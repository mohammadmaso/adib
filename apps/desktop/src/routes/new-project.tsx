import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { open } from "@tauri-apps/plugin-dialog";
import { useMutation, useQuery } from "@tanstack/react-query";
import { FileText, FolderOpen, Loader } from "lucide-react";
import { toast } from "sonner";
import { api, type components } from "@/lib/api-client";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Card, CardContent } from "@/components/ui/card";

type Preset = components["schemas"]["Preset"];

interface ProbeResult {
  format: string;
  pages: number | null;
  has_text_layer: boolean | null;
  likely_scanned?: boolean;
  should_escalate?: boolean;
  reason?: string;
}

const TARGET_LANGUAGES = [
  { code: "fa", label: "Persian" },
  { code: "ar", label: "Arabic" },
  { code: "he", label: "Hebrew" },
  { code: "ur", label: "Urdu" },
  { code: "en", label: "English" },
  { code: "fr", label: "French" },
  { code: "de", label: "German" },
  { code: "es", label: "Spanish" },
];

function stemOf(path: string): string {
  const file = path.split(/[/\\]/).pop() ?? path;
  return file.replace(/\.[^.]+$/, "");
}

export default function NewProjectRoute() {
  const navigate = useNavigate();
  const [sourcePath, setSourcePath] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [targetLang, setTargetLang] = useState("fa");
  const [presetId, setPresetId] = useState("general");

  const presetsQuery = useQuery({
    queryKey: ["presets"],
    queryFn: async () => {
      const { data, error } = await api.GET("/presets");
      if (error) throw error;
      return data;
    },
  });

  const probeQuery = useQuery({
    queryKey: ["probe", sourcePath],
    enabled: sourcePath !== null,
    queryFn: async () => {
      const { data, error } = await api.GET("/probe", {
        params: { query: { path: sourcePath! } },
      });
      if (error) throw error;
      return data as unknown as ProbeResult;
    },
  });

  const createMutation = useMutation({
    mutationFn: async () => {
      if (!sourcePath) throw new Error("choose a file first");
      const { data: created, error: createError } = await api.POST("/projects", {
        body: {
          name: name.trim() || stemOf(sourcePath),
          source_path: sourcePath,
          target_lang: targetLang,
          preset_id: presetId,
        },
      });
      if (createError) throw createError;

      const { error: ingestError } = await api.POST("/projects/{project_id}/ingest", {
        params: { path: { project_id: created.project_id }, query: {} },
      });
      if (ingestError) throw ingestError;

      return created.project_id;
    },
    onSuccess: (projectId) => {
      navigate(`/projects/${projectId}/structure`);
    },
    onError: (error: unknown) => {
      toast.error(error instanceof Error ? error.message : "Failed to start project");
    },
  });

  async function pickFile() {
    const selected = await open({
      multiple: false,
      filters: [
        {
          name: "Books",
          extensions: ["pdf", "epub", "docx", "html", "htm", "md", "markdown", "txt"],
        },
      ],
    });
    if (typeof selected === "string") {
      setSourcePath(selected);
      if (!name) setName(stemOf(selected));
    }
  }

  const probe = probeQuery.data;
  const canStart = sourcePath !== null && !probeQuery.isError && !createMutation.isPending;

  return (
    <div className="mx-auto max-w-xl p-8">
      <h1 className="text-2xl font-semibold tracking-tight">New Project</h1>
      <p className="mt-1 text-sm text-muted-foreground">
        Bring in a book, then choose how it should be translated.
      </p>

      <div className="mt-6 space-y-6">
        <div className="space-y-2">
          <Label>Source file</Label>
          {sourcePath ? (
            <Card>
              <CardContent className="flex items-start justify-between gap-3">
                <div className="flex min-w-0 items-start gap-2">
                  <FileText className="mt-0.5 size-4 shrink-0 text-muted-foreground" aria-hidden />
                  <div className="min-w-0">
                    <p className="truncate text-sm font-medium" title={sourcePath}>
                      {sourcePath.split(/[/\\]/).pop()}
                    </p>
                    {probeQuery.isLoading && (
                      <p className="text-xs text-muted-foreground">Probing file…</p>
                    )}
                    {probeQuery.isError && (
                      <p className="text-xs text-destructive">
                        {(probeQuery.error as Error).message}
                      </p>
                    )}
                    {probe && (
                      <p className="text-xs text-muted-foreground">
                        {probe.format.toUpperCase()}
                        {probe.pages != null && ` · ${probe.pages} pages`}
                        {probe.has_text_layer === false && " · no text layer (will use OCR)"}
                        {probe.likely_scanned && " · looks scanned"}
                      </p>
                    )}
                  </div>
                </div>
                <Button variant="ghost" size="sm" onClick={pickFile}>
                  Change
                </Button>
              </CardContent>
            </Card>
          ) : (
            <Button variant="outline" className="w-full justify-center py-8" onClick={pickFile}>
              <FolderOpen className="size-4" aria-hidden />
              Choose a file…
            </Button>
          )}
        </div>

        <div className="space-y-2">
          <Label htmlFor="project-name">Project name</Label>
          <Input
            id="project-name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="My Book"
          />
        </div>

        <div className="grid grid-cols-2 gap-4">
          <div className="space-y-2">
            <Label>Target language</Label>
            <Select value={targetLang} onValueChange={(v) => setTargetLang(v as string)}>
              <SelectTrigger className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {TARGET_LANGUAGES.map((lang) => (
                  <SelectItem key={lang.code} value={lang.code}>
                    {lang.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="space-y-2">
            <Label>Preset</Label>
            <Select value={presetId} onValueChange={(v) => setPresetId(v as string)}>
              <SelectTrigger className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {(presetsQuery.data ?? []).map((preset: Preset) => (
                  <SelectItem key={preset.id} value={preset.id}>
                    {preset.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </div>

        <Button className="w-full" disabled={!canStart} onClick={() => createMutation.mutate()}>
          {createMutation.isPending && <Loader className="size-4 animate-spin" aria-hidden />}
          Start
        </Button>
      </div>
    </div>
  );
}
