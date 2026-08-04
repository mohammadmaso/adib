import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { BookOpen, Loader, Plus, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { api, type components } from "@/lib/api-client";
import {
  projectIdFromPath,
  routeForStage,
  STAGE_BADGE_VARIANT,
  STAGE_LABEL,
} from "@/lib/project";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardAction,
  CardContent,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Progress } from "@/components/ui/progress";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

type ProjectSummary = components["schemas"]["ProjectSummary"];

const currency = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" });
const relativeTime = (iso: string) => {
  const diffMs = Date.now() - new Date(iso).getTime();
  const mins = Math.round(diffMs / 60_000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.round(hours / 24)}d ago`;
};

export default function LibraryRoute() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [pendingDelete, setPendingDelete] = useState<ProjectSummary | null>(null);

  const projectsQuery = useQuery({
    queryKey: ["projects"],
    queryFn: async () => {
      const { data, error } = await api.GET("/projects");
      if (error) throw error;
      return data;
    },
  });

  const deleteMutation = useMutation({
    mutationFn: async (projectId: string) => {
      const { error } = await api.DELETE("/projects/{project_id}", {
        params: { path: { project_id: projectId } },
      });
      if (error) throw error;
    },
    onSuccess: (_data, projectId) => {
      toast.success("Project deleted");
      setPendingDelete(null);
      queryClient.setQueryData<ProjectSummary[]>(["projects"], (prev) =>
        prev?.filter((p) => projectIdFromPath(p.path) !== projectId),
      );
    },
    onError: (error: unknown) => {
      toast.error(error instanceof Error ? error.message : "Failed to delete project");
    },
  });

  return (
    <div className="p-8">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Library</h1>
          <p className="mt-1 text-sm text-muted-foreground">Your books in translation.</p>
        </div>
        <Button onClick={() => navigate("/new")}>
          <Plus className="size-4" aria-hidden />
          New Project
        </Button>
      </div>

      <div className="mt-6">
        {projectsQuery.isLoading && <LibrarySkeleton />}

        {projectsQuery.isError && (
          <p className="text-sm text-destructive">
            Could not load projects: {(projectsQuery.error as Error).message}
          </p>
        )}

        {projectsQuery.data && projectsQuery.data.length === 0 && <EmptyState onNew={() => navigate("/new")} />}

        {projectsQuery.data && projectsQuery.data.length > 0 && (
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3">
            {projectsQuery.data.map((project) => {
              const projectId = projectIdFromPath(project.path);
              const progress =
                project.segments_total > 0
                  ? Math.round((project.segments_done / project.segments_total) * 100)
                  : 0;

              return (
                <Card
                  key={project.path}
                  className="cursor-pointer transition-shadow hover:shadow-md"
                  onClick={() => navigate(routeForStage(project.stage, projectId))}
                >
                  <CardHeader>
                    <CardTitle className="truncate" title={project.name}>
                      {project.name}
                    </CardTitle>
                    <CardAction>
                      <Badge variant={STAGE_BADGE_VARIANT[project.stage]}>
                        {STAGE_LABEL[project.stage]}
                      </Badge>
                    </CardAction>
                  </CardHeader>

                  <CardContent className="space-y-3">
                    <p className="text-sm text-muted-foreground">
                      {(project.source_lang ?? "auto").toUpperCase()} → {project.target_lang.toUpperCase()}
                    </p>

                    {project.error ? (
                      <p className="line-clamp-2 text-sm text-destructive">{project.error}</p>
                    ) : (
                      project.segments_total > 0 && (
                        <div className="space-y-1">
                          <Progress value={progress} />
                          <p className="text-xs text-muted-foreground">
                            {project.segments_done} / {project.segments_total} segments
                          </p>
                        </div>
                      )
                    )}
                  </CardContent>

                  <CardFooter className="flex items-center justify-between text-xs text-muted-foreground">
                    <span>
                      {currency.format(project.cost_usd)} · {relativeTime(project.updated_at)}
                    </span>
                    <Button
                      variant="ghost"
                      size="icon"
                      aria-label={`Delete ${project.name}`}
                      onClick={(e) => {
                        e.stopPropagation();
                        setPendingDelete(project);
                      }}
                    >
                      <Trash2 className="size-4 text-muted-foreground" aria-hidden />
                    </Button>
                  </CardFooter>
                </Card>
              );
            })}
          </div>
        )}
      </div>

      <Dialog
        open={pendingDelete !== null}
        onOpenChange={(open) => {
          if (!open) setPendingDelete(null);
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Delete "{pendingDelete?.name}"?</DialogTitle>
            <DialogDescription>
              This removes the project file and all its translated content. This cannot be undone.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setPendingDelete(null)}>
              Cancel
            </Button>
            <Button
              variant="destructive"
              disabled={deleteMutation.isPending}
              onClick={() => {
                if (pendingDelete) deleteMutation.mutate(projectIdFromPath(pendingDelete.path));
              }}
            >
              {deleteMutation.isPending && <Loader className="size-4 animate-spin" aria-hidden />}
              Delete
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

function LibrarySkeleton() {
  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3">
      {Array.from({ length: 3 }).map((_, i) => (
        <Card key={i}>
          <CardHeader>
            <Skeleton className="h-5 w-2/3" />
          </CardHeader>
          <CardContent className="space-y-3">
            <Skeleton className="h-4 w-1/3" />
            <Skeleton className="h-2 w-full" />
          </CardContent>
          <CardFooter>
            <Skeleton className="h-4 w-1/2" />
          </CardFooter>
        </Card>
      ))}
    </div>
  );
}

function EmptyState({ onNew }: { onNew: () => void }) {
  return (
    <div className="flex flex-col items-center gap-3 rounded-lg border border-dashed border-neutral-200 py-16 text-center dark:border-neutral-800">
      <BookOpen className="size-8 text-muted-foreground" aria-hidden />
      <div>
        <p className="font-medium">No projects yet</p>
        <p className="text-sm text-muted-foreground">Start by bringing in a book to translate.</p>
      </div>
      <Button onClick={onNew}>
        <Plus className="size-4" aria-hidden />
        New Project
      </Button>
    </div>
  );
}
