/**
 * Typed API client over the engine's OpenAPI contract (`@adib/schema`,
 * regenerated from the engine's Pydantic models by
 * `scripts/generate-types.sh` — never hand-edit it).
 *
 * `openapi-fetch`'s base URL and auth header are both only knowable after the
 * async sidecar handshake (`getEngineInfo`), so both are injected by a
 * middleware that resolves them lazily on the first request rather than at
 * client-construction time.
 */
import createClient from "openapi-fetch";
import type { paths } from "@adib/schema";
import { EngineError, getEngineInfo } from "@/lib/engine";

export const api = createClient<paths>({ baseUrl: "" });

api.use({
  async onRequest({ request }) {
    const info = await getEngineInfo();
    const url = new URL(request.url, info.base_url);
    const headers = new Headers(request.headers);
    headers.set("Authorization", `Bearer ${info.token}`);
    // Passing `request` itself as init (`new Request(url, request)`) re-pipes
    // its body as a ReadableStream, which WKWebView (Tauri's macOS webview)
    // rejects with "ReadableStream uploading is not supported." Reading the
    // body into a buffer first avoids the stream entirely.
    const body = request.body ? await request.clone().arrayBuffer() : undefined;
    return new Request(`${info.base_url}${url.pathname}${url.search}`, {
      method: request.method,
      headers,
      body,
    });
  },
  async onResponse({ response }) {
    if (!response.ok) {
      let detail = response.statusText;
      try {
        const body = await response.clone().json();
        detail = body?.detail ?? detail;
      } catch {
        /* non-JSON error body */
      }
      throw new EngineError(`${response.url} failed: ${detail}`, response.status);
    }
    return response;
  },
});

export type { components, operations, paths } from "@adib/schema";
