/**
 * Subscribes to a project's SSE progress stream
 * (`GET /projects/{id}/events`, see api/routes/events.py).
 *
 * The browser's native `EventSource` cannot carry an Authorization header, and
 * the engine's auth middleware only checks that header — so this reads the
 * `text/event-stream` body directly off an authenticated `fetch` instead,
 * with a small incremental parser rather than pulling in a client library for
 * one endpoint.
 */
import { useEffect, useRef, useState } from "react";
import { getEngineInfo } from "@/lib/engine";

export interface ProjectEvent {
  stage: string;
  percent?: number;
  error?: string;
  [key: string]: unknown;
}

export function useProjectEvents(projectId: string | undefined, enabled = true) {
  const [lastEvent, setLastEvent] = useState<ProjectEvent | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    if (!projectId || !enabled) return;

    const controller = new AbortController();
    abortRef.current = controller;

    (async () => {
      const info = await getEngineInfo();
      let resp: Response;
      try {
        resp = await fetch(`${info.base_url}/projects/${projectId}/events`, {
          headers: { Authorization: `Bearer ${info.token}` },
          signal: controller.signal,
        });
      } catch {
        return; // aborted or the engine went away; nothing to report
      }
      if (!resp.body) return;

      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });

        // SSE frames are separated by a blank line; each frame's payload is
        // the concatenation of its "data: " lines.
        const frames = buffer.split("\n\n");
        buffer = frames.pop() ?? "";
        for (const frame of frames) {
          const dataLines = frame
            .split("\n")
            .filter((l) => l.startsWith("data:"))
            .map((l) => l.slice(5).trimStart());
          if (dataLines.length === 0) continue;
          try {
            setLastEvent(JSON.parse(dataLines.join("\n")));
          } catch {
            /* ignore malformed frame */
          }
        }
      }
    })();

    return () => controller.abort();
  }, [projectId, enabled]);

  return lastEvent;
}
