"""Project CRUD: the Library and New Project screens.

Creating a project does not ingest — that is a separate call
(`POST /projects/{id}/ingest`) so New Project can show the probe result before
committing to a full parse of a possibly-large book.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from adib_engine.api.deps import project_path, project_store, registry_dep, settings_dep
from adib_engine.models.project import ProjectCreate, ProjectCreated, ProjectMeta, ProjectSummary
from adib_engine.settings import RuntimeSettings
from adib_engine.store.project_store import ProjectStore, create_project
from adib_engine.store.registry import ProjectRegistry

router = APIRouter(prefix="/projects", tags=["projects"])


@router.get("", response_model=list[ProjectSummary])
def list_projects(registry: ProjectRegistry = Depends(registry_dep)) -> list[ProjectSummary]:
    return registry.list()


@router.post("", response_model=ProjectCreated, status_code=201)
def create(
    body: ProjectCreate,
    registry: ProjectRegistry = Depends(registry_dep),
) -> ProjectCreated:
    if not Path(body.source_path).exists():
        raise HTTPException(status_code=400, detail=f"source file not found: {body.source_path}")

    path = registry.path_for(body.name)
    with create_project(
        path,
        name=body.name,
        source_path=body.source_path,
        target_lang=body.target_lang,
    ) as store:
        if body.preset_id:
            from adib_engine.presets.library import PresetLibrary

            preset = PresetLibrary().load(body.preset_id)
            if preset:
                store.set_preset(preset)
        return ProjectCreated(project_id=path.stem, meta=store.meta())


@router.get("/{project_id}", response_model=ProjectMeta)
def get(store: ProjectStore = Depends(project_store)) -> ProjectMeta:
    return store.meta()


@router.delete("/{project_id}", status_code=204)
def delete(
    project_id: str,
    path: Path = Depends(project_path),
    settings: RuntimeSettings = Depends(settings_dep),
) -> None:
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"no project '{project_id}'")
    path.unlink()
    assets_dir = path.with_suffix(".assets")
    if assets_dir.is_dir():
        import shutil

        shutil.rmtree(assets_dir, ignore_errors=True)
