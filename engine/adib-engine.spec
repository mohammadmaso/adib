# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller build spec for the `adib-engine` sidecar.

Run via `scripts/prepare-binaries.sh --release` (from the repo root), which
also copies the result into `apps/desktop/src-tauri/binaries/` under the
target-triple name Tauri's `externalBin` expects. Not meant to be invoked
directly except for debugging a packaging problem in isolation:

    cd engine && uv run pyinstaller --clean --noconfirm adib-engine.spec

`docling` (the optional OCR-escalation parser) is deliberately excluded: it's
an opt-in extra (`pip install adib-engine[docling]`) pulling in a heavy ML
stack, not installed in the base dev env this spec builds from, and not a
dependency this build should silently go looking for.
"""

from pathlib import Path

from PyInstaller.utils.hooks import copy_metadata

root = Path(SPECPATH)

# The only non-Python data the engine reads off disk at runtime: the built-in
# preset library. Everything else (Jinja/Typst templates, etc.) lives as
# Python string constants, not files, so needs no `datas` entry.
preset_dir = root / "adib_engine" / "presets" / "builtins"
datas = [(str(p), "adib_engine/presets/builtins") for p in sorted(preset_dir.glob("*.yaml"))]

# Packages that read their own version via `importlib.metadata` at import
# time — PyInstaller doesn't bundle a package's `.dist-info` unless asked,
# so without this they raise `PackageNotFoundError` the moment they're
# imported inside the frozen build (they import fine unfrozen, where the
# real `.dist-info` is on disk).
for pkg in ("genai_prices", "pydantic_ai", "pydantic_ai_slim"):
    datas += copy_metadata(pkg)

a = Analysis(
    ["entrypoint.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["docling", "torch", "transformers", "tkinter"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

# Onefile, not onedir: Tauri's `externalBin` treats a sidecar as one
# executable at `binaries/adib-engine-<target-triple>` — a onedir build's
# `dist/adib-engine/adib-engine` looks like a single file but silently
# depends on the `_internal/` directory sitting next to it, which nothing
# in `scripts/prepare-binaries.sh` (or Tauri's sidecar spawn) would carry
# along. Onefile self-extracts to a temp dir on each launch (a small,
# one-time startup cost) but is genuinely one portable file.
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="adib-engine",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
