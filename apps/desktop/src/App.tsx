import { useEffect, useState } from "react";
import { BookOpen, CircleAlert, CircleCheck, Loader } from "lucide-react";
import { getEngineInfo, getHealth, type Health } from "@/lib/engine";

type EngineStatus =
  | { state: "connecting" }
  | { state: "ready"; health: Health; baseUrl: string }
  | { state: "failed"; error: string };

/**
 * Placeholder shell. Its only job today is to prove the sidecar handshake:
 * spawn the engine, discover its port, and make an authenticated call.
 * The real screens (Library, New Project, the three gates) replace this.
 */
export default function App() {
  const [status, setStatus] = useState<EngineStatus>({ state: "connecting" });

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const info = await getEngineInfo();
        const health = await getHealth();
        if (!cancelled) setStatus({ state: "ready", health, baseUrl: info.base_url });
      } catch (e) {
        if (!cancelled) setStatus({ state: "failed", error: (e as Error).message });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <main className="grid h-full place-items-center bg-neutral-50 p-8 text-neutral-900 dark:bg-neutral-950 dark:text-neutral-100">
      <div className="w-full max-w-md space-y-6 text-center">
        <div className="flex flex-col items-center gap-3">
          <BookOpen className="size-10 text-neutral-400" aria-hidden />
          <h1 className="text-2xl font-semibold tracking-tight">Adib</h1>
          <p className="text-sm text-neutral-500">Professional book translation</p>
        </div>

        <div className="rounded-lg border border-neutral-200 bg-white p-4 text-left text-sm dark:border-neutral-800 dark:bg-neutral-900">
          {status.state === "connecting" && (
            <p className="flex items-center gap-2 text-neutral-500">
              <Loader className="size-4 animate-spin" aria-hidden />
              Starting translation engine…
            </p>
          )}

          {status.state === "ready" && (
            <div className="space-y-2">
              <p className="flex items-center gap-2 font-medium text-emerald-600 dark:text-emerald-400">
                <CircleCheck className="size-4" aria-hidden />
                Engine ready
              </p>
              <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 font-mono text-xs text-neutral-500">
                <dt>version</dt>
                <dd>{status.health.version}</dd>
                <dt>pid</dt>
                <dd>{status.health.pid}</dd>
                <dt>url</dt>
                <dd className="truncate">{status.baseUrl}</dd>
              </dl>
            </div>
          )}

          {status.state === "failed" && (
            <div className="space-y-1">
              <p className="flex items-center gap-2 font-medium text-red-600 dark:text-red-400">
                <CircleAlert className="size-4" aria-hidden />
                Engine failed to start
              </p>
              <p className="font-mono text-xs break-words text-neutral-500">{status.error}</p>
            </div>
          )}
        </div>
      </div>
    </main>
  );
}
