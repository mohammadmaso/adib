"""Ingest: parse the source file into a `DocTree` and advance to Gate 1.

Runs in the background because a large PDF/Docling parse can take a while; the
webview watches `/projects/{id}/events` for progress and polls
`/projects/{id}` for the stage flip to `structure_review`.
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException

from adib_engine.api.deps import project_store
from adib_engine.api.progress import hub
from adib_engine.ingest.router import ScannedPdfError, UnsupportedFormatError, route
from adib_engine.models.project import ProjectStage
from adib_engine.store.project_store import ProjectStore, open_project

log = logging.getLogger(__name__)

router = APIRouter(prefix="/projects", tags=["ingest"])


@router.post("/{project_id}/ingest", status_code=202)
def start_ingest(
    project_id: str,
    background_tasks: BackgroundTasks,
    force_docling: bool = False,
    store: ProjectStore = Depends(project_store),
) -> dict[str, str]:
    meta = store.meta()
    if meta.stage not in (ProjectStage.CREATED, ProjectStage.FAILED):
        raise HTTPException(
            status_code=409, detail=f"project is already at stage '{meta.stage.value}'"
        )
    path = store.path
    store.set_stage(ProjectStage.INGESTING)
    background_tasks.add_task(_run_ingest, project_id, path, force_docling)
    return {"status": "started"}


def _run_ingest(project_id: str, path: Path, force_docling: bool) -> None:
    with open_project(path) as store:
        hub.publish(project_id, {"stage": "ingesting", "percent": 0})
        try:
            tree, report = route(
                Path(store.meta().source_path),
                assets_dir=store.assets_dir,
                force_docling=force_docling,
            )
            tree.meta.source_lang = tree.meta.source_lang or None
            store.save_tree("source", tree)
            added, kept, removed = store.sync_segments(tree)
            store.update_meta(
                stage=ProjectStage.STRUCTURE_REVIEW,
                source_format=report.format,
                source_lang=tree.meta.source_lang,
            )
            hub.publish(
                project_id,
                {
                    "stage": "structure_review",
                    "percent": 100,
                    "segments_added": added,
                    "segments_kept": kept,
                    "segments_removed": removed,
                    "parser": report.parser,
                    "escalated": report.escalated,
                },
            )
        except (UnsupportedFormatError, ScannedPdfError) as exc:
            store.update_meta(
                stage=ProjectStage.FAILED,
                failed_stage=ProjectStage.INGESTING,
                failed_reason=str(exc),
            )
            hub.publish(project_id, {"stage": "failed", "error": str(exc)})
        except Exception:
            log.exception("ingest failed for project %s", project_id)
            store.update_meta(
                stage=ProjectStage.FAILED,
                failed_stage=ProjectStage.INGESTING,
                failed_reason="ingest failed unexpectedly",
            )
            hub.publish(project_id, {"stage": "failed", "error": "ingest failed unexpectedly"})
