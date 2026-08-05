/**
 * Fetches an asset's image bytes and hands back a `blob:` URL for `<img>`.
 *
 * A plain `<img src="/projects/.../assets/...">` can't carry the engine's
 * bearer token, so the bytes have to be fetched through authenticated
 * `fetch()` first (see `lib/engine.ts`'s `getEngineInfo`) and turned into an
 * object URL.
 */
import { useEffect, useState } from "react";
import { getEngineInfo } from "@/lib/engine";

export function useAssetImage(projectId: string | undefined, assetId: string | null | undefined) {
  const [url, setUrl] = useState<string | null>(null);

  useEffect(() => {
    if (!projectId || !assetId) {
      setUrl(null);
      return;
    }
    let objectUrl: string | null = null;
    let cancelled = false;

    (async () => {
      const info = await getEngineInfo();
      const resp = await fetch(
        `${info.base_url}/projects/${projectId}/assets/${assetId}`,
        { headers: { Authorization: `Bearer ${info.token}` } },
      );
      if (!resp.ok || cancelled) return;
      const blob = await resp.blob();
      if (cancelled) return;
      objectUrl = URL.createObjectURL(blob);
      setUrl(objectUrl);
    })().catch(() => setUrl(null));

    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [projectId, assetId]);

  return url;
}
