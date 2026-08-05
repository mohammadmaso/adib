"""The image provider config (base URL, model, size, ...) for cover
translation — an app-wide setting alongside the text provider's, persisted as
its own JSON file so the two can be configured and changed independently.

The API key is deliberately never part of this file — it lives in its own OS
keychain slot and is passed in per-call, same as the text provider's.
"""

from __future__ import annotations

import json

from adib_engine.models.project import ImageProviderSettings
from adib_engine.settings import RuntimeSettings

CONFIG_FILENAME = "image_provider.json"


def _config_path(settings: RuntimeSettings):
    return settings.data_dir / CONFIG_FILENAME


def load_image_provider(settings: RuntimeSettings) -> ImageProviderSettings:
    path = _config_path(settings)
    if not path.exists():
        return ImageProviderSettings()
    return ImageProviderSettings.model_validate_json(path.read_text())


def save_image_provider(
    settings: RuntimeSettings, provider: ImageProviderSettings
) -> ImageProviderSettings:
    path = _config_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(provider.model_dump(mode="json"), indent=2))
    return provider
