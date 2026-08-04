"""Gate 2 — Style & Glossary: analysis proposal and glossary build/edit.

Both the analyst and the glossary adjudicator call an LLM, so both run as
background tasks with progress on the SSE stream; everything else here (reading
back the analysis, editing glossary terms) is a plain synchronous call against
the store.
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel

from adib_engine.agents.analyst import analyze_book
from adib_engine.agents.glossary import adjudicate_glossary, decisions_to_terms
from adib_engine.api.deps import project_store
from adib_engine.api.progress import hub
from adib_engine.glossary.mine import mine_candidates
from adib_engine.models.analysis import BookAnalysis
from adib_engine.models.glossary import GlossaryTerm, GlossaryTermUpdate, TermPolicy
from adib_engine.models.preset import Preset, StyleDelta
from adib_engine.models.project import ProjectStage
from adib_engine.presets.library import PresetLibrary
from adib_engine.store.project_store import SOURCE, ProjectStore, open_project

log = logging.getLogger(__name__)

router = APIRouter(prefix="/projects", tags=["gate2"])


class ApiKeyBody(BaseModel):
    api_key: str | None = None


@router.post("/{project_id}/analyze", status_code=202)
def start_analysis(
    project_id: str,
    body: ApiKeyBody,
    background_tasks: BackgroundTasks,
    store: ProjectStore = Depends(project_store),
) -> dict[str, str]:
    meta = store.meta()
    allowed = (ProjectStage.STRUCTURE_REVIEW, ProjectStage.ANALYZING, ProjectStage.FAILED)
    if meta.stage not in allowed:
        raise HTTPException(
            status_code=409, detail=f"cannot analyze from stage '{meta.stage.value}'"
        )
    tree = store.load_tree(SOURCE)
    if tree is None:
        raise HTTPException(status_code=409, detail="no source tree — ingest first")

    store.set_stage(ProjectStage.ANALYZING)
    background_tasks.add_task(_run_analysis, project_id, store.path, body.api_key)
    return {"status": "started"}


def _run_analysis(project_id: str, path: Path, api_key: str | None) -> None:
    import asyncio

    with open_project(path) as store:
        hub.publish(project_id, {"stage": "analyzing", "percent": 0})
        try:
            tree = store.load_tree(SOURCE)
            provider = store.provider()
            result = asyncio.run(analyze_book(tree, provider, api_key=api_key))
            analysis = result.output
            store.set_analysis(analysis)
            store.record_usage(
                purpose="analysis",
                model=provider.model,
                prompt_tokens=result.prompt_tokens,
                completion_tokens=result.completion_tokens,
                cost_usd=_cost(provider, result.prompt_tokens, result.completion_tokens),
            )
            store.set_stage(ProjectStage.STYLE_REVIEW)
            hub.publish(
                project_id,
                {
                    "stage": "style_review",
                    "percent": 100,
                    "suggested_preset": analysis.suggested_preset,
                },
            )
        except Exception:
            log.exception("analysis failed for project %s", project_id)
            store.set_stage(ProjectStage.FAILED)
            hub.publish(project_id, {"stage": "failed", "error": "analysis failed unexpectedly"})


def _cost(provider, prompt_tokens: int, completion_tokens: int) -> float:
    from adib_engine.agents.base import cost_for

    return cost_for(provider, prompt_tokens, completion_tokens)


@router.get("/{project_id}/analysis", response_model=BookAnalysis)
def get_analysis(store: ProjectStore = Depends(project_store)) -> BookAnalysis:
    analysis = store.analysis()
    if analysis is None:
        raise HTTPException(status_code=404, detail="no analysis yet")
    return analysis


@router.put("/{project_id}/analysis", response_model=BookAnalysis)
def update_analysis(
    analysis: BookAnalysis, store: ProjectStore = Depends(project_store)
) -> BookAnalysis:
    """The user's edits in Gate 2 — accept-with-changes rather than raw acceptance."""
    store.set_analysis(analysis)
    return analysis


# -- glossary --------------------------------------------------------------


@router.post("/{project_id}/glossary/build", status_code=202)
def start_glossary_build(
    project_id: str,
    body: ApiKeyBody,
    background_tasks: BackgroundTasks,
    store: ProjectStore = Depends(project_store),
) -> dict[str, str]:
    tree = store.load_tree(SOURCE)
    if tree is None:
        raise HTTPException(status_code=409, detail="no source tree — ingest first")
    background_tasks.add_task(_run_glossary_build, project_id, store.path, body.api_key)
    return {"status": "started"}


