import { useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CircleAlert, Loader, Sparkles } from "lucide-react";
import { toast } from "sonner";
import { api, type components } from "@/lib/api-client";
import { getApiKey } from "@/lib/keychain";
import { useProjectEvents } from "@/hooks/use-project-events";
import { AnalysisPanel } from "@/components/gate2/analysis-panel";
import { GlossaryTable } from "@/components/gate2/glossary-table";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

type BookAnalysis = components["schemas"]["BookAnalysis"];

export default function Gate2Route() {
  const { projectId } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [analysis, setAnalysis] = useState<BookAnalysis | null>(null);
  const [dirty, setDirty] = useState(false);
  const [presetId, setPresetId] = useState<string | null>(null);
  const analyzeTriggered = useRef(false);

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
    refetchInterval: (query) => (query.state.data?.stage === "analyzing" ? 1500 : false),
  });

  const stage = projectQuery.data?.stage;
  const isAnalyzing = stage === "analyzing";
  const hasAnalysis = stage === "style_review" || stage === "translating" || stage === "review" || stage === "exporting" || stage === "done";

  const analysisQuery = useQuery({
    queryKey: ["analysis", projectId],
    enabled: !!projectId && hasAnalysis,
    retry: false,
    queryFn: async () => {
      const { data, error } = await api.GET("/projects/{project_id}/analysis", {
        params: { path: { project_id: projectId! } },
      });
      if (error) throw error;
      return data;
    },
  });

  useEffect(() => {
    if (analysisQuery.data && !analysis) {
      setAnalysis(analysisQuery.data);
      setPresetId(analysisQuery.data.suggested_preset);
    }
  }, [analysisQuery.data, analysis]);

  const presetsQuery = useQuery({
    queryKey: ["presets"],
    queryFn: async () => {
      const { data, error } = await api.GET("/presets");
      if (error) throw error;
      return data;
    },
  });

  const glossaryQuery = useQuery({
    queryKey: ["glossary", projectId],
    enabled: !!projectId && hasAnalysis,
    queryFn: async () => {
      const { data, error } = await api.GET("/projects/{project_id}/glossary", {
        params: { path: { project_id: projectId! }, query: {} },
      });
      if (error) throw error;
      return data;
    },
  });

  const event = useProjectEvents(projectId, isAnalyzing || glossaryQuery.isFetching);
  useEffect(() => {
    if (!event) return;
    if (event.stage === "style_review" || event.stage === "failed") {
      queryClient.invalidateQueries({ queryKey: ["project", projectId] });
    }
    if (event.stage === "glossary_ready") {
      queryClient.invalidateQueries({ queryKey: ["glossary", projectId] });
      toast.success(`Glossary ready · ${event.added ?? 0} added, ${event.updated ?? 0} updated`);
    }
    if (event.stage === "glossary_failed") {
      toast.error(typeof event.error === "string" ? event.error : "Glossary build failed");
    }
  }, [event, projectId, queryClient]);

  const analyzeMutation = useMutation({
    mutationFn: async () => {
      if (!projectId) throw new Error("no project");
      const api_key = (await getApiKey().catch(() => null)) ?? null;
      const { error } = await api.POST("/projects/{project_id}/analyze", {
        params: { path: { project_id: projectId } },
        body: { api_key },
      });
      if (error) throw error;
    },
    onError: (error: unknown) => {
      toast.error(error instanceof Error ? error.message : "Failed to start analysis");
    },
  });

  useEffect(() => {
    if (isAnalyzing && !analyzeTriggered.current) {
      analyzeTriggered.current = true;
      analyzeMutation.mutate();
    }
    if (!isAnalyzing) analyzeTriggered.current = false;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isAnalyzing]);

  const saveAnalysisMutation = useMutation({
    mutationFn: async () => {
      if (!analysis || !projectId) throw new Error("nothing to save");
      const { data, error } = await api.PUT("/projects/{project_id}/analysis", {
        params: { path: { project_id: projectId } },
        body: analysis,
      });
      if (error) throw error;
      return data;
    },
    onSuccess: (data) => {
      setAnalysis(data);
      setDirty(false);
      toast.success("Analysis saved");
    },
    onError: (error: unknown) => {
      toast.error(error instanceof Error ? error.message : "Failed to save analysis");
    },
  });

  const buildGlossaryMutation = useMutation({
    mutationFn: async () => {
      if (!projectId) throw new Error("no project");
      const api_key = (await getApiKey().catch(() => null)) ?? null;
      const { error } = await api.POST("/projects/{project_id}/glossary/build", {
        params: { path: { project_id: projectId } },
        body: { api_key },
      });
      if (error) throw error;
    },
    onError: (error: unknown) => {
      toast.error(error instanceof Error ? error.message : "Failed to start glossary build");
    },
  });

  const approveMutation = useMutation({
    mutationFn: async () => {
      if (!projectId || !presetId) throw new Error("choose a preset first");
      const { error } = await api.POST("/projects/{project_id}/gate2/approve", {
        params: { path: { project_id: projectId } },
        body: { preset_id: presetId, style_delta: analysis?.style_delta ?? null },
      });
      if (error) throw error;
    },
    onSuccess: () => navigate(`/projects/${projectId}/review`),
    onError: (error: unknown) => {
      toast.error(error instanceof Error ? error.message : "Failed to approve style");
    },
  });

  if (projectQuery.isLoading) {
    return (
      <div className="grid h-full place-items-center">
        <Loader className="size-6 animate-spin text-muted-foreground" aria-hidden />
      </div>
    );
  }

  if (projectQuery.isError) {
    return <div className="p-8 text-sm text-destructive">{(projectQuery.error as Error).message}</div>;
  }

  if (isAnalyzing) {
    return (
      <div className="grid h-full place-items-center p-8">
        <div className="flex flex-col items-center gap-3 text-center">
          <Sparkles className="size-8 animate-pulse text-muted-foreground" aria-hidden />
          <p className="font-medium">Analyzing…</p>
          <p className="max-w-sm text-sm text-muted-foreground">
            Reading a sample of the book to propose tone, register, and a style guide.
          </p>
        </div>
      </div>
    );
  }

  if (stage === "failed" || (event?.stage === "failed" && !hasAnalysis)) {
    return (
      <div className="p-8">
        <div className="flex items-start gap-2 rounded-lg border border-destructive/30 bg-destructive/5 p-4">
          <CircleAlert className="mt-0.5 size-4 shrink-0 text-destructive" aria-hidden />
          <div>
            <p className="font-medium text-destructive">Analysis failed</p>
            <p className="mt-1 text-sm text-muted-foreground">
              {(event?.stage === "failed" && event.error) || "Something went wrong analyzing the book."}
            </p>
          </div>
        </div>
      </div>
    );
  }

  if (!hasAnalysis || analysisQuery.isLoading || !analysis) {
    return (
      <div className="grid h-full place-items-center">
        <Loader className="size-6 animate-spin text-muted-foreground" aria-hidden />
      </div>
    );
  }

  const readOnly = stage !== "style_review";
  const terms = glossaryQuery.data ?? [];

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center justify-between border-b border-border px-8 py-4">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">Style &amp; Glossary</h1>
          <p className="mt-0.5 text-sm text-muted-foreground">
            {readOnly ? "Read-only (already past this gate)" : "Review the proposal, then approve to start translating."}
          </p>
        </div>
        <div className="flex items-center gap-2">
          {dirty && <Badge variant="secondary">Unsaved changes</Badge>}
          <Button
            variant="outline"
            disabled={!dirty || readOnly || saveAnalysisMutation.isPending}
            onClick={() => saveAnalysisMutation.mutate()}
          >
            {saveAnalysisMutation.isPending && <Loader className="size-4 animate-spin" aria-hidden />}
            Save
          </Button>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto px-8 py-6">
        <div className="mx-auto max-w-3xl space-y-10">
          <section>
            <h2 className="mb-3 text-sm font-semibold text-muted-foreground uppercase tracking-wide">
              Analysis
            </h2>
            <AnalysisPanel
              analysis={analysis}
              readOnly={readOnly}
              onChange={(next) => {
                setAnalysis(next);
                setDirty(true);
              }}
            />
          </section>

          <section>
            <div className="mb-3 flex items-center justify-between">
              <h2 className="text-sm font-semibold text-muted-foreground uppercase tracking-wide">
                Glossary
              </h2>
              {!readOnly && (
                <Button
                  variant="outline"
                  size="sm"
                  disabled={buildGlossaryMutation.isPending || glossaryQuery.isFetching}
                  onClick={() => buildGlossaryMutation.mutate()}
                >
                  {(buildGlossaryMutation.isPending ||
                    (event?.stage?.startsWith("glossary_") && event.stage !== "glossary_ready" && event.stage !== "glossary_failed")) && (
                    <Loader className="size-3.5 animate-spin" aria-hidden />
                  )}
                  {terms.length > 0 ? "Rebuild glossary" : "Build glossary"}
                </Button>
              )}
            </div>
            <GlossaryTable projectId={projectId!} terms={terms} readOnly={readOnly} />
          </section>

          <section className="flex items-center justify-between rounded-lg border border-border p-4">
            <div className="space-y-1.5">
              <p className="text-sm font-medium">Preset</p>
              <Select value={presetId ?? undefined} onValueChange={setPresetId} disabled={readOnly}>
                <SelectTrigger className="w-56">
                  <SelectValue placeholder="Choose a preset" />
                </SelectTrigger>
                <SelectContent>
                  {(presetsQuery.data ?? []).map((preset) => (
                    <SelectItem key={preset.id} value={preset.id}>
                      {preset.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <Button
              disabled={readOnly || dirty || !presetId || approveMutation.isPending}
              onClick={() => approveMutation.mutate()}
            >
              {approveMutation.isPending && <Loader className="size-4 animate-spin" aria-hidden />}
              Approve &amp; start translating
            </Button>
          </section>
        </div>
      </div>
    </div>
  );
}
