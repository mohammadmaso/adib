import { useEffect, useState } from "react";
import { open } from "@tauri-apps/plugin-dialog";
import { openPath, revealItemInDir } from "@tauri-apps/plugin-opener";
import { useQuery } from "@tanstack/react-query";
import { CircleAlert, FolderOpen, Loader, TriangleAlert } from "lucide-react";
import { toast } from "sonner";
import { api } from "@/lib/api-client";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Progress } from "@/components/ui/progress";

export type ExportFormat = "pdf" | "epub";

/** Where the user last exported *this* project. The engine's default (the
 *  project's own .export folder) is a fine first answer but a poor second one:
 *  someone who chose Desktop once means it for the next export too. */
const dirKey = (projectId: string) => `adib.export.dir.${projectId}`;
const nameKey = (projectId: string) => `adib.export.name.${projectId}`;

function remembered(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

/** The engine rejects an unusable destination with `{detail: "..."}`; that
 *  sentence is what the user needs to see, not "Error". */
function detailOf(error: unknown, fallback: string): string {
  if (typeof error === "object" && error && "detail" in error) {
    return String((error as { detail: unknown }).detail);
  }
  return error instanceof Error ? error.message : fallback;
}

function remember(key: string, value: string) {
  try {
    localStorage.setItem(key, value);
  } catch {
    // A blocked localStorage costs the convenience, not the export.
  }
}

interface ExportPanelProps {
  projectId: string;
  /** True while the engine is running an export for this project. */
  isExporting: boolean;
  /** Live percent from the project's SSE stream, if any. */
  percent: number | null;
  /** Absolute paths of the last run's output, keyed by format. */
  outputs: Record<string, string> | null;
  failedReason: string | null;
  starting: boolean;
  onExport: (request: {
    formats: ExportFormat[];
    out_dir: string;
    filename: string;
  }) => void;
}

export function ExportPanel({
  projectId,
  isExporting,
  percent,
  outputs,
  failedReason,
  starting,
  onExport,
}: ExportPanelProps) {
  const [formats, setFormats] = useState<Record<ExportFormat, boolean>>({
    pdf: true,
    epub: true,
  });
  const [directory, setDirectory] = useState<string | null>(null);
  const [filename, setFilename] = useState<string | null>(null);

  const selected = (Object.keys(formats) as ExportFormat[]).filter((f) => formats[f]);

  // The engine owns the defaults (project name, project .export folder) and the
  // path validation, so the panel asks it rather than guessing either.
  const targetQuery = useQuery({
    queryKey: ["export-target", projectId, directory, filename, selected.join(",")],
    // A rejected folder is an answer, not a transient failure to retry.
    retry: false,
    queryFn: async () => {
      const { data, error } = await api.GET("/projects/{project_id}/export/target", {
        params: {
          path: { project_id: projectId },
          query: {
            out_dir: directory ?? undefined,
            filename: filename ?? undefined,
            formats: selected.join(",") || "pdf,epub",
          },
        },
      });
      if (error) throw error;
      return data;
    },
  });

  const target = targetQuery.data;

  // Seed the fields once from the remembered choice, falling back to whatever
  // the engine says the default is.
  useEffect(() => {
    if (!target) return;
    setDirectory((d) => d ?? remembered(dirKey(projectId)) ?? target.directory);
    setFilename((n) => n ?? remembered(nameKey(projectId)) ?? target.filename);
  }, [target, projectId]);

  const chooseFolder = async () => {
    const picked = await open({
      directory: true,
      multiple: false,
      title: "Choose export folder",
      defaultPath: directory ?? undefined,
    });
    if (typeof picked === "string") {
      setDirectory(picked);
      remember(dirKey(projectId), picked);
    }
  };

  const start = () => {
    if (!target || selected.length === 0) return;
    const dir = directory ?? target.directory;
    const name = filename?.trim() || target.filename;
    remember(dirKey(projectId), dir);
    remember(nameKey(projectId), name);
    onExport({ formats: selected, out_dir: dir, filename: name });
  };

  const pathError = targetQuery.error
    ? detailOf(targetQuery.error, "That folder cannot be used")
    : null;
  const willOverwrite = (target?.existing ?? []).filter((f) =>
    selected.includes(f as ExportFormat),
  );

  if (isExporting) {
    return (
      <div className="space-y-2">
        <div className="flex items-center gap-3">
          <Loader className="size-4 shrink-0 animate-spin text-muted-foreground" aria-hidden />
          <p className="flex-1 text-sm font-medium">
            Exporting{percent != null ? ` — ${percent}%` : "…"}
          </p>
        </div>
        <Progress value={percent ?? 0} />
        <p className="truncate text-xs text-muted-foreground" title={directory ?? ""}>
          {directory}
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      {failedReason && (
        <div className="flex items-start gap-2 rounded-lg border border-destructive/30 bg-destructive/5 px-3 py-2 text-sm text-destructive">
          <CircleAlert className="mt-0.5 size-3.5 shrink-0" aria-hidden />
          <span>{failedReason}</span>
        </div>
      )}

      <div className="flex flex-wrap items-end gap-x-6 gap-y-3">
        <div className="space-y-1.5">
          <Label className="text-xs text-muted-foreground">Formats</Label>
          <div className="flex h-8 items-center gap-4">
            {(["pdf", "epub"] as ExportFormat[]).map((format) => (
              <label key={format} className="flex items-center gap-1.5 text-sm">
                <Checkbox
                  checked={formats[format]}
                  onCheckedChange={(checked) =>
                    setFormats((f) => ({ ...f, [format]: Boolean(checked) }))
                  }
                />
                <Label>{format.toUpperCase()}</Label>
              </label>
            ))}
          </div>
        </div>

        <div className="min-w-[14rem] flex-1 space-y-1.5">
          <Label className="text-xs text-muted-foreground">File name</Label>
          <Input
            value={filename ?? ""}
            placeholder={target?.filename ?? "book"}
            onChange={(e) => setFilename(e.target.value)}
            onBlur={() => setFilename((n) => n?.trim() || null)}
          />
        </div>

        <div className="min-w-[16rem] flex-[2] space-y-1.5">
          <Label className="text-xs text-muted-foreground">Folder</Label>
          <div className="flex items-center gap-2">
            <Button variant="outline" size="sm" onClick={chooseFolder}>
              <FolderOpen className="size-3.5" aria-hidden />
              Choose…
            </Button>
            <span
              className="min-w-0 flex-1 truncate text-xs text-muted-foreground"
              // The full path is often longer than the panel; keep it reachable.
              title={directory ?? ""}
              dir="rtl"
            >
              {directory ?? "…"}
            </span>
          </div>
        </div>

        <Button disabled={selected.length === 0 || starting || !target} onClick={start}>
          {starting && <Loader className="size-4 animate-spin" aria-hidden />}
          Export
        </Button>
      </div>

      <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-xs">
        {pathError ? (
          <span className="flex items-center gap-1.5 text-destructive">
            <CircleAlert className="size-3.5" aria-hidden />
            {pathError}
          </span>
        ) : (
          <span className="truncate text-muted-foreground">
            Saves {selected.map((f) => `${filename ?? target?.filename ?? "book"}.${f}`).join(", ")}
          </span>
        )}
        {willOverwrite.length > 0 && (
          <span className="flex items-center gap-1.5 text-amber-600 dark:text-amber-500">
            <TriangleAlert className="size-3.5" aria-hidden />
            Replaces the existing {willOverwrite.map((f) => f.toUpperCase()).join(" and ")}
          </span>
        )}
      </div>

      {outputs && Object.keys(outputs).length > 0 && (
        <div className="flex flex-wrap items-center gap-2 rounded-lg border border-border bg-muted/40 px-3 py-2">
          <span className="text-xs text-muted-foreground">Last export:</span>
          {Object.entries(outputs).map(([format, path]) => (
            <div key={format} className="flex items-center gap-1">
              <Button
                variant="outline"
                size="sm"
                onClick={() =>
                  openPath(path).catch(() => toast.error(`Could not open the ${format}`))
                }
              >
                Open {format.toUpperCase()}
              </Button>
              <Button
                variant="ghost"
                size="sm"
                aria-label={`Show ${format.toUpperCase()} in folder`}
                onClick={() => revealItemInDir(path)}
              >
                <FolderOpen className="size-3.5" aria-hidden />
              </Button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
