import { useEffect, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CircleAlert, Loader, Pause, Sparkles } from "lucide-react";
import { toast } from "sonner";
import { api, type components } from "@/lib/api-client";
import { getApiKey } from "@/lib/keychain";
import { useProjectEvents } from "@/hooks/use-project-events";
import { SegmentRow } from "@/components/gate3/segment-row";
import { ExportPanel } from "@/components/gate3/export-panel";
import { CoverPanel, CoverTranslateFailedNotice } from "@/components/gate3/cover-panel";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Progress } from "@/components/ui/progress";

type SegmentStatus = components["schemas"]["SegmentStatus"];

const FILTERS: { label: string; value: SegmentStatus | "all" }[] = [
  { label: "All", value: "all" },
  { label: "Flagged", value: "flagged" },
  { label: "Pending", value: "pending" },
  { label: "Failed", value: "failed" },
  { label: "Approved", value: "approved" },
];

export default function Gate3Route() {
  const { projectId } = useParams();
  const queryClient = useQueryClient();
  const translateTriggered = useRef(false);
  const [filter, setFilter] = useState<SegmentStatus | "all">("all");
  const [retranslating, setRetranslating] = useState<Set<string>>(new Set());
  const [outputs, setOutputs] = useState<Record<string, string> | null>(null);
  const [coverTranslating, setCoverTranslating] = useState(false);
  const [coverFailedReason, setCoverFailedReason] = useState<string | null>(null);

  const projectQuery = useQuery({
    queryKey: ["project", projectId],
    enabled: !!projectId,
    queryFn: async () => {
      const { data, error } = await api.GET("/projects/{project_id}", {
        params: { path: { project_id: projectId! } },
      });
      if (error) throw error;
      return data;
    },
    refetchInterval: (query) => {
      const s = query.state.data?.stage;
      return s === "translating" || s === "exporting" ? 1500 : false;
    },
  });

  const stage = projectQuery.data?.stage;
  const failedStage = projectQuery.data?.failed_stage;
  const isTranslating = stage === "translating";
  const isExporting = stage === "exporting";
  const isTranslateFailure = stage === "failed" && failedStage === "translating";
  //  Segments already translated in a prior run (or earlier in this run, since
  //  translation commits per-segment) should stay visible while translation is
  //  in progress rather than being hidden behind a full-screen spinner.
  const hasSegments = stage != null && !isTranslateFailure;

  const event = useProjectEvents(
    projectId,
    isTranslating || isExporting || retranslating.size > 0 || coverTranslating,
  );

  const countsQuery = useQuery({
    queryKey: ["segment-counts", projectId],
    enabled: !!projectId && hasSegments,
    queryFn: async () => {
      const { data, error } = await api.GET("/projects/{project_id}/segments/counts", {
        params: { path: { project_id: projectId! } },
      });
      if (error) throw error;
      return data;
    },
  });

  const segmentsQuery = useQuery({
    queryKey: ["segments", projectId, filter],
    enabled: !!projectId && hasSegments,
    queryFn: async () => {
      const { data, error } = await api.GET("/projects/{project_id}/segments", {
        params: {
          path: { project_id: projectId! },
          query: { status: filter === "all" ? undefined : filter, limit: 300, offset: 0 },
        },
      });
      if (error) throw error;
      return data;
    },
  });

  const translateMutation = useMutation({
    mutationFn: async () => {
      if (!projectId) throw new Error("no project");
      const api_key = (await getApiKey().catch(() => null)) ?? null;
      const { error } = await api.POST("/projects/{project_id}/translate", {
        params: { path: { project_id: projectId } },
        body: { api_key },
      });
      if (error) throw error;
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["project", projectId] }),
    onError: (error: unknown) => {
      toast.error(error instanceof Error ? error.message : "Failed to start translation");
    },
  });

  const pauseMutation = useMutation({
    mutationFn: async () => {
      if (!projectId) throw new Error("no project");
      const { error } = await api.POST("/projects/{project_id}/translate/pause", {
        params: { path: { project_id: projectId } },
      });
      if (error) throw error;
    },
    //  The engine stops picking up work at the next batch boundary rather than
    //  killing requests in flight, so the stage flips a moment later — say so
    //  instead of leaving the button looking unresponsive.
    onSuccess: () => toast.info("Pausing — finishing the requests already in flight…"),
    onError: (error: unknown) => {
      toast.error(error instanceof Error ? error.message : "Failed to pause translation");
    },
  });

  const pauseRequested = pauseMutation.isPending || pauseMutation.isSuccess;

  useEffect(() => {
    if (isTranslating && !translateTriggered.current) {
      translateTriggered.current = true;
      translateMutation.mutate();
      pauseMutation.reset(); // a new run is pausable again
    }
    if (!isTranslating) translateTriggered.current = false;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isTranslating]);

  useEffect(() => {
    if (!event) return;
    if (event.stage === "review" || event.stage === "failed" || event.stage === "paused") {
      queryClient.invalidateQueries({ queryKey: ["project", projectId] });
      queryClient.invalidateQueries({ queryKey: ["segments", projectId] });
      queryClient.invalidateQueries({ queryKey: ["segment-counts", projectId] });
    }
    if (event.stage === "translating" && typeof event.segment_id === "string") {
      queryClient.invalidateQueries({ queryKey: ["segment-counts", projectId] });
    }
    if (event.stage === "segment_retranslated" && typeof event.segment_id === "string") {
      setRetranslating((prev) => {
        const next = new Set(prev);
        next.delete(event.segment_id as string);
        return next;
      });
      queryClient.invalidateQueries({ queryKey: ["segments", projectId] });
      queryClient.invalidateQueries({ queryKey: ["segment-counts", projectId] });
    }
    if (event.stage === "segment_retranslate_failed" && typeof event.segment_id === "string") {
      setRetranslating((prev) => {
        const next = new Set(prev);
        next.delete(event.segment_id as string);
        return next;
      });
      toast.error("Retranslation failed");
    }
    if (event.stage === "cover_translating") {
      setCoverTranslating(true);
      setCoverFailedReason(null);
    }
    if (event.stage === "cover_translated") {
      setCoverTranslating(false);
      queryClient.invalidateQueries({ queryKey: ["cover", projectId] });
    }
    if (event.stage === "cover_translate_failed") {
      setCoverTranslating(false);
      setCoverFailedReason(
        typeof event.error === "string" ? event.error : "Cover translation failed",
      );
    }
    if (event.stage === "done") {
      queryClient.invalidateQueries({ queryKey: ["project", projectId] });
      if (event.outputs && typeof event.outputs === "object") {
        setOutputs(event.outputs as Record<string, string>);
      }
      toast.success("Export complete");
    }
  }, [event, projectId, queryClient]);

  const saveMutation = useMutation({
    mutationFn: async ({ id, patch }: { id: string; patch: components["schemas"]["SegmentUpdate"] }) => {
      const { error } = await api.PATCH("/projects/{project_id}/segments/{segment_id}", {
        params: { path: { project_id: projectId!, segment_id: id } },
        body: patch,
      });
      if (error) throw error;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["segments", projectId] });
      queryClient.invalidateQueries({ queryKey: ["segment-counts", projectId] });
    },
    onError: (error: unknown) => {
      toast.error(error instanceof Error ? error.message : "Failed to save segment");
    },
  });

  const retranslateMutation = useMutation({
    mutationFn: async (segmentId: string) => {
      if (!projectId) throw new Error("no project");
      const api_key = (await getApiKey().catch(() => null)) ?? null;
      const { error } = await api.POST("/projects/{project_id}/segments/{segment_id}/retranslate", {
        params: { path: { project_id: projectId, segment_id: segmentId } },
        body: { api_key },
      });
      if (error) throw error;
    },
    onMutate: (segmentId) => {
      setRetranslating((prev) => new Set(prev).add(segmentId));
    },
    onError: (error: unknown, segmentId) => {
      setRetranslating((prev) => {
        const next = new Set(prev);
        next.delete(segmentId);
        return next;
      });
      toast.error(error instanceof Error ? error.message : "Failed to retranslate");
    },
  });

  const exportMutation = useMutation({
    mutationFn: async (request: components["schemas"]["ExportRequest"]) => {
      if (!projectId) throw new Error("no project");
      const { error } = await api.POST("/projects/{project_id}/export", {
        params: { path: { project_id: projectId } },
        body: request,
      });
      if (error) throw error;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["project", projectId] });
    },
    onError: (error: unknown) => {
      // A rejected destination (unwritable folder, bad path) comes back as the
      // engine's own detail string; that is the useful thing to show.
      const detail =
        typeof error === "object" && error && "detail" in error
          ? String((error as { detail: unknown }).detail)
          : error instanceof Error
            ? error.message
            : "Failed to start export";
      toast.error(detail);
    },
  });

  if (projectQuery.isLoading) {
    return (
      <div className="grid h-full place-items-center">
        <Loader className="size-6 animate-spin text-muted-foreground" aria-hidden />
      </div>
    );
  }

  if (isTranslateFailure) {
    const reason =
      projectQuery.data?.failed_reason ??
      (event?.stage === "failed" && typeof event.error === "string" ? event.error : null) ??
      "The translation run failed.";
    return (
      <div className="p-8">
        <div className="flex items-start gap-2 rounded-lg border border-destructive/30 bg-destructive/5 p-4">
          <CircleAlert className="mt-0.5 size-4 shrink-0 text-destructive" aria-hidden />
          <div className="flex-1 space-y-3">
            <div>
              <p className="font-medium text-destructive">Translation failed</p>
              <p className="mt-1 text-sm text-muted-foreground">{reason}</p>
            </div>
            <Button
              size="sm"
              variant="outline"
              disabled={translateMutation.isPending}
              onClick={() => translateMutation.mutate()}
            >
              {translateMutation.isPending && <Loader className="size-3.5 animate-spin" aria-hidden />}
              Retry translation
            </Button>
          </div>
        </div>
      </div>
    );
  }

  if (!hasSegments) {
    return (
      <div className="grid h-full place-items-center">
        <Loader className="size-6 animate-spin text-muted-foreground" aria-hidden />
      </div>
    );
  }

  const segments = segmentsQuery.data ?? [];
  const counts = countsQuery.data ?? {};
  const totalCount = Object.values(counts).reduce((a, b) => a + b, 0);

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center justify-between border-b border-border px-8 py-4">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">Review &amp; Export</h1>
          <p className="mt-0.5 text-sm text-muted-foreground">{totalCount} segments</p>
        </div>
        <div className="flex items-center gap-1.5">
          {FILTERS.map((f) => (
            <Button
              key={f.value}
              variant={filter === f.value ? "secondary" : "ghost"}
              size="sm"
              onClick={() => setFilter(f.value)}
            >
              {f.label}
              {f.value !== "all" && counts[f.value] != null && (
                <Badge variant="outline" className="ml-1 text-[10px]">
                  {counts[f.value]}
                </Badge>
              )}
            </Button>
          ))}
        </div>
      </div>

      {isTranslating && (
        <div className="flex items-center gap-3 border-b border-border bg-muted/40 px-8 py-3">
          <Sparkles className="size-4 shrink-0 animate-pulse text-muted-foreground" aria-hidden />
          <div className="flex-1 space-y-1">
            <p className="text-sm font-medium">
              Translating… {typeof event?.percent === "number" ? `${event.percent}%` : ""}
            </p>
            <Progress value={typeof event?.percent === "number" ? event.percent : 0} />
          </div>
          <Button
            variant="outline"
            size="sm"
            disabled={pauseRequested}
            onClick={() => pauseMutation.mutate()}
          >
            {pauseRequested ? (
              <Loader className="size-3.5 animate-spin" aria-hidden />
            ) : (
              <Pause className="size-3.5" aria-hidden />
            )}
            {pauseRequested ? "Pausing…" : "Pause"}
          </Button>
        </div>
      )}

      {stage === "paused" && (
        <div className="flex items-center gap-3 border-b border-border bg-muted/40 px-8 py-3">
          <Pause className="size-4 shrink-0 text-muted-foreground" aria-hidden />
          <p className="flex-1 text-sm font-medium">Translation paused</p>
          <Button
            size="sm"
            disabled={translateMutation.isPending}
            onClick={() => translateMutation.mutate()}
          >
            {translateMutation.isPending && <Loader className="size-3.5 animate-spin" aria-hidden />}
            Resume
          </Button>
        </div>
      )}

      {coverFailedReason && (
        <div className="border-b border-border px-8 py-3">
          <CoverTranslateFailedNotice message={coverFailedReason} />
        </div>
      )}
      <CoverPanel
        projectId={projectId!}
        translating={coverTranslating}
        onTranslatingChange={setCoverTranslating}
      />

      <div className="flex-1 overflow-y-auto">
        {segmentsQuery.isLoading && (
          <div className="grid h-32 place-items-center">
            <Loader className="size-5 animate-spin text-muted-foreground" aria-hidden />
          </div>
        )}
        {segments.length === 0 && !segmentsQuery.isLoading && (
          <p className="p-8 text-center text-sm text-muted-foreground">No segments match this filter.</p>
        )}
        {segments.map((segment) => (
          <SegmentRow
            key={segment.id}
            segment={segment}
            retranslating={retranslating.has(segment.id)}
            onSave={(target_text) => saveMutation.mutate({ id: segment.id, patch: { target_text } })}
            onToggleLock={() =>
              saveMutation.mutate({ id: segment.id, patch: { locked: !segment.locked } })
            }
            onApprove={() =>
              saveMutation.mutate({
                id: segment.id,
                patch: { status: segment.status === "approved" ? "translated" : "approved" },
              })
            }
            onRetranslate={() => retranslateMutation.mutate(segment.id)}
          />
        ))}
      </div>

      <div className="border-t border-border px-8 py-4">
        <ExportPanel
          projectId={projectId!}
          isExporting={isExporting}
          percent={
            isExporting && typeof event?.percent === "number" ? event.percent : null
          }
          outputs={outputs}
          failedReason={
            stage === "failed" && failedStage === "exporting"
              ? projectQuery.data?.failed_reason ?? "The last export attempt failed."
              : null
          }
          starting={exportMutation.isPending}
          onExport={(request) => {
            setOutputs(null);
            exportMutation.mutate(request);
          }}
        />
      </div>
    </div>
  );
}
