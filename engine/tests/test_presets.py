"""Preset library: built-ins load as valid `Preset`s, deltas apply, user shadowing."""

from __future__ import annotations

from pathlib import Path

import yaml

from adib_engine.models.preset import Register, StyleDelta, Typography
from adib_engine.presets import BUILTIN_IDS, PresetLibrary, load_all
from adib_engine.presets.library import bundled_dir


def test_all_builtins_report_properly():
    ps = load_all()
    assert [p.id for p in ps] == list(BUILTIN_IDS)
    for p in ps:
        assert p.name
        assert p.system_prompt
        assert isinstance(p.typography, Typography)


def test_every_builtin_yaml_is_a_complete_preset():
    """A tooltip/editor must be able to render any preset without partial data."""
    for p in load_all():
        # These are the fields the editor renders and the renderers require.
        assert p.typography.body_size_pt > 0
        assert p.typography.leading > 0
        assert p.typography.body_font
        assert p.default_glossary_policy is not None


def test_bundled_dir_is_on_disk_and_contains_the_ids():
    d = bundled_dir()
    assert d is not None
    ids = {f.stem for f in d.glob("*.yaml")}
    assert BUILTIN_IDS == tuple(sorted(ids, key=lambda x: list(BUILTIN_IDS).index(x)))


def test_style_delta_applies_without_mutating_the_base(tmp_path: Path):
    lib = PresetLibrary()
    base = lib.load("academic-paper")
    assert base is not None

    delta = StyleDelta(
        language_register=Register.NEUTRAL,
        extra_instructions="Keep prose vivid.",
        typography_overrides={"body_size_pt": 12.5},
    )
    tuned = delta.apply(base)

    assert tuned is not base
    assert tuned.language_register is Register.NEUTRAL
    assert tuned.typography.body_size_pt == 12.5
    assert "Keep prose vivid." in tuned.system_prompt
    # The base preset is pristine for the next book.
    assert base.language_register is Register.FORMAL
    assert base.typography.body_size_pt == 11.0
    assert base.system_prompt == tuned.system_prompt.replace("\n\nKeep prose vivid.", "")


def test_user_preset_shadows_builtin(tmp_path: Path):
    # A user drops a custom general.yaml with a different register.
    custom = {"id": "general", "name": "General", "system_prompt": "x",
              "language_register": "colloquial", "builtin": False}
    (tmp_path / "general.yaml").write_text(yaml.safe_dump(custom), encoding="utf-8")

    lib = PresetLibrary(user_dir=tmp_path)
    loaded = lib.load("general")
    assert loaded is not None
    assert loaded.language_register is Register.COLLOQUIAL  # user wins
    assert loaded.builtin is False

    # But the built-in still exists untouched for a fresh library.
    fresh = PresetLibrary()
    assert fresh.load("general").builtin is True


def test_unknown_preset_returns_none(tmp_path: Path):
    assert PresetLibrary(user_dir=tmp_path).load("no-such") is None
