"""The preset library: built-in YAML presets plus user overrides.

The analysis agent proposes the closest preset id from here; the user can accept
it (and the agent's style delta) or pick another. User presets live in the app
data dir and shadow built-ins of the same id, exactly like the plan's "merge
over built-ins" note.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from adib_engine.models.preset import Preset

#: Built-in presets ship beside this module so a frozen PyInstaller bundle can
#: find them with `importlib.resources` rather than a fragile cwd-relative path.
_BUNDLED = "builtins"

#: The seven presets every install ships with (see docs/presets.md).
BUILTIN_IDS = (
    "academic-paper",
    "technical-manual",
    "literary-fiction",
    "business-nonfiction",
    "religious-classical",
    "childrens",
    "general",
)


class PresetLibrary:
    """A read-mostly collection of presets.

    Built-ins are immutable and re-readable from disk each listing so a user
    editing a YAML file does not need a restart. User presets shadow built-ins
    with the same id.
    """

    def __init__(self, user_dir: Path | None = None) -> None:
        self.bundled_dir = bundled_dir()
        self.user_dir = Path(user_dir) if user_dir else None

    def _paths(self) -> list[Path]:
        paths = list(sorted(self.bundled_dir.glob("*.yaml"))) if self.bundled_dir else []
        if self.user_dir and self.user_dir.is_dir():
            paths += list(sorted(self.user_dir.glob("*.yaml")))
        return paths

    def load(self, preset_id: str) -> Preset | None:
        # User shadows built-in: search user_dir first.
        for d in ([self.user_dir, self.bundled_dir] if self.user_dir else [self.bundled_dir]):
            if d is None:
                continue
            p = d / f"{preset_id}.yaml"
            if p.exists():
                return _load_yaml(p)
        return None

    def ids(self) -> list[str]:
        """Every preset id, built-in order first, user-added after."""
        ids = list(BUILTIN_IDS)
        if self.user_dir and self.user_dir.is_dir():
            for p in sorted(self.user_dir.glob("*.yaml")):
                pid = p.stem
                if pid not in ids:
                    ids.append(pid)
        return ids

    def all(self) -> list[Preset]:
        return [p for pid in self.ids() if (p := self.load(pid))]


def bundled_dir() -> Path | None:
    """Directory holding built-in YAML presets, wherever the wheel landed."""
    p = Path(__file__).resolve().parent / _BUNDLED
    return p if p.is_dir() else None


def _load_yaml(path: Path) -> Preset:
    with path.open(encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    return Preset.model_validate(raw)


def load_all(user_dir: Path | None = None) -> list[Preset]:
    return PresetLibrary(user_dir).all()
