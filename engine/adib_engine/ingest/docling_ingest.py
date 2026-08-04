"""Docling fallback: PDFs with weak text layers or complex layout, plus
DOCX/HTML/PPTX/XLSX that pymupdf's fast path can't touch.

This module is only imported when `router.route()` decides to escalate, so the
heavy ML weights never load on the fast path. `docling` itself is an optional
dependency (`pip install adib-engine[docling]`); `router._route_docling` turns
its absence into a clear `UnsupportedFormatError` rather than a traceback.
"""

from __future__ import annotations

from pathlib import Path

from adib_engine.ingest.kit import cleanup_inline, make_asset_stager
from adib_engine.models.document import (
    AssetRef,
    DocNode,
    DocTree,
    NodeKind,
    TableCell,
    TableData,
    make_node_id,
)

#: DoclingDocument item labels we treat as headings, keyed by nesting depth
#: reported by `iterate_items()`.
_HEADING_LABELS = {"title", "section_header"}
_CODE_LABELS = {"code"}
_LIST_LABELS = {"list_item"}


def ingest_docling(path: Path, *, assets_dir: Path | None = None) -> DocTree:
    from docling.datamodel.base_models import InputFormat  # noqa: F401
    from docling.document_converter import DocumentConverter

    converter = DocumentConverter()
    result = converter.convert(str(path))
    ddoc = result.document

    tree = DocTree(parser="docling")
    stage = make_asset_stager(tree, assets_dir or path.parent / f"{path.stem}.assets")

    title = getattr(ddoc, "name", None) or getattr(ddoc, "title", None)
    if title:
        tree.meta.title = str(title)

    nodes: list[DocNode] = []
    for item, level in ddoc.iterate_items():
        node = _convert_item(item, level, stage, len(nodes))
        if node is not None:
            nodes.append(node)

    tree.nodes = nodes
    return tree


def _convert_item(item, level: int, stage, ordinal: int) -> DocNode | None:
    label = str(getattr(item, "label", "")).lower()

    if label == "table" or item.__class__.__name__ == "TableItem":
        return _table_node(item, ordinal)

    if label == "picture" or item.__class__.__name__ == "PictureItem":
        return _picture_node(item, stage, ordinal)

    text = cleanup_inline(getattr(item, "text", "") or "")
    if not text:
        return None

    if label in _HEADING_LABELS:
        return DocNode(
            id=make_node_id(NodeKind.HEADING, text, ordinal),
            kind=NodeKind.HEADING,
            text=text,
            level=min(max(level, 1), 6),
        )
    if label in _CODE_LABELS:
        return DocNode(
            id=make_node_id(NodeKind.CODE, text, ordinal),
            kind=NodeKind.CODE,
            text=text,
        )
    if label in _LIST_LABELS:
        return DocNode(
            id=make_node_id(NodeKind.LIST_ITEM, text, ordinal),
            kind=NodeKind.LIST_ITEM,
            text=text,
        )
    return DocNode(
        id=make_node_id(NodeKind.PARAGRAPH, text, ordinal),
        kind=NodeKind.PARAGRAPH,
        text=text,
    )


def _table_node(item, ordinal: int) -> DocNode | None:
    data = getattr(item, "data", None)
    grid = getattr(data, "grid", None) if data else None
    if not grid:
        return None
    rows: list[list[TableCell]] = []
    for row in grid:
        cells = []
        for cell in row:
            text = cleanup_inline(getattr(cell, "text", "") or "")
            is_header = bool(
                getattr(cell, "column_header", False) or getattr(cell, "row_header", False)
            )
            cells.append(TableCell(text=text, is_header=is_header))
        rows.append(cells)
    table = TableData(rows=rows)
    return DocNode(
        id=make_node_id(NodeKind.TABLE, str(rows), ordinal),
        kind=NodeKind.TABLE,
        table=table,
    )


def _picture_node(item, stage, ordinal: int) -> DocNode | None:
    image = getattr(item, "image", None)
    pil_image = getattr(image, "pil_image", None) if image else None
    if pil_image is None:
        return None
    import io

    buf = io.BytesIO()
    pil_image.save(buf, format="PNG")
    caption = None
    get_caption = getattr(item, "caption_text", None)
    if callable(get_caption):
        try:
            caption = get_caption(doc=None) or None
        except Exception:
            caption = None
    aid = stage(buf.getvalue(), "image/png")
    return DocNode(
        id=make_node_id(NodeKind.FIGURE, caption or f"pic-{ordinal}", ordinal),
        kind=NodeKind.FIGURE,
        text=caption,
        assets=[AssetRef(asset_id=aid, caption=caption)],
    )


__all__ = ["ingest_docling"]
