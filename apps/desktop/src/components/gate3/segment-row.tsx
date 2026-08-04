import { useEffect, useState } from "react";
import { Loader, Lock, RotateCcw, Unlock } from "lucide-react";
import type { components } from "@/lib/api-client";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";

type Segment = components["schemas"]["Segment"];

const STATUS_VARIANT: Record<string, "default" | "secondary" | "outline" | "destructive"> = {
  pending: "outline",
  in_progress: "secondary",
  translated: "outline",
  flagged: "destructive",
  approved: "default",
  failed: "destructive",
  skipped: "secondary",
};

interface SegmentRowProps {
  segment: Segment;
  onSave: (targetText: string) => void;
  onToggleLock: () => void;
  onApprove: () => void;
  onRetranslate: () => void;
  retranslating: boolean;
}

export function SegmentRow({
  segment,
  onSave,
  onToggleLock,
  onApprove,
  onRetranslate,
  retranslating,
}: SegmentRowProps) {
  const [draft, setDraft] = useState(segment.target_text ?? "");
  useEffect(() => {
    setDraft(segment.target_text ?? "");
  }, [segment.target_text]);
  const dirty = draft !== (segment.target_text ?? "");
  const flags = (segment.qa_flags ?? []) as { rule?: string; severity?: string; message?: string }[];

  return (
    <div className="grid grid-cols-2 gap-4 border-b border-border px-4 py-3">
      <div className="min-w-0 space-y-1.5">
        <div className="flex flex-wrap items-center gap-1.5">
          <Badge variant={STATUS_VARIANT[segment.status] ?? "outline"} className="text-[10px]">
            {segment.status}
          </Badge>
          {segment.heading_path && segment.heading_path.length > 0 && (
            <span className="truncate text-[10px] text-muted-foreground">
              {segment.heading_path.join(" › ")}
            </span>
          )}
          {flags.map((f, i) => (
            <Badge
              key={i}
              variant={f.severity === "error" ? "destructive" : "secondary"}
              className="text-[10px]"
              title={f.message}
            >
              {f.rule}
            </Badge>
          ))}
        </div>
        <p className="text-sm whitespace-pre-wrap text-muted-foreground">{segment.source_text}</p>
      </div>

      <div className="min-w-0 space-y-1.5">
        <div className="flex items-center justify-end gap-1">
          <Button
            variant="ghost"
            size="icon"
            className="size-7"
            title={segment.locked ? "Unlock" : "Lock"}
            onClick={onToggleLock}
          >
            {segment.locked ? <Lock className="size-3.5" /> : <Unlock className="size-3.5" />}
          </Button>
          <Button
            variant="ghost"
            size="icon"
            className="size-7"
            title="Retranslate"
            disabled={segment.locked || retranslating}
            onClick={onRetranslate}
          >
            {retranslating ? (
              <Loader className="size-3.5 animate-spin" />
            ) : (
              <RotateCcw className="size-3.5" />
            )}
          </Button>
          <Button
            variant={segment.status === "approved" ? "default" : "outline"}
            size="sm"
            className="h-7 text-xs"
            onClick={onApprove}
          >
            {segment.status === "approved" ? "Approved" : "Approve"}
          </Button>
        </div>
        <Textarea
          dir="auto"
          value={draft}
          disabled={segment.locked}
          className="min-h-16 text-sm"
          onChange={(e) => setDraft(e.target.value)}
          onBlur={() => {
            if (dirty) onSave(draft);
          }}
        />
      </div>
    </div>
  );
}
