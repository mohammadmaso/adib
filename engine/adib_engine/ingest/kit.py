"""Shared helpers used by every format importer.

The importers live in this package and differ only in how they read a file;
they all funnel into the same asset staging and pipe-table conversion here so
those behave identically no matter the source format.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from pathlib import Path

from adib_engine.models.document import Asset, DocTree, TableCell, TableData

# -- asset staging ------------------------------------------------------------


def make_asset_stager(tree: DocTree, assets_dir: Path) -> Callable[..., str]:
    """Return a `stage(data, mime, ext?) -> asset_id` bound to one ingest.

    Files land under `assets_dir/<sha256-prefix>.<ext>` so the tree stays movable
    and blobs never touch SQLite. Staging is idempotent: the same bytes yield the
    same id and one file on disk.
    """

    def _suffix(mime: str) -> str:
        return {
            "image/png": "png",
            "image/jpeg": "jpg",
            "image/gif": "gif",
            "image/webp": "webp",
            "image/svg+xml": "svg",
            "image/bmp": "bmp",
        }.get(mime, "img")

    def stage(data: bytes, mime: str, ext: str | None = None) -> str:
        digest = hashlib.sha256(data).hexdigest()
        # Same decoration of the same image twice → reuse the already-staged file.
        for existing in tree.assets.values():
            if existing.sha256 == digest and existing.mime == mime:
                return existing.id
        suffix = ext or _suffix(mime)
        aid = f"asset-{digest[:16]}"
        assets_dir.mkdir(parents=True, exist_ok=True)
        dest = assets_dir / f"{digest[:16]}.{suffix}"
        if not dest.exists():
            dest.write_bytes(data)
        tree.assets[aid] = Asset(id=aid, path=dest.name, mime=mime, sha256=digest)
        return aid

    return stage


# -- table text -> TableData ---------------------------------------------------

_PIPE_SEP = re.compile(r"^\|[\s:|-]+\|$")


def table_from_pipe(lines: list[str], *, caption: str | None = None) -> TableData | None:
    """Convert markdown pipe-table lines into TableData.

    A separator row (`|---|---|`) makes the first row a header. Span metadata is
    not representable in markdown, so this yields a rectangular grid.
    """
    rows: list[list[str]] = []
    for line in lines:
        line = line.strip()
        if not line.startswith("|"):
            continue
        if _PIPE_SEP.match(line):
            continue
        cells = [c.strip().replace(r"\|", "|") for c in line.strip("|").split("|")]
        if cells:
            rows.append(cells)
    if not rows:
        return None
    cells = [TableCell(text=cell, is_header=False) for cell in rows[0]]
    table = TableData(rows=[cells], caption=caption)
    for row in rows[1:]:
        # pad uneven rows to match the header width
        row = row + [""] * (len(table.rows[0]) - len(row))
        table.rows.append([TableCell(text=c, is_header=False) for c in row])
    if _has_separator(lines):
        for cell in table.rows[0]:
            cell.is_header = True
    return table


def _has_separator(lines: list[str]) -> bool:
    return any(_PIPE_SEP.match(line.strip()) for line in lines if line)


def cleanup_inline(text: str) -> str:
    """Collapse runs of spaces/tabs and trim, without touching newlines."""
    return re.sub(r"[ \t]+", " ", text).strip()


__all__ = [
    "cleanup_inline",
    "make_asset_stager",
    "table_from_pipe",
]
