import { useRef } from "react";
import {
  ArrowDown,
  ArrowUp,
  ChevronDown,
  ChevronRight,
  GitMerge,
  IndentDecrease,
  IndentIncrease,
  Scissors,
  Trash2,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { NODE_KINDS, type DocNode } from "@/lib/doc-tree";
import { useAssetImage } from "@/lib/asset-image";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

const KIND_LABEL: Record<string, string> = {
  heading: "Heading",
  paragraph: "Paragraph",
  list: "List",
  list_item: "List item",
  table: "Table",
  figure: "Figure",
  code: "Code",
  quote: "Quote",
  footnote: "Footnote",
  toc: "TOC",
  front_matter: "Front matter",
  back_matter: "Back matter",
  page_break: "Page break",
  equation: "Equation",
};

export type NodeAction =
  | { type: "delete" }
  | { type: "patch"; patch: Partial<DocNode> }
  | { type: "move"; dir: -1 | 1 }
  | { type: "merge-next" }
  | { type: "indent" }
  | { type: "outdent" }
  | { type: "split"; index: number };

export interface TreeNodeRowProps {
  projectId: string | undefined;
  node: DocNode;
  depth: number;
  hasChildren: boolean;
  collapsed: boolean;
  onToggleCollapse: () => void;
  can: {
    moveUp: boolean;
    moveDown: boolean;
    indent: boolean;
    outdent: boolean;
    mergeNext: boolean;
  };
  onAction: (id: string, action: NodeAction) => void;
}

export function TreeNodeRow({
  projectId,
  node,
  depth,
  hasChildren,
  collapsed,
  onToggleCollapse,
  can,
  onAction,
}: TreeNodeRowProps) {
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const firstAssetId = node.kind === "figure" ? node.assets?.[0]?.asset_id : undefined;
  const imageUrl = useAssetImage(projectId, firstAssetId);

  return (
    <div>
      <div
        className="group flex items-start gap-1.5 rounded-md py-1.5 pr-2 hover:bg-muted/50"
        style={{ paddingLeft: `${depth * 20}px` }}
      >
        <button
          type="button"
          className={cn(
            "mt-1 flex size-4 shrink-0 items-center justify-center text-muted-foreground",
            !hasChildren && "invisible",
          )}
          onClick={onToggleCollapse}
          aria-label={collapsed ? "Expand" : "Collapse"}
        >
          {collapsed ? <ChevronRight className="size-3.5" /> : <ChevronDown className="size-3.5" />}
        </button>

        <div className="min-w-0 flex-1 space-y-1.5">
          <div className="flex flex-wrap items-center gap-1.5">
            <Select
              value={node.kind}
              onValueChange={(v) => onAction(node.id, { type: "patch", patch: { kind: v as DocNode["kind"] } })}
            >
              <SelectTrigger size="sm" className="h-6 gap-1 px-1.5 text-xs">
                <SelectValue>{KIND_LABEL[node.kind] ?? node.kind}</SelectValue>
              </SelectTrigger>
              <SelectContent>
                {NODE_KINDS.map((kind) => (
                  <SelectItem key={kind} value={kind}>
                    {KIND_LABEL[kind] ?? kind}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>

            {node.kind === "heading" && (
              <div className="flex items-center gap-0.5">
                <Button
                  variant="ghost"
                  size="icon"
                  className="size-6"
                  disabled={(node.level ?? 1) <= 1}
                  onClick={() =>
                    onAction(node.id, { type: "patch", patch: { level: Math.max(1, (node.level ?? 1) - 1) } })
                  }
                >
                  −
                </Button>
                <span className="w-4 text-center text-xs text-muted-foreground">H{node.level ?? 1}</span>
                <Button
                  variant="ghost"
                  size="icon"
                  className="size-6"
                  disabled={(node.level ?? 1) >= 6}
                  onClick={() =>
                    onAction(node.id, { type: "patch", patch: { level: Math.min(6, (node.level ?? 1) + 1) } })
                  }
                >
                  +
                </Button>
              </div>
            )}

            {node.source?.page_start != null && (
              <Badge variant="outline" className="text-[10px] text-muted-foreground">
                p.{node.source.page_start}
                {node.source.page_end != null &&
                  node.source.page_end !== node.source.page_start &&
                  `–${node.source.page_end}`}
              </Badge>
            )}

            {(node.assets?.length ?? 0) > 0 && (
              <Badge variant="secondary" className="text-[10px]">
                {node.assets!.length} asset{node.assets!.length > 1 ? "s" : ""}
              </Badge>
            )}

            <div className="ml-auto flex items-center gap-0.5 opacity-0 transition-opacity group-hover:opacity-100">
              <Button
                variant="ghost"
                size="icon"
                className="size-6"
                disabled={!can.moveUp}
                title="Move up"
                onClick={() => onAction(node.id, { type: "move", dir: -1 })}
              >
                <ArrowUp className="size-3.5" />
              </Button>
              <Button
                variant="ghost"
                size="icon"
                className="size-6"
                disabled={!can.moveDown}
                title="Move down"
                onClick={() => onAction(node.id, { type: "move", dir: 1 })}
              >
                <ArrowDown className="size-3.5" />
              </Button>
              <Button
                variant="ghost"
                size="icon"
                className="size-6"
                disabled={!can.outdent}
                title="Outdent"
                onClick={() => onAction(node.id, { type: "outdent" })}
              >
                <IndentDecrease className="size-3.5" />
              </Button>
              <Button
                variant="ghost"
                size="icon"
                className="size-6"
                disabled={!can.indent}
                title="Indent (nest under previous sibling)"
                onClick={() => onAction(node.id, { type: "indent" })}
              >
                <IndentIncrease className="size-3.5" />
              </Button>
              <Button
                variant="ghost"
                size="icon"
                className="size-6"
                disabled={!can.mergeNext}
                title="Merge with next"
                onClick={() => onAction(node.id, { type: "merge-next" })}
              >
                <GitMerge className="size-3.5" />
              </Button>
              {!!node.text && (
                <Button
                  variant="ghost"
                  size="icon"
                  className="size-6"
                  title="Split at cursor"
                  onClick={() => {
                    const idx = textareaRef.current?.selectionStart ?? 0;
                    if (idx > 0 && idx < (node.text?.length ?? 0)) {
                      onAction(node.id, { type: "split", index: idx });
                    }
                  }}
                >
                  <Scissors className="size-3.5" />
                </Button>
              )}
              <Button
                variant="ghost"
                size="icon"
                className="size-6 text-destructive hover:text-destructive"
                title="Delete"
                onClick={() => onAction(node.id, { type: "delete" })}
              >
                <Trash2 className="size-3.5" />
              </Button>
            </div>
          </div>

          {node.kind === "figure" &&
            (imageUrl ? (
              <img
                src={imageUrl}
                alt={node.assets?.[0]?.alt ?? ""}
                className="max-h-48 max-w-full rounded border object-contain"
              />
            ) : (
              <p className="text-xs text-muted-foreground italic">
                {firstAssetId ? "loading image…" : "figure (no asset)"}
              </p>
            ))}

          {node.text != null ? (
            <Textarea
              ref={textareaRef}
              defaultValue={node.text}
              className={cn(
                "min-h-8 py-1 text-sm",
                node.kind === "code" && "font-mono whitespace-pre",
              )}
              onBlur={(e) => {
                if (e.target.value !== node.text) {
                  onAction(node.id, { type: "patch", patch: { text: e.target.value } });
                }
              }}
            />
          ) : (
            node.kind !== "figure" && (
              <p className="text-xs text-muted-foreground italic">
                {node.kind === "table" ? "table content" : "(no text)"}
              </p>
            )
          )}
        </div>
      </div>
    </div>
  );
}
