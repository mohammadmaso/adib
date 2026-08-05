"""Cover translation: an independent action available any time after ingest,
not a pipeline gate — same footing as a segment retranslate in Gate 3.

Extracts the book's cover (staged at ingest, see `ingest/epub.py` and
`ingest/pdf.py`), sends it to the configured image provider, and stores the
result on `ProjectMeta` so export can pick it up (see `gate3.py`'s
`_run_export`).
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from adib_engine.agents.cover import translate_cover
from adib_engine.api.deps import project_store
from adib_engine.api.progress import hub
from adib_engine.models.document import Asset
from adib_engine.settings import get_settings
from adib_engine.store.image_provider_config import load_image_provider
from adib_engine.store.project_store import SOURCE, ProjectStore, open_project

log = logging.getLogger(__name__)

router = APIRouter(prefix="/projects", tags=["cover"])


class ApiKeyBody(BaseModel):
    api_key: str | None = None


class CoverStatus(BaseModel):
    has_source_cover: bool
    source_asset_id: str | None = None
    translated_asset_id: str | None = None


def _stage_cover_asset(assets_dir: Path, data: bytes, mime: str) -> Asset:
    """Write `data` under `assets_dir`, content-addressed like other staged
    assets (see `ingest/kit.py:make_asset_stager`), and return its `Asset`.

    Not routed through `make_asset_stager` because that helper dedupes against
    a `DocTree.assets` dict — the translated cover doesn't belong to one.
    """
    ext = mime.split("/")[-1] or "png"
    digest = hashlib.sha256(data).hexdigest()
    assets_dir.mkdir(parents=True, exist_ok=True)
    dest = assets_dir / f"{digest[:16]}.{ext}"
    if not dest.exists():
        dest.write_bytes(data)
    return Asset(id=f"asset-{digest[:16]}", path=dest.name, mime=mime, sha256=digest)


@router.get("/{project_id}/cover", response_model=CoverStatus)
def get_cover_status(store: ProjectStore = Depends(project_store)) -> CoverStatus:
    source = store.load_tree(SOURCE)
    meta = store.meta()
    translated = meta.translated_cover_asset
    return CoverStatus(
        has_source_cover=bool(source and source.meta.cover_asset_id),
        source_asset_id=source.meta.cover_asset_id if source else None,
        translated_asset_id=translated.get("id") if translated else None,
    )


@router.get("/{project_id}/assets/{asset_id}")
def get_asset(
    asset_id: str, store: ProjectStore = Depends(project_store)
) -> FileResponse:
    source = store.load_tree(SOURCE)
    meta = store.meta()

    asset = None
    if source and asset_id in source.assets:
        asset = source.assets[asset_id]
    elif meta.translated_cover_asset and meta.translated_cover_asset.get("id") == asset_id:
        asset = Asset.model_validate(meta.translated_cover_asset)

    if asset is None:
        raise HTTPException(status_code=404, detail=f"no asset {asset_id}")
    path = store.assets_dir / asset.path
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"asset {asset_id} has no file on disk")
    return FileResponse(path, media_type=asset.mime)


@router.post("/{project_id}/cover/translate", status_code=202)
def start_cover_translation(
    project_id: str,
    body: ApiKeyBody,
    background_tasks: BackgroundTasks,
    store: ProjectStore = Depends(project_store),
) -> dict[str, str]:
    source = store.load_tree(SOURCE)
    if source is None or not source.meta.cover_asset_id:
        raise HTTPException(status_code=409, detail="this project has no cover to translate")

    background_tasks.add_task(_run_translate_cover, project_id, store.path, body.api_key)
    return {"status": "started"}


def _run_translate_cover(project_id: str, path: Path, api_key: str | None) -> None:
    import asyncio

    with open_project(path) as store:
        hub.publish(project_id, {"stage": "cover_translating"})
        try:
            source = store.load_tree(SOURCE)
            meta = store.meta()
            cover_id = source.meta.cover_asset_id if source else None
            if not source or not cover_id:
                raise RuntimeError("no cover asset on the source tree")
            asset = source.assets[cover_id]
            image_path = store.assets_dir / asset.path
            image_bytes = image_path.read_bytes()

            provider = load_image_provider(get_settings())
            result = asyncio.run(
                translate_cover(
                    image_bytes,
                    asset.mime,
                    target_lang=meta.target_lang,
                    provider=provider,
                    api_key=api_key,
                )
            )
            translated = _stage_cover_asset(store.assets_dir, result.image_bytes, "image/png")
            store.update_meta(translated_cover_asset=translated.model_dump(mode="json"))
            hub.publish(
                project_id,
                {
                    "stage": "cover_translated",
                    "asset_id": translated.id,
                    "cost_usd": result.cost_usd,
                },
            )
        except Exception as exc:
            log.exception("cover translation failed for project %s", project_id)
            reason = str(exc) or "cover translation failed"
            hub.publish(project_id, {"stage": "cover_translate_failed", "error": reason})
