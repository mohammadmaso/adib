/**
 * Client for the adib-engine sidecar.
 *
 * The Rust shell spawns the engine, learns its ephemeral port, and mints a
 * per-run token. We ask for those once, then talk plain HTTP. Every request
 * carries the token; the engine rejects anything else with a 401.
 */
import { invoke } from "@tauri-apps/api/core";

export interface EngineInfo {
  base_url: string;
  token: string;
}

export interface Health {
  status: string;
  version: string;
  pid: number;
}

export class EngineError extends Error {
  constructor(
    message: string,
    readonly status?: number,
  ) {
    super(message);
    this.name = "EngineError";
  }
}

let cached: EngineInfo | null = null;

/**
 * Resolve the engine's address, polling until the sidecar finishes starting.
 * `engine_info` returns null while the handshake is still in flight.
 */
export async function getEngineInfo(timeoutMs = 60_000): Promise<EngineInfo> {
  if (cached) return cached;

  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const info = await invoke<EngineInfo | null>("engine_info");
    if (info) {
      cached = info;
      return info;
    }
    await new Promise((r) => setTimeout(r, 200));
  }
  throw new EngineError("engine did not become ready in time");
}

/** Typed fetch against the engine. `path` is relative, e.g. "/projects". */
export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const info = await getEngineInfo();

  const headers = new Headers(init.headers);
  headers.set("Authorization", `Bearer ${info.token}`);
  if (init.body !== undefined && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  const resp = await fetch(`${info.base_url}${path}`, { ...init, headers });

  if (!resp.ok) {
    // FastAPI puts the useful message in `detail`; fall back to the raw body.
    let detail = resp.statusText;
    try {
      const body = await resp.json();
      detail = body?.detail ?? detail;
    } catch {
      /* non-JSON error body */
    }
    throw new EngineError(`${path} failed: ${detail}`, resp.status);
  }

  return resp.status === 204 ? (undefined as T) : ((await resp.json()) as T);
}

export const getHealth = () => api<Health>("/health");
