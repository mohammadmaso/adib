import { useEffect, useState, type ReactNode } from "react";
import { BookOpen, CircleAlert, Loader } from "lucide-react";
import { getEngineInfo } from "@/lib/engine";

type EngineStatus = { state: "connecting" } | { state: "ready" } | { state: "failed"; error: string };

/**
 * Blocks rendering the app until the sidecar handshake completes. Every
 * screen depends on the engine being reachable, so this is simpler than
 * threading a loading state through each one individually.
 */
export function EngineGate({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<EngineStatus>({ state: "connecting" });

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        await getEngineInfo();
        if (!cancelled) setStatus({ state: "ready" });
      } catch (e) {
        if (!cancelled) setStatus({ state: "failed", error: (e as Error).message });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  if (status.state === "ready") return <>{children}</>;

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
