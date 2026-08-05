import { useMutation, useQuery } from "@tanstack/react-query";
import { CircleAlert, ImageIcon, Loader, Sparkles } from "lucide-react";
import { toast } from "sonner";
import { api } from "@/lib/api-client";
import { useAssetImage } from "@/lib/asset-image";
import { getImageApiKey } from "@/lib/keychain";
import { Button } from "@/components/ui/button";

interface CoverPanelProps {
  projectId: string;
  /** True while a `cover_translating` event is in flight for this project. */
  translating: boolean;
  onTranslatingChange: (translating: boolean) => void;
}

export function CoverPanel({ projectId, translating, onTranslatingChange }: CoverPanelProps) {
  const coverQuery = useQuery({
    queryKey: ["cover", projectId],
    enabled: !!projectId,
    queryFn: async () => {
      const { data, error } = await api.GET("/projects/{project_id}/cover", {
        params: { path: { project_id: projectId } },
      });
      if (error) throw error;
      return data;
    },
  });

  const cover = coverQuery.data;
  const sourceUrl = useAssetImage(projectId, cover?.source_asset_id);
  const translatedUrl = useAssetImage(projectId, cover?.translated_asset_id);

  const translateMutation = useMutation({
    mutationFn: async () => {
      const api_key = (await getImageApiKey().catch(() => null)) ?? null;
      const { error } = await api.POST("/projects/{project_id}/cover/translate", {
        params: { path: { project_id: projectId } },
        body: { api_key },
      });
      if (error) throw error;
    },
    onMutate: () => onTranslatingChange(true),
    onError: (error: unknown) => {
      onTranslatingChange(false);
      toast.error(error instanceof Error ? error.message : "Failed to start cover translation");
    },
  });

  if (!cover?.has_source_cover) return null;

  return (
    <div className="space-y-3 border-b border-border px-8 py-4">
      <div className="flex items-center gap-2">
        <ImageIcon className="size-4 text-muted-foreground" aria-hidden />
        <h2 className="text-sm font-medium">Cover</h2>
      </div>
      <div className="flex flex-wrap items-start gap-6">
        <div className="space-y-1.5">
          <p className="text-xs text-muted-foreground">Source</p>
          {sourceUrl ? (
            <img src={sourceUrl} alt="Source cover" className="h-48 w-auto rounded-md border border-border" />
          ) : (
            <div className="grid h-48 w-32 place-items-center rounded-md border border-border bg-muted/40">
              <Loader className="size-4 animate-spin text-muted-foreground" aria-hidden />
            </div>
          )}
        </div>

        <div className="space-y-1.5">
          <p className="text-xs text-muted-foreground">Translated</p>
          {translating ? (
            <div className="grid h-48 w-32 place-items-center rounded-md border border-border bg-muted/40">
              <Sparkles className="size-4 animate-pulse text-muted-foreground" aria-hidden />
            </div>
          ) : translatedUrl ? (
            <img
              src={translatedUrl}
              alt="Translated cover"
              className="h-48 w-auto rounded-md border border-border"
            />
          ) : (
            <div className="grid h-48 w-32 place-items-center rounded-md border border-dashed border-border text-center">
              <Button
                size="sm"
                variant="outline"
                disabled={translateMutation.isPending}
                onClick={() => translateMutation.mutate()}
              >
                {translateMutation.isPending && (
                  <Loader className="size-3.5 animate-spin" aria-hidden />
                )}
                Translate cover
              </Button>
            </div>
          )}
        </div>
      </div>

      {cover.translated_asset_id && !translating && (
        <Button
          size="sm"
          variant="ghost"
          disabled={translateMutation.isPending}
          onClick={() => translateMutation.mutate()}
        >
          Retranslate cover
        </Button>
      )}
    </div>
  );
}

export function CoverTranslateFailedNotice({ message }: { message: string }) {
  return (
    <div className="flex items-start gap-2 rounded-lg border border-destructive/30 bg-destructive/5 px-3 py-2 text-sm text-destructive">
      <CircleAlert className="mt-0.5 size-3.5 shrink-0" aria-hidden />
      <span>{message}</span>
    </div>
  );
}
