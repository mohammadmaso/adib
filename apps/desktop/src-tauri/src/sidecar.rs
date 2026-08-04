//! Lifecycle for the `adib-engine` Python sidecar.
//!
//! The engine binds an ephemeral port itself and announces it on stdout, so there
//! is no race between "pick a free port" and "bind it". We read that line, then
//! hand the resulting base URL plus a per-run shared secret to the webview, which
//! talks to the engine over plain HTTP.
//!
//! In development, set `ADIB_ENGINE_URL` (and optionally `ADIB_ENGINE_TOKEN`) to
//! attach to an engine you started yourself with `uv run adib-engine`, and no
//! sidecar is spawned.

use std::sync::Mutex;
use std::time::Duration;

use serde::{Deserialize, Serialize};
use tauri::{AppHandle, Manager, RunEvent};
use tauri_plugin_shell::process::{CommandChild, CommandEvent};
use tauri_plugin_shell::ShellExt;

/// Must match `READY_PREFIX` in engine/adib_engine/__main__.py.
const READY_PREFIX: &str = "ADIB_ENGINE_READY ";

/// How long to wait for the engine to announce its port before giving up.
const STARTUP_TIMEOUT: Duration = Duration::from_secs(60);

/// Interval between liveness pings. The engine exits on its own if these stop,
/// which is what prevents orphaned sidecars when the shell is force-killed.
const HEARTBEAT_INTERVAL: Duration = Duration::from_secs(15);

/// Must exceed HEARTBEAT_INTERVAL by enough to tolerate a slow or busy engine.
const HEARTBEAT_TIMEOUT_SECS: &str = "60";

#[derive(Debug, Deserialize)]
struct ReadyLine {
    host: String,
    port: u16,
}

/// What the webview needs in order to call the engine.
#[derive(Debug, Clone, Serialize)]
pub struct EngineInfo {
    pub base_url: String,
    pub token: String,
}

pub struct EngineState {
    info: Mutex<Option<EngineInfo>>,
    child: Mutex<Option<CommandChild>>,
}

impl EngineState {
    pub fn new() -> Self {
        Self {
            info: Mutex::new(None),
            child: Mutex::new(None),
        }
    }

    /// Kill the sidecar. Safe to call more than once.
    pub fn shutdown(&self) {
        if let Some(child) = self.child.lock().expect("engine child lock").take() {
            let _ = child.kill();
        }
    }
}

/// A per-run secret so that another local process cannot drive the engine.
fn generate_token() -> String {
    use std::collections::hash_map::RandomState;
    use std::hash::{BuildHasher, Hasher};
    // Two independently seeded hashers give 128 bits of ASLR/OS-seeded entropy
    // without pulling in a crypto RNG for what is a localhost-only guard.
    let a = RandomState::new().build_hasher().finish();
    let b = RandomState::new().build_hasher().finish();
    format!("{a:016x}{b:016x}")
}

/// Spawn the engine and block until it reports its port.
pub async fn start(app: &AppHandle) -> Result<EngineInfo, String> {
    if let Ok(base_url) = std::env::var("ADIB_ENGINE_URL") {
        let token = std::env::var("ADIB_ENGINE_TOKEN").unwrap_or_default();
        log::info!("attaching to external engine at {base_url}");
        return Ok(EngineInfo {
            base_url: base_url.trim_end_matches('/').to_string(),
            token,
        });
    }

    let token = generate_token();
    let (mut rx, child) = app
        .shell()
        .sidecar("adib-engine")
        .map_err(|e| format!("adib-engine sidecar not found: {e}"))?
        .args([
            "--port",
            "0",
            "--auth-token",
            &token,
            "--heartbeat-timeout",
            HEARTBEAT_TIMEOUT_SECS,
        ])
        .spawn()
        .map_err(|e| format!("failed to spawn adib-engine: {e}"))?;

    app.state::<EngineState>()
        .child
        .lock()
        .expect("engine child lock")
        .replace(child);

    let ready = tokio::time::timeout(STARTUP_TIMEOUT, async {
        while let Some(event) = rx.recv().await {
            match event {
                CommandEvent::Stdout(bytes) => {
                    let line = String::from_utf8_lossy(&bytes);
                    if let Some(json) = line.trim().strip_prefix(READY_PREFIX) {
                        return serde_json::from_str::<ReadyLine>(json)
                            .map_err(|e| format!("malformed ready line {json:?}: {e}"));
                    }
                }
                // The engine logs to stderr; mirror it so engine failures are
                // visible in the shell's console rather than silently swallowed.
                CommandEvent::Stderr(bytes) => {
                    log::info!("[engine] {}", String::from_utf8_lossy(&bytes).trim_end());
                }
                CommandEvent::Terminated(payload) => {
                    return Err(format!("engine exited during startup: {payload:?}"));
                }
                _ => {}
            }
        }
        Err("engine stdout closed before it reported a port".to_string())
    })
    .await
    .map_err(|_| format!("engine did not start within {STARTUP_TIMEOUT:?}"))??;

    // Keep draining the channel after startup, otherwise the engine blocks once
    // the pipe buffer fills and its logs are lost.
    tauri::async_runtime::spawn(async move {
        while let Some(event) = rx.recv().await {
            match event {
                CommandEvent::Stderr(bytes) | CommandEvent::Stdout(bytes) => {
                    log::info!("[engine] {}", String::from_utf8_lossy(&bytes).trim_end());
                }
                CommandEvent::Terminated(payload) => {
                    log::warn!("engine terminated: {payload:?}");
                    break;
                }
                _ => {}
            }
        }
    });

    Ok(EngineInfo {
        base_url: format!("http://{}:{}", ready.host, ready.port),
        token,
    })
}

/// Ping `/health` forever so the engine's watchdog knows the shell is alive.
fn spawn_heartbeat(info: EngineInfo) {
    tauri::async_runtime::spawn(async move {
        // no_proxy is essential: the engine is on loopback, but reqwest honours
        // HTTP_PROXY/system proxy settings and will happily route 127.0.0.1
        // through them. A proxy that is down would then fail every heartbeat and
        // the engine's watchdog would kill a perfectly healthy session.
        let client = match reqwest::Client::builder().no_proxy().build() {
            Ok(c) => c,
            Err(e) => {
                log::error!("could not build heartbeat client: {e}");
                return;
            }
        };
        let url = format!("{}/health", info.base_url);
        loop {
            tokio::time::sleep(HEARTBEAT_INTERVAL).await;
            if let Err(e) = client.get(&url).send().await {
                log::warn!("engine heartbeat failed: {e}");
            }
        }
    });
}

pub fn init(app: &AppHandle) {
    let app = app.clone();
    tauri::async_runtime::spawn(async move {
        match start(&app).await {
            Ok(info) => {
                log::info!("engine ready at {}", info.base_url);
                app.state::<EngineState>()
                    .info
                    .lock()
                    .expect("engine info lock")
                    .replace(info.clone());
                spawn_heartbeat(info);
            }
            Err(e) => log::error!("engine failed to start: {e}"),
        }
    });
}

/// Called by the frontend to learn where the engine lives.
/// Returns `None` while the engine is still coming up.
#[tauri::command]
pub fn engine_info(state: tauri::State<'_, EngineState>) -> Option<EngineInfo> {
    state.info.lock().expect("engine info lock").clone()
}

/// Kill the sidecar when the last window closes or the process exits.
pub fn on_run_event(app: &AppHandle, event: &RunEvent) {
    if matches!(event, RunEvent::Exit) {
        app.state::<EngineState>().shutdown();
    }
}