def _run_glossary_build(project_id: str, path: Path, api_key: str | None) -> None:
    import asyncio

    with open_project(path) as store:
        hub.publish(project_id, {"stage": "glossary_mining", "percent": 0})
        try:
            tree = store.load_tree(SOURCE)
            candidates = mine_candidates(tree)
            hub.publish(
                project_id,
                {"stage": "glossary_adjudicating", "percent": 30, "mined": len(candidates)},
            )
            if not candidates:
                hub.publish(project_id, {"stage": "glossary_ready", "percent": 100, "kept": 0})
                return

            provider = store.provider()
            preset = store.preset()
            default_policy = preset.default_glossary_policy if preset else TermPolicy.TRANSLATE
            analysis = store.analysis()
            result = asyncio.run(
                adjudicate_glossary(
                    candidates,
                    provider,
                    target_lang=store.meta().target_lang,
                    api_key=api_key,
                    analysis=analysis,
                    default_policy=default_policy,
                )
            )
            terms = decisions_to_terms(
                candidates, result.output.decisions, default_policy=default_policy
            )
            added, updated, skipped = store.upsert_terms(terms)
            store.record_usage(
                purpose="glossary",
                model=provider.model,
                prompt_tokens=result.prompt_tokens,
                completion_tokens=result.completion_tokens,
                cost_usd=_cost(provider, result.prompt_tokens, result.completion_tokens),
            )
            hub.publish(
                project_id,
                {
                    "stage": "glossary_ready",
                    "percent": 100,
                    "added": added,
                    "updated": updated,
                    "skipped_locked": skipped,
                },
            )
        except Exception:
            log.exception("glossary build failed for project %s", project_id)
            hub.publish(project_id, {"stage": "glossary_failed", "error": "glossary build failed"})


@router.get("/{project_id}/glossary", response_model=list[GlossaryTerm])
def list_terms(
    enabled_only: bool = False, store: ProjectStore = Depends(project_store)
) -> list[GlossaryTerm]:
    return store.terms(enabled_only=enabled_only)


@router.post("/{project_id}/glossary", response_model=GlossaryTerm, status_code=201)
def add_term(term: GlossaryTerm, store: ProjectStore = Depends(project_store)) -> GlossaryTerm:
    """A manually added term — always `origin="user"` regardless of what was sent."""
    term.origin = "user"
    # Read before upsert: `upsert_terms` commits and closes its own session, and
    # `term` (a SQLModel table object) becomes detached afterwards — any
    # attribute access on it past that point re-triggers a DB load and raises.
    source = term.source
    added, updated, _skipped = store.upsert_terms([term])
    if not added and not updated:
        raise HTTPException(status_code=409, detail=f"term '{source}' is locked")
    matches = [t for t in store.terms() if t.source == source]
    return matches[0]


@router.patch("/{project_id}/glossary/{term_id}", response_model=GlossaryTerm)
def update_term(
    term_id: str, update: GlossaryTermUpdate, store: ProjectStore = Depends(project_store)
) -> GlossaryTerm:
    try:
        return store.update_term(term_id, update)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# -- gate 2 approval ---------------------------------------------------------


class Gate2Approval(BaseModel):
    preset_id: str
    style_delta: StyleDelta | None = None


@router.post("/{project_id}/gate2/approve", response_model=Preset)
def approve_style(body: Gate2Approval, store: ProjectStore = Depends(project_store)) -> Preset:
    """Resolve and persist the approved preset (base + style delta).

    Stored as a fully-resolved `Preset` so the run stays reproducible even if
    the built-in preset library changes later.
    """
    meta = store.meta()
    if meta.stage != ProjectStage.STYLE_REVIEW:
        raise HTTPException(
            status_code=409, detail=f"cannot approve style from stage '{meta.stage.value}'"
        )

    base = PresetLibrary().load(body.preset_id)
    if base is None:
        raise HTTPException(status_code=400, detail=f"unknown preset '{body.preset_id}'")
    resolved = body.style_delta.apply(base) if body.style_delta else base
    store.set_preset(resolved)
    store.set_stage(ProjectStage.TRANSLATING)
    return resolved
