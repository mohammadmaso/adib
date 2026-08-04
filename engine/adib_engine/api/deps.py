"""Shared FastAPI dependencies: settings, the project registry, and per-request
store access.

Projects are files, not database rows, so "the project id" is just the `.adib`
file's stem inside `settings.projects_dir` — the registry already establishes
that convention via `path_for()`. Opening and closing a `ProjectStore` per
request keeps SQLite connections short-lived, which matters because the engine
also runs long background tasks against the same file.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from fastapi import Depends, HTTPException

from adib_engine.settings import RuntimeSettings, get_settings
from adib_engine.store.project_store import PROJECT_SUFFIX, ProjectStore, open_project
from adib_engine.store.registry import ProjectRegistry


def settings_dep() -> RuntimeSettings:
    return get_settings()


def registry_dep(settings: RuntimeSettings = Depends(settings_dep)) -> ProjectRegistry:
    return ProjectRegistry(settings.projects_dir)


def project_path(project_id: str, settings: RuntimeSettings = Depends(settings_dep)) -> Path:
    """The `.adib` path for a project id, without checking existence.

    Rejects path traversal since `project_id` becomes a filename directly.
    """
    if "/" in project_id or "\\" in project_id or project_id in (".", ".."):
        raise HTTPException(status_code=400, detail="invalid project id")
    return settings.projects_dir / f"{project_id}{PROJECT_SUFFIX}"


def project_store(path: Path = Depends(project_path)) -> Iterator[ProjectStore]:
    """Open the project for one request, closing it afterwards."""
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"no project '{path.stem}'")
    store = open_project(path)
    try:
        yield store
    finally:
        store.close()
