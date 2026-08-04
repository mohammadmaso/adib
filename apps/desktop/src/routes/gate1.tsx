import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CircleAlert, Loader } from "lucide-react";
import { toast } from "sonner";
import { api } from "@/lib/api-client";
import {
  canIndent,
  canMergeNext,
  countNodes,
  deleteNode,
  indentNode,
  mergeWithNext,
  moveNode,
  outdentNode,
  splitNode,
  updateNode,
  type DocTree,
} from "@/lib/doc-tree";
import { TreeNodeRow, type NodeAction } from "@/components/gate1/tree-node-row";
import { useProjectEvents } from "@/hooks/use-project-events";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";

function applyAction(tree: DocTree, id: string, action: NodeAction): DocTree {
  const nodes = tree.nodes ?? [];
  switch (action.type) {
    case "delete":
      return { ...tree, nodes: deleteNode(nodes, id) };
    case "patch":
      return { ...tree, nodes: updateNode(nodes, id, action.patch) };
    case "move":
      return { ...tree, nodes: moveNode(nodes, id, action.dir) };
    case "merge-next":
      return { ...tree, nodes: mergeWithNext(nodes, id) };
    case "indent":
      return { ...tree, nodes: indentNode(nodes, id) };
    case "outdent":
      return { ...tree, nodes: outdentNode(nodes, id) };
    case "split":
      return { ...tree, nodes: splitNode(nodes, id, action.index) };
  }
}

