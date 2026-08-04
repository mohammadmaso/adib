"""Orchestrates one PDF render: DocTree + Preset -> book.pdf via the typst binary.

The `.typ` source is written directly into the project's assets directory so
every `image("...")` reference in the generated document can be a bare relative
filename — the renderer never has to reconcile Typst's `--root` semantics with
where the project happens to live on disk.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from adib_engine.models.document import DocTree
from adib_engine.models.preset import Preset
from adib_engine.render.typst.document import tree_to_typst_body
from adib_engine.render.typst.theme import build_theme


class TypstCompileError(RuntimeError):
    """typst compile exited non-zero. `stderr` carries the diagnostic."""

    def __init__(self, message: str, *, stderr: str, typ_path: Path):
        super().__init__(message)
        self.stderr = stderr
        self.typ_path = typ_path


def render_typst_source(
    tree: DocTree, preset: Preset, *, target_lang: str, assets_dir: Path | None = None
) -> str:
    """Build the full `.typ` document: theme preamble + body, from one tree."""
    theme = build_theme(preset.typography, target_lang=target_lang)
    body = tree_to_typst_body(tree, assets_dir)
    return f"{theme}\n{body}\n"


def compile_pdf(
    tree: DocTree,
    preset: Preset,
    *,
    target_lang: str,
    assets_dir: Path,
    out_path: Path,
    typst_bin: Path | str = "typst",
    fonts_dir: Path | None = None,
) -> Path:
    """Render `tree` to a PDF at `out_path` using the bundled typst binary.

    `assets_dir` must be the directory holding the tree's staged images (see
    `ingest.kit.make_asset_stager`); the `.typ` source is written there too so
    every image reference resolves as a bare relative filename.
    """
    assets_dir.mkdir(parents=True, exist_ok=True)
    typ_path = assets_dir / "book.typ"
    source = render_typst_source(tree, preset, target_lang=target_lang, assets_dir=assets_dir)
    typ_path.write_text(source, encoding="utf-8")

    out_path.parent.mkdir(parents=True, exist_ok=True)

    cmd = [str(typst_bin), "compile"]
    if fonts_dir is not None:
        cmd += ["--font-path", str(fonts_dir)]
    cmd += [str(typ_path), str(out_path)]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise TypstCompileError(
            f"typst compile failed (exit {result.returncode})",
            stderr=result.stderr,
            typ_path=typ_path,
        )
    return out_path


__all__ = ["TypstCompileError", "compile_pdf", "render_typst_source"]
