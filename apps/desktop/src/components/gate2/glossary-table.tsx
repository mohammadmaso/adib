import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Plus } from "lucide-react";
import { toast } from "sonner";
import { api, type components } from "@/lib/api-client";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Input } from "@/components/ui/input";
import { Checkbox } from "@/components/ui/checkbox";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Badge } from "@/components/ui/badge";

type GlossaryTerm = components["schemas"]["GlossaryTerm"];
type TermPolicy = components["schemas"]["TermPolicy"];

const POLICIES: TermPolicy[] = ["translate", "keep", "translate_paren", "footnote", "appendix"];

interface GlossaryTableProps {
  projectId: string;
  terms: GlossaryTerm[];
  readOnly: boolean;
}

export function GlossaryTable({ projectId, terms, readOnly }: GlossaryTableProps) {
  const queryClient = useQueryClient();
  const [newSource, setNewSource] = useState("");

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ["glossary", projectId] });

  const updateMutation = useMutation({
    mutationFn: async ({ id, patch }: { id: string; patch: components["schemas"]["GlossaryTermUpdate"] }) => {
      const { error } = await api.PATCH("/projects/{project_id}/glossary/{term_id}", {
        params: { path: { project_id: projectId, term_id: id } },
        body: patch,
      });
      if (error) throw error;
    },
    onSuccess: invalidate,
    onError: (error: unknown) => {
      toast.error(error instanceof Error ? error.message : "Failed to update term");
    },
  });

  const addMutation = useMutation({
    mutationFn: async (source: string) => {
      const { error } = await api.POST("/projects/{project_id}/glossary", {
        params: { path: { project_id: projectId } },
        body: {
          id: "",
          source,
          policy: "translate",
          frequency: 0,
          locked: false,
          enabled: true,
          origin: "user",
        },
      });
      if (error) throw error;
    },
    onSuccess: () => {
      setNewSource("");
      invalidate();
    },
    onError: (error: unknown) => {
      toast.error(error instanceof Error ? error.message : "Failed to add term");
    },
  });

  return (
    <div className="space-y-3">
      {!readOnly && (
        <div className="flex items-center gap-2">
          <Input
            placeholder="Add a term…"
            value={newSource}
            onChange={(e) => setNewSource(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && newSource.trim()) addMutation.mutate(newSource.trim());
            }}
            className="max-w-64"
          />
          <Button
            variant="outline"
            size="sm"
            disabled={!newSource.trim() || addMutation.isPending}
            onClick={() => addMutation.mutate(newSource.trim())}
          >
            <Plus className="size-3.5" />
            Add
          </Button>
        </div>
      )}

      <div className="max-h-[28rem] overflow-y-auto rounded-lg border border-border">
        <Table>
          <TableHeader className="sticky top-0 bg-background">
            <TableRow>
              <TableHead>Source</TableHead>
              <TableHead>Target</TableHead>
              <TableHead>Policy</TableHead>
              <TableHead className="text-right">Freq.</TableHead>
              <TableHead>Origin</TableHead>
              <TableHead className="text-center">Enabled</TableHead>
              <TableHead className="text-center">Locked</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {terms.length === 0 && (
              <TableRow>
                <TableCell colSpan={7} className="py-8 text-center text-sm text-muted-foreground">
                  No glossary terms yet.
                </TableCell>
              </TableRow>
            )}
            {terms.map((term) => (
              <TableRow key={term.id} className={!term.enabled ? "opacity-50" : undefined}>
                <TableCell className="font-medium">{term.source}</TableCell>
                <TableCell>
                  <Input
                    defaultValue={term.target ?? ""}
                    disabled={readOnly}
                    className="h-7 text-sm"
                    onBlur={(e) => {
                      if (e.target.value !== (term.target ?? "")) {
                        updateMutation.mutate({ id: term.id, patch: { target: e.target.value || null } });
                      }
                    }}
                  />
                </TableCell>
                <TableCell>
                  <Select
                    value={term.policy}
                    disabled={readOnly}
                    onValueChange={(v) =>
                      updateMutation.mutate({ id: term.id, patch: { policy: v as TermPolicy } })
                    }
                  >
                    <SelectTrigger size="sm" className="h-7 text-xs">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {POLICIES.map((p) => (
                        <SelectItem key={p} value={p}>
                          {p}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </TableCell>
                <TableCell className="text-right text-muted-foreground">{term.frequency}</TableCell>
                <TableCell>
                  <Badge variant="outline" className="text-[10px]">
                    {term.origin}
                  </Badge>
                </TableCell>
                <TableCell className="text-center">
                  <Checkbox
                    checked={term.enabled}
                    disabled={readOnly}
                    onCheckedChange={(checked) =>
                      updateMutation.mutate({ id: term.id, patch: { enabled: checked } })
                    }
                  />
                </TableCell>
                <TableCell className="text-center">
                  <Checkbox
                    checked={term.locked}
                    disabled={readOnly}
                    onCheckedChange={(checked) =>
                      updateMutation.mutate({ id: term.id, patch: { locked: checked } })
                    }
                  />
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
    </div>
  );
}
