"""The provider config (base URL, model, concurrency, spend cap, ...) is a
single app-wide setting, not per-project — every project translates through
whatever endpoint the user has configured in Settings. Persisted as one JSON
file in the data dir, alongside `projects/` and `fonts/`, so it survives
restarts without needing a database of its own.

The API key is deliberately never part of this file — it lives in the OS
keychain via the Tauri shell and is passed in per-call.
"""

from __future__ import annotations

import json

from adib_engine.models.project import ProviderSettings
from adib_engine.settings import RuntimeSettings

CONFIG_FILENAME = "provider.json"


def _config_path(settings: RuntimeSettings):
    return settings.data_dir / CONFIG_FILENAME


def load_provider(settings: RuntimeSettings) -> ProviderSettings:
    path = _config_path(settings)
    if not path.exists():
        return ProviderSettings()
    return ProviderSettings.model_validate_json(path.read_text())


def save_provider(settings: RuntimeSettings, provider: ProviderSettings) -> ProviderSettings:
    path = _config_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(provider.model_dump(mode="json"), indent=2))
    return provider
