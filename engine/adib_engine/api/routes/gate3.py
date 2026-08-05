"""Gate 3 — Review & Export: translation run, per-segment review, and the final
PDF/EPUB render.

Translation and export both run as background tasks (an LLM run over the whole
book, and a Typst/ebooklib compile, are both too slow for a request/response
cycle); segment listing and single-segment edits are synchronous store calls so
the review table feels instant.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel

from adib_engine.agents.translate import translate_book
from adib_engine.api.deps import project_store
from adib_engine.api.progress import hub, pause_registry
from adib_engine.models.document import Asset
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
    # Mark the run as in flight *before* the background task starts: the stage is
    # what the UI polls to show the pause button, and what `pause` checks to know
    # a run exists. Resuming a paused (or failed) project would otherwise leave
    # the stage at `paused`/`failed` for the whole second run, making it
    # unpausable and invisible to the progress view.
    store.update_meta(
        stage=ProjectStage.TRANSLATING, failed_stage=None, failed_reason=None
    )
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

            done_count = 0

            def on_segment(segment_id: str) -> None:
                # Counted locally rather than re-querying `pending_segments()`:
                # that is a full scan of the segment table per finished segment.
                nonlocal done_count
                done_count += 1
                percent = int(100 * done_count / total_pending) if total_pending else 100
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
                translate_book(
                    store,
                    provider,
                    preset,
                    api_key=api_key,
                    style_guide=style_guide,
                    # Just this segment: a re-run of every other pending segment
                    # is not what the user asked for, and not what they'd expect
                    # to pay for.
                    only_ids={segment_id},
                )
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


#: Characters no mainstream filesystem accepts in a name, plus the separators
#: that would let a "filename" escape the directory the user picked.
_UNSAFE_IN_FILENAME = re.compile(r'[\\/:*?"<>|\x00-\x1f]')

VALID_FORMATS = ("pdf", "epub")


class ExportRequest(BaseModel):
    formats: list[str] = ["pdf", "epub"]
    #: Absolute directory to write into. None means the project's own `.export`
    #: folder, which is where exports landed before this was selectable.
    out_dir: str | None = None
    #: Base name for the written files, without extension. None means the
    #: project's name.
    filename: str | None = None


class ExportTarget(BaseModel):
    """Where an export would go, so the UI can show it before anything runs."""

    directory: str
    filename: str
    #: Full paths that would be written, keyed by format — what the user is
    #: about to overwrite, spelled out rather than implied.
    files: dict[str, str]
    #: Formats whose file is already there and would be replaced.
    existing: list[str] = []


def safe_filename(name: str | None, fallback: str) -> str:
    """A user-supplied base name reduced to something safe to write.

    Separators are stripped rather than rejected: the name comes from a text
    field next to a folder picker, and someone typing "my/book" means a name,
    not a subdirectory.
    """
    cleaned = _UNSAFE_IN_FILENAME.sub("", (name or "").strip()).strip(" .")
    if cleaned.lower().endswith((".pdf", ".epub")):
        cleaned = cleaned.rsplit(".", 1)[0].strip()
    return cleaned[:120] or fallback


def _resolve_target(
    store: ProjectStore, body: ExportRequest, *, create: bool = False
) -> tuple[Path, str]:
    """Validate the requested destination, or fall back to the project's own.

    Raises HTTPException so a bad path is refused while the user is still
    looking at the export panel, instead of failing the whole background run.
    `create` stays off for the preview endpoint: showing someone where a file
    would go should not start making folders for it.
    """
    default_dir = store.path.with_suffix(".export")
    stem = safe_filename(body.filename, safe_filename(store.meta().name, "book"))

    if not body.out_dir:
        return default_dir, stem

    out_dir = Path(body.out_dir).expanduser()
    if not out_dir.is_absolute():
        raise HTTPException(status_code=400, detail="export folder must be an absolute path")
    if out_dir.exists() and not out_dir.is_dir():
        raise HTTPException(status_code=400, detail=f"{out_dir} is not a folder")
    if create:
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise HTTPException(
                status_code=400, detail=f"cannot write to {out_dir}: {exc.strerror or exc}"
            ) from exc
    if out_dir.exists() and not os.access(out_dir, os.W_OK):
        raise HTTPException(status_code=400, detail=f"no permission to write to {out_dir}")
    return out_dir, stem


@router.get("/{project_id}/export/target", response_model=ExportTarget)
def export_target(
    out_dir: str | None = None,
    filename: str | None = None,
    formats: str = "pdf,epub",
    store: ProjectStore = Depends(project_store),
) -> ExportTarget:
    """Resolve a proposed destination without exporting anything.

    Lets the panel show the real paths (and validate a picked folder) before
    the user commits to a run that takes a while.
    """
    wanted = [f for f in formats.split(",") if f in VALID_FORMATS]
    directory, stem = _resolve_target(
        store, ExportRequest(out_dir=out_dir, filename=filename, formats=wanted)
    )
    files = {f: directory / f"{stem}.{f}" for f in wanted or VALID_FORMATS}
    return ExportTarget(
        directory=str(directory),
        filename=stem,
        files={f: str(p) for f, p in files.items()},
        existing=[f for f, p in files.items() if p.exists()],
    )


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
    formats = [f for f in body.formats if f in VALID_FORMATS]
    if not formats:
        raise HTTPException(status_code=400, detail="pick at least one of: pdf, epub")

    out_dir, stem = _resolve_target(store, body, create=True)
    store.set_stage(ProjectStage.EXPORTING)
    background_tasks.add_task(_run_export, project_id, store.path, formats, out_dir, stem)
    return {"status": "started", "directory": str(out_dir), "filename": stem}


def _export_failure_reason(exc: Exception) -> str:
    """The message the Gate 3 failure banner shows.

    A bare `str(exc)` on a typst failure is just "typst compile failed (exit 1)",
    which tells the user nothing they can act on; the diagnostic they need is in
    the compiler's stderr.
    """
    from adib_engine.render.typst.compile import TypstCompileError

    if isinstance(exc, TypstCompileError):
        detail = (exc.stderr or "").strip().splitlines()
        tail = " ".join(detail[-4:]) if detail else ""
        return f"{exc} — {tail}" if tail else str(exc)
    return str(exc) or "export failed unexpectedly"


def _run_export(
    project_id: str, path: Path, formats: list[str], out_dir: Path, stem: str
) -> None:
    with open_project(path) as store:
        hub.publish(project_id, {"stage": "exporting", "percent": 0})
        try:
            source = store.load_tree(SOURCE)
            translations = store.translations()
            target_tree = apply_translations(source, translations)

            preset = store.preset()
            meta = store.meta()
            if meta.translated_cover_asset:
                # `apply_translations` deep-copies meta/assets from `source`, so
                # without this the export would carry the untranslated cover
                # even after a successful cover translation.
                cover_asset = Asset.model_validate(meta.translated_cover_asset)
                target_tree.assets[cover_asset.id] = cover_asset
                target_tree.meta.cover_asset_id = cover_asset.id
            store.save_tree(TARGET, target_tree)
            out_dir.mkdir(parents=True, exist_ok=True)
            outputs: dict[str, str] = {}
            # Merging the translated tree is roughly a tenth of the work; the
            # rest is split evenly across the formats actually being written, so
            # the bar moves for an EPUB-only run too.
            step = (100 - 10) // max(len(formats), 1)
            percent = 10
            hub.publish(project_id, {"stage": "exporting", "percent": percent})

            if "pdf" in formats:
                from adib_engine.render.typst.compile import compile_pdf
                from adib_engine.settings import get_settings

                settings = get_settings()
                out_pdf = compile_pdf(
                    target_tree,
                    preset,
                    target_lang=meta.target_lang,
                    assets_dir=store.assets_dir,
                    out_path=out_dir / f"{stem}.pdf",
                    typst_bin=str(settings.typst_bin) if settings.typst_bin else "typst",
                    fonts_dir=settings.bundled_fonts_dir,
                )
                outputs["pdf"] = str(out_pdf)
                percent += step
                hub.publish(
                    project_id, {"stage": "exporting", "percent": percent, "pdf": str(out_pdf)}
                )

            if "epub" in formats:
                from adib_engine.render.epub.compile import compile_epub
                from adib_engine.settings import get_settings

                settings = get_settings()
                out_epub = compile_epub(
                    target_tree,
                    preset,
                    target_lang=meta.target_lang,
                    assets_dir=store.assets_dir,
                    out_path=out_dir / f"{stem}.epub",
                    fonts_dir=settings.bundled_fonts_dir,
                )
                outputs["epub"] = str(out_epub)
                percent += step
                hub.publish(
                    project_id, {"stage": "exporting", "percent": percent, "epub": str(out_epub)}
                )

            store.set_stage(ProjectStage.DONE)
            hub.publish(
                project_id,
                {
                    "stage": "done",
                    "percent": 100,
                    "outputs": outputs,
                    "directory": str(out_dir),
                },
            )
        except Exception as exc:
            log.exception("export failed for project %s", project_id)
            reason = _export_failure_reason(exc)
            store.update_meta(
                stage=ProjectStage.FAILED,
                failed_stage=ProjectStage.EXPORTING,
                failed_reason=reason,
            )
            hub.publish(project_id, {"stage": "failed", "error": reason})
