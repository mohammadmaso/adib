"""Gate 3 — Review & Export: translation run, per-segment review, and the final
PDF/EPUB render.

Translation and export both run as background tasks (an LLM run over the whole
book, and a Typst/ebooklib compile, are both too slow for a request/response
cycle); segment listing and single-segment edits are synchronous store calls so
the review table feels instant.
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel

from adib_engine.agents.translate import translate_book
from adib_engine.api.deps import project_store
from adib_engine.api.progress import hub, pause_registry
from adib_engine.models.project import ProjectStage
from adib_engine.models.segment import Segment, SegmentStatus, SegmentUpdate
from adib_engine.segmentation import apply_translations
from adib_engine.settings import get_settings
from adib_engine.store.project_store import SOURCE, TARGET, ProjectStore, open_project
from adib_engine.store.provider_config import load_provider

log = logging.getLogger(__name__)

router = APIRouter(prefix="/projects", tags=["gate3"])


class ApiKeyBody(BaseModel):
    api_key: str | None = None


@router.post("/{project_id}/translate", status_code=202)
def start_translation(
    project_id: str,
    body: ApiKeyBody,
    background_tasks: BackgroundTasks,
    store: ProjectStore = Depends(project_store),
) -> dict[str, str]:
    meta = store.meta()
    allowed = (
        ProjectStage.TRANSLATING,
        ProjectStage.PAUSED,
        ProjectStage.REVIEW,
        ProjectStage.FAILED,
    )
    if meta.stage not in allowed:
        raise HTTPException(
            status_code=409, detail=f"cannot translate from stage '{meta.stage.value}'"
        )
    preset = store.preset()
    if preset is None:
        raise HTTPException(status_code=409, detail="no approved preset — finish Gate 2 first")

    pause_registry.clear(project_id)
    pending = store.pending_segments()
    background_tasks.add_task(_run_translate, project_id, store.path, body.api_key)
    return {"status": "started", "queued": str(len(pending))}


@router.post("/{project_id}/translate/pause", status_code=202)
def pause_translation(
    project_id: str, store: ProjectStore = Depends(project_store)
) -> dict[str, str]:
    """Ask an in-flight translation run to stop after its current segments.

    A no-op (but not an error) if nothing is running — the flag is simply
    cleared the next time a run starts.
    """
    if store.meta().stage != ProjectStage.TRANSLATING:
        raise HTTPException(status_code=409, detail="no translation run in progress")
    pause_registry.request(project_id)
    return {"status": "pausing"}


def _run_translate(project_id: str, path: Path, api_key: str | None) -> None:
    import asyncio

    with open_project(path) as store:
        hub.publish(project_id, {"stage": "translating", "percent": 0})
        try:
            provider = load_provider(get_settings())
            preset = store.preset()
            analysis = store.analysis()
            style_guide = analysis.style_guide if analysis else None
            total_pending = len(store.pending_segments())

            def on_segment(segment_id: str) -> None:
                remaining = len(store.pending_segments())
                done = total_pending - remaining
                percent = int(100 * done / total_pending) if total_pending else 100
                hub.publish(
                    project_id,
                    {"stage": "translating", "percent": percent, "segment_id": segment_id},
                )

            result = asyncio.run(
                translate_book(
                    store, provider, preset, api_key=api_key, style_guide=style_guide,
                    progress=on_segment,
                    should_pause=lambda: pause_registry.should_pause(project_id),
                )
            )
            pause_registry.clear(project_id)
            if result["paused"]:
                store.set_stage(ProjectStage.PAUSED)
                hub.publish(
                    project_id,
                    {
                        "stage": "paused",
                        "percent": int(100 * result["segments"] / max(total_pending, 1)),
                        "segments": result["segments"],
                    },
                )
            else:
                store.set_stage(ProjectStage.REVIEW)
                hub.publish(
                    project_id,
                    {
                        "stage": "review",
                        "percent": 100,
                        "segments": result["segments"],
                        "failed": len(result["failed"]),
                        "cost_usd": result["cost_usd"],
                    },
                )
        except Exception as exc:
            pause_registry.clear(project_id)
            log.exception("translation failed for project %s", project_id)
            reason = str(exc) or "translation run failed"
            store.update_meta(
                stage=ProjectStage.FAILED,
                failed_stage=ProjectStage.TRANSLATING,
                failed_reason=reason,
            )
            hub.publish(project_id, {"stage": "failed", "error": reason})


@router.get("/{project_id}/segments", response_model=list[Segment])
def list_segments(
    status: SegmentStatus | None = None,
    limit: int | None = None,
    offset: int = 0,
    store: ProjectStore = Depends(project_store),
) -> list[Segment]:
    return store.segments(status=status, limit=limit, offset=offset)


@router.get("/{project_id}/segments/counts", response_model=dict[str, int])
def segment_counts(store: ProjectStore = Depends(project_store)) -> dict[str, int]:
    return store.segment_counts()


@router.patch("/{project_id}/segments/{segment_id}", response_model=Segment)
def update_segment(
    segment_id: str, update: SegmentUpdate, store: ProjectStore = Depends(project_store)
) -> Segment:
    """A human edit or approval in Gate 3. Locks the segment against re-runs
    only when the caller explicitly sets `locked=True`."""
    try:
        return store.update_segment(segment_id, update)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{project_id}/segments/{segment_id}/retranslate", status_code=202)
def retranslate_segment(
    project_id: str,
    segment_id: str,
    body: ApiKeyBody,
    background_tasks: BackgroundTasks,
    store: ProjectStore = Depends(project_store),
) -> dict[str, str]:
    """Re-queue one segment and translate just it, without touching the rest."""
    seg = store.get_segment(segment_id)
    if seg is None:
        raise HTTPException(status_code=404, detail=f"no segment {segment_id}")
    if seg.locked:
        raise HTTPException(status_code=409, detail="segment is locked")

    store.update_segment(segment_id, SegmentUpdate(status=SegmentStatus.PENDING))
    background_tasks.add_task(_run_retranslate, project_id, store.path, segment_id, body.api_key)
    return {"status": "started"}


def _run_retranslate(project_id: str, path: Path, segment_id: str, api_key: str | None) -> None:
    import asyncio

    with open_project(path) as store:
        try:
            provider = load_provider(get_settings())
            preset = store.preset()
            analysis = store.analysis()
            style_guide = analysis.style_guide if analysis else None
            result = asyncio.run(
                translate_book(store, provider, preset, api_key=api_key, style_guide=style_guide)
            )
            hub.publish(
                project_id,
                {
                    "stage": "segment_retranslated",
                    "segment_id": segment_id,
                    "cost_usd": result["cost_usd"],
                },
            )
        except Exception:
            log.exception("retranslate failed for segment %s", segment_id)
            hub.publish(
                project_id, {"stage": "segment_retranslate_failed", "segment_id": segment_id}
            )


# -- export ------------------------------------------------------------------


class ExportRequest(BaseModel):
    formats: list[str] = ["pdf", "epub"]


@router.post("/{project_id}/export", status_code=202)
def start_export(
    project_id: str,
    body: ExportRequest,
    background_tasks: BackgroundTasks,
    store: ProjectStore = Depends(project_store),
) -> dict[str, str]:
    meta = store.meta()
    if meta.stage not in (ProjectStage.REVIEW, ProjectStage.DONE, ProjectStage.FAILED):
        raise HTTPException(
            status_code=409, detail=f"cannot export from stage '{meta.stage.value}'"
        )
    store.set_stage(ProjectStage.EXPORTING)
    background_tasks.add_task(_run_export, project_id, store.path, body.formats)
    return {"status": "started"}


def _run_export(project_id: str, path: Path, formats: list[str]) -> None:
    with open_project(path) as store:
        hub.publish(project_id, {"stage": "exporting", "percent": 0})
        try:
            source = store.load_tree(SOURCE)
            translations = store.translations()
            target_tree = apply_translations(source, translations)
            store.save_tree(TARGET, target_tree)

            preset = store.preset()
            meta = store.meta()
            out_dir = store.path.with_suffix(".export")
            out_dir.mkdir(parents=True, exist_ok=True)
            outputs: dict[str, str] = {}

            if "pdf" in formats:
                from adib_engine.render.typst.compile import compile_pdf
                from adib_engine.settings import get_settings

                settings = get_settings()
                out_pdf = compile_pdf(
                    target_tree,
                    preset,
                    target_lang=meta.target_lang,
                    assets_dir=store.assets_dir,
                    out_path=out_dir / "book.pdf",
                    typst_bin=str(settings.typst_bin) if settings.typst_bin else "typst",
                    fonts_dir=settings.bundled_fonts_dir,
                )
                outputs["pdf"] = str(out_pdf)
                hub.publish(project_id, {"stage": "exporting", "percent": 50, "pdf": str(out_pdf)})

            if "epub" in formats:
                from adib_engine.render.epub.compile import compile_epub
                from adib_engine.settings import get_settings

                settings = get_settings()
                out_epub = compile_epub(
                    target_tree,
                    preset,
                    target_lang=meta.target_lang,
                    assets_dir=store.assets_dir,
                    out_path=out_dir / "book.epub",
                    fonts_dir=settings.bundled_fonts_dir,
                )
                outputs["epub"] = str(out_epub)

            store.set_stage(ProjectStage.DONE)
            hub.publish(project_id, {"stage": "done", "percent": 100, "outputs": outputs})
        except Exception as exc:
            log.exception("export failed for project %s", project_id)
            reason = str(exc) or "export failed unexpectedly"
            store.update_meta(
                stage=ProjectStage.FAILED,
                failed_stage=ProjectStage.EXPORTING,
                failed_reason=reason,
            )
            hub.publish(project_id, {"stage": "failed", "error": reason})
