"""Read-only preset catalog for New Project and Gate 2's "choose a different
preset" control."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from adib_engine.models.preset import Preset
from adib_engine.presets.library import PresetLibrary

router = APIRouter(prefix="/presets", tags=["presets"])


@router.get("", response_model=list[Preset])
def list_presets() -> list[Preset]:
    return PresetLibrary().all()


@router.get("/{preset_id}", response_model=Preset)
def get_preset(preset_id: str) -> Preset:
    preset = PresetLibrary().load(preset_id)
    if preset is None:
        raise HTTPException(status_code=404, detail=f"no preset '{preset_id}'")
    return preset
