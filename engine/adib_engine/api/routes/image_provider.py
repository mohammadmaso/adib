"""Image provider settings (Settings screen), for cover translation.

Same split as `routes/provider.py`: the API key never appears here, it is
stored in the OS keychain by the Rust shell (a separate slot from the text
provider's key) and passed to the engine per-call.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from adib_engine.api.deps import settings_dep
from adib_engine.models.project import ImageProviderSettings
from adib_engine.settings import RuntimeSettings
from adib_engine.store.image_provider_config import load_image_provider, save_image_provider

router = APIRouter(prefix="/image-provider", tags=["image-provider"])


@router.get("", response_model=ImageProviderSettings)
def get_image_provider(settings: RuntimeSettings = Depends(settings_dep)) -> ImageProviderSettings:
    return load_image_provider(settings)


@router.put("", response_model=ImageProviderSettings)
def set_image_provider(
    body: ImageProviderSettings, settings: RuntimeSettings = Depends(settings_dep)
) -> ImageProviderSettings:
    return save_image_provider(settings, body)
