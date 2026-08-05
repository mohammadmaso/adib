mod keychain;
mod sidecar;

use sidecar::EngineState;

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(
            tauri_plugin_log::Builder::new()
                .level(log::LevelFilter::Info)
                // reqwest/hyper emit per-request TRACE noise that buries engine logs.
                .level_for("reqwest", log::LevelFilter::Warn)
                .level_for("hyper_util", log::LevelFilter::Warn)
                .build(),
        )
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_dialog::init())
        .manage(EngineState::new())
        .invoke_handler(tauri::generate_handler![
            sidecar::engine_info,
            keychain::get_api_key,
            keychain::set_api_key,
            keychain::clear_api_key,
            keychain::get_image_api_key,
            keychain::set_image_api_key,
            keychain::clear_image_api_key,
        ])
        .setup(|app| {
            sidecar::init(app.handle());
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building tauri application")
        .run(|app, event| sidecar::on_run_event(app, &event));
}
