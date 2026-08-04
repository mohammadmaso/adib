import { Plus, X } from "lucide-react";
import type { components } from "@/lib/api-client";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
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
type Register = components["schemas"]["Register"];

const REGISTERS: Register[] = ["formal", "neutral", "colloquial", "literary", "technical"];

interface AnalysisPanelProps {
  analysis: BookAnalysis;
  onChange: (analysis: BookAnalysis) => void;
  readOnly: boolean;
}

export function AnalysisPanel({ analysis, onChange, readOnly }: AnalysisPanelProps) {
  function set<K extends keyof BookAnalysis>(key: K, value: BookAnalysis[K]) {
    onChange({ ...analysis, [key]: value });
  }

  const notes = analysis.reader_notes ?? [];

  return (
    <div className="space-y-5">
      <div className="grid grid-cols-2 gap-4">
        <div className="space-y-1.5">
          <Label>Detected source language</Label>
          <Input
            value={analysis.detected_source_lang}
            disabled={readOnly}
            onChange={(e) => set("detected_source_lang", e.target.value)}
          />
        </div>
        <div className="space-y-1.5">
          <Label>Genre</Label>
          <Input
            value={analysis.genre}
            disabled={readOnly}
            onChange={(e) => set("genre", e.target.value)}
          />
        </div>
        <div className="space-y-1.5">
          <Label>Register</Label>
          <Select
            value={analysis.language_register}
            onValueChange={(v) => set("language_register", v as Register)}
            disabled={readOnly}
          >
            <SelectTrigger className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {REGISTERS.map((r) => (
                <SelectItem key={r} value={r}>
                  {r}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="space-y-1.5">
          <Label>Audience</Label>
          <Input
            value={analysis.audience}
            disabled={readOnly}
            onChange={(e) => set("audience", e.target.value)}
          />
        </div>
      </div>

      <div className="space-y-1.5">
        <Label>Tone</Label>
        <Textarea
          value={analysis.tone}
          disabled={readOnly}
          onChange={(e) => set("tone", e.target.value)}
          className="min-h-16"
        />
      </div>

      <div className="space-y-1.5">
        <div className="flex items-center justify-between">
          <Label>Style guide</Label>
          <Badge variant="outline" className="text-[10px]">
            {Math.round((analysis.confidence ?? 0) * 100)}% confidence
          </Badge>
        </div>
        <p className="text-xs text-muted-foreground">
          Injected into every translation call — the single biggest lever on output quality.
        </p>
        <Textarea
          value={analysis.style_guide}
          disabled={readOnly}
          onChange={(e) => set("style_guide", e.target.value)}
          className="min-h-32"
        />
      </div>

      <div className="space-y-1.5">
        <Label>Extra instructions</Label>
        <Textarea
          value={analysis.style_delta?.extra_instructions ?? ""}
          disabled={readOnly}
          placeholder="Book-specific overrides on top of the preset…"
          onChange={(e) =>
            set("style_delta", { ...analysis.style_delta, extra_instructions: e.target.value || null })
          }
          className="min-h-16"
        />
      </div>

      <div className="space-y-1.5">
        <Label>Reader notes</Label>
        <p className="text-xs text-muted-foreground">
          Translation hazards found while sampling: puns, verse, culture-bound references.
        </p>
        <div className="space-y-1.5">
          {notes.map((note, i) => (
            <div key={i} className="flex items-center gap-1.5">
              <Input
                value={note}
                disabled={readOnly}
                onChange={(e) => {
                  const next = [...notes];
                  next[i] = e.target.value;
                  set("reader_notes", next);
                }}
              />
              {!readOnly && (
                <Button
                  variant="ghost"
                  size="icon"
                  className="size-8 shrink-0"
                  onClick={() => set("reader_notes", notes.filter((_, idx) => idx !== i))}
                >
                  <X className="size-3.5" />
                </Button>
              )}
            </div>
          ))}
          {!readOnly && (
            <Button variant="outline" size="sm" onClick={() => set("reader_notes", [...notes, ""])}>
              <Plus className="size-3.5" />
              Add note
            </Button>
          )}
        </div>
      </div>
    </div>
  );
}
