"""Provider settings (Settings screen) and the New Project probe.

The API key itself never appears here: it is stored in the OS keychain by the
Rust shell and passed to the engine per-call, never persisted in the project
file or logged (see ProviderSettings' docstring).
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from adib_engine.api.deps import project_store
from adib_engine.ingest.router import SUPPORTED_EXTENSIONS
from adib_engine.models.project import ProviderSettings
from adib_engine.store.project_store import ProjectStore

router = APIRouter(prefix="/projects", tags=["provider"])


@router.get("/{project_id}/provider", response_model=ProviderSettings)
def get_provider(store: ProjectStore = Depends(project_store)) -> ProviderSettings:
    return store.provider()


@router.put("/{project_id}/provider", response_model=ProviderSettings)
def set_provider(
    body: ProviderSettings, store: ProjectStore = Depends(project_store)
) -> ProviderSettings:
    store.set_provider(body)
    return body


probe_router = APIRouter(prefix="/probe", tags=["probe"])


@probe_router.get("")
def probe_file(path: str) -> dict:
    """Format/page-count/text-layer probe for the New Project screen, before
    a project (and thus a background ingest) is created."""
    p = Path(path)
    if not p.exists():
        raise HTTPException(status_code=400, detail=f"file not found: {path}")
    suffix = p.suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise HTTPException(
            status_code=400, detail=f"unsupported format: {suffix or '(no extension)'}"
        )

    if suffix == ".pdf":
        from adib_engine.ingest.pdf import probe_pdf

        probe = probe_pdf(p)
        return {
            "format": "pdf",
            "pages": probe.pages,
            "has_text_layer": probe.has_text_layer,
            "likely_scanned": probe.likely_scanned,
            "should_escalate": probe.should_escalate,
            "reason": probe.reason,
        }

    return {"format": suffix.lstrip("."), "pages": None, "has_text_layer": None}
