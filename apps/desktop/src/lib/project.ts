import type { components } from "@adib/schema";

export type ProjectStage = components["schemas"]["ProjectStage"];

/**
 * The engine's `project_id` is the `.adib` file's stem — there is no separate
 * id field on `ProjectSummary`, only the full path on disk.
 */
export function projectIdFromPath(path: string): string {
  const file = path.split(/[/\\]/).pop() ?? path;
  return file.replace(/\.adib$/, "");
}

/** Where clicking a project card should land, based on its pipeline stage. */
export function routeForStage(stage: ProjectStage, projectId: string): string {
  switch (stage) {
    case "created":
    case "ingesting":
    case "structure_review":
    case "failed":
      return `/projects/${projectId}/structure`;
    case "analyzing":
    case "style_review":
      return `/projects/${projectId}/style`;
    case "translating":
    case "review":
    case "exporting":
    case "done":
      return `/projects/${projectId}/review`;
    default:
      return `/projects/${projectId}/structure`;
  }
}

export const STAGE_LABEL: Record<ProjectStage, string> = {
  created: "Created",
  ingesting: "Ingesting…",
  structure_review: "Structure review",
  analyzing: "Analyzing…",
  style_review: "Style & glossary",
  translating: "Translating…",
  review: "Review",
  exporting: "Exporting…",
  done: "Done",
  failed: "Failed",
};

export const STAGE_BADGE_VARIANT: Record<
  ProjectStage,
  "default" | "secondary" | "outline" | "destructive"
> = {
  created: "outline",
  ingesting: "secondary",
  structure_review: "default",
  analyzing: "secondary",
  style_review: "default",
  translating: "secondary",
  review: "default",
  exporting: "secondary",
  done: "outline",
  failed: "destructive",
};