export default function Gate1Route() {
  const { projectId } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [tree, setTree] = useState<DocTree | null>(null);
  const [dirty, setDirty] = useState(false);

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
      const stage = query.state.data?.stage;
      return stage === "ingesting" ? 1500 : false;
    },
  });

  const stage = projectQuery.data?.stage;
  const isIngesting = stage === "created" || stage === "ingesting";
  const hasTree = stage != null && !isIngesting;

  const treeQuery = useQuery({
    queryKey: ["tree", projectId],
    enabled: !!projectId && hasTree,
    queryFn: async () => {
      const { data, error } = await api.GET("/projects/{project_id}/tree", {
        params: { path: { project_id: projectId! }, query: {} },
      });
      if (error) throw error;
      return data;
    },
  });

  useEffect(() => {
    if (treeQuery.data && !tree) setTree(treeQuery.data as DocTree);
  }, [treeQuery.data, tree]);

  const event = useProjectEvents(projectId, isIngesting);
  useEffect(() => {
    if (event && (event.stage === "structure_review" || event.stage === "failed")) {
      queryClient.invalidateQueries({ queryKey: ["project", projectId] });
    }
  }, [event, projectId, queryClient]);

  const saveMutation = useMutation({
    mutationFn: async () => {
      if (!tree || !projectId) throw new Error("nothing to save");
      const { data, error } = await api.PUT("/projects/{project_id}/tree", {
        params: { path: { project_id: projectId } },
        body: tree,
      });
      if (error) throw error;
      return data;
    },
    onSuccess: (result) => {
      const { segments_added, segments_kept, segments_removed, ...rest } = result;
      setTree(rest as DocTree);
      setDirty(false);
      toast.success(
        `Saved · ${segments_added} added, ${segments_kept} kept, ${segments_removed} removed`,
      );
    },
    onError: (error: unknown) => {
      toast.error(error instanceof Error ? error.message : "Failed to save structure");
    },
  });

  const retryIngestMutation = useMutation({
    mutationFn: async () => {
      if (!projectId) throw new Error("no project");
      const { error } = await api.POST("/projects/{project_id}/ingest", {
        params: { path: { project_id: projectId }, query: {} },
      });
      if (error) throw error;
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["project", projectId] }),
    onError: (error: unknown) => {
      toast.error(error instanceof Error ? error.message : "Failed to retry ingest");
    },
  });

  const approveMutation = useMutation({
    mutationFn: async () => {
      if (!projectId) throw new Error("no project");
      const { error } = await api.POST("/projects/{project_id}/tree/approve", {
        params: { path: { project_id: projectId } },
      });
      if (error) throw error;
    },
    onSuccess: () => navigate(`/projects/${projectId}/style`),
    onError: (error: unknown) => {
      toast.error(error instanceof Error ? error.message : "Failed to approve structure");
    },
  });

  function dispatch(id: string, action: NodeAction) {
    setTree((t) => {
      if (!t) return t;
      return applyAction(t, id, action);
    });
    setDirty(true);
  }

  if (projectQuery.isLoading) {
    return (
      <div className="grid h-full place-items-center">
        <Loader className="size-6 animate-spin text-muted-foreground" aria-hidden />
      </div>
    );
  }

  if (projectQuery.isError) {
    return (
      <div className="p-8 text-sm text-destructive">
        {(projectQuery.error as Error).message}
      </div>
    );
  }

  if (isIngesting) {
    return (
      <div className="grid h-full place-items-center p-8">
        <div className="flex flex-col items-center gap-3 text-center">
          <Loader className="size-8 animate-spin text-muted-foreground" aria-hidden />
          <p className="font-medium">Ingesting…</p>
          <p className="text-sm text-muted-foreground">
            Parsing the source file into a document structure. This can take a moment for large books.
          </p>
        </div>
      </div>
    );
  }

  if (stage === "failed") {
    const reason =
      projectQuery.data?.failed_reason ??
      (event?.stage === "failed" && typeof event.error === "string" ? event.error : null) ??
      "The source file could not be parsed.";
    return (
      <div className="p-8">
        <div className="flex items-start gap-2 rounded-lg border border-destructive/30 bg-destructive/5 p-4">
          <CircleAlert className="mt-0.5 size-4 shrink-0 text-destructive" aria-hidden />
          <div className="flex-1 space-y-3">
            <div>
              <p className="font-medium text-destructive">Ingest failed</p>
              <p className="mt-1 text-sm text-muted-foreground">{reason}</p>
            </div>
            <Button
              size="sm"
              variant="outline"
              disabled={retryIngestMutation.isPending}
              onClick={() => retryIngestMutation.mutate()}
            >
              {retryIngestMutation.isPending && <Loader className="size-3.5 animate-spin" aria-hidden />}
              Retry ingest
            </Button>
          </div>
        </div>
      </div>
    );
  }

  const nodes = tree?.nodes ?? [];
  const readOnly = stage !== "structure_review";

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center justify-between border-b border-border px-8 py-4">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">
            Structure {tree?.meta?.title ? `· ${tree.meta.title}` : ""}
          </h1>
          <p className="mt-0.5 text-sm text-muted-foreground">
            {countNodes(nodes)} nodes
            {readOnly && " · read-only (already past this gate)"}
          </p>
        </div>
        <div className="flex items-center gap-2">
          {dirty && <Badge variant="secondary">Unsaved changes</Badge>}
          <Button
            variant="outline"
            disabled={!dirty || saveMutation.isPending || readOnly}
            onClick={() => saveMutation.mutate()}
          >
            {saveMutation.isPending && <Loader className="size-4 animate-spin" aria-hidden />}
            Save
          </Button>
          <Button
            disabled={dirty || readOnly || approveMutation.isPending}
            onClick={() => approveMutation.mutate()}
          >
            {approveMutation.isPending && <Loader className="size-4 animate-spin" aria-hidden />}
            Approve structure
          </Button>
        </div>
      </div>

      <div className={`flex-1 overflow-y-auto px-6 py-4 ${readOnly ? "pointer-events-none opacity-70" : ""}`}>
        {treeQuery.isLoading && !tree && (
          <div className="grid h-full place-items-center">
            <Loader className="size-6 animate-spin text-muted-foreground" aria-hidden />
          </div>
        )}
        {tree &&
          nodes.map((node, i) => (
            <TreeNodeRow
              key={node.id}
              node={node}
              depth={0}
              can={{
                moveUp: i > 0,
                moveDown: i < nodes.length - 1,
                indent: canIndent(nodes, node.id),
                outdent: false,
                mergeNext: canMergeNext(nodes, node.id),
              }}
              onAction={dispatch}
            />
          ))}
      </div>
    </div>
  );
}
