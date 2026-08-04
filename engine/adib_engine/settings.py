"""Process-wide runtime settings, populated once at startup by ``__main__``."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class RuntimeSettings:
    """Settings handed to the engine by its parent (the Tauri shell) or the CLI."""

    host: str = "127.0.0.1"
    port: int = 0
    auth_token: str = ""
    data_dir: Path = field(default_factory=lambda: Path.home() / ".adib")

    # Seconds without a heartbeat from the parent before the engine exits on its own.
    # Guards against orphaned sidecars when the shell is killed rather than closed.
    # Zero disables the watchdog, which is what standalone/dev runs want.
    heartbeat_timeout: float = 0.0

    #: Path to the typst binary. None means "look up `typst` on PATH", which is
    #: what a plain `uv run` dev setup expects; the Tauri shell always passes
    #: the bundled sidecar's own path explicitly.
    typst_bin: Path | None = None
    #: Directories to search for fonts, in addition to the system's own. The
    #: Tauri shell passes its bundled resources/fonts/ here.
    bundled_fonts_dir: Path | None = None

    @property
    def projects_dir(self) -> Path:
        return self.data_dir / "projects"

    @property
    def fonts_dir(self) -> Path:
        return self.data_dir / "fonts"

    def ensure_dirs(self) -> None:
        for d in (self.data_dir, self.projects_dir, self.fonts_dir):
            d.mkdir(parents=True, exist_ok=True)


_settings = RuntimeSettings()


def get_settings() -> RuntimeSettings:
    return _settings


def configure(settings: RuntimeSettings) -> RuntimeSettings:
    """Replace the process-wide settings. Called once, before the app starts."""
    global _settings
    _settings = settings
    _settings.ensure_dirs()
    return _settings
