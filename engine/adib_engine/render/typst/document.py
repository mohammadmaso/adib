"""DocTree -> Typst document body.

Each node kind maps to one Typst construct. Tables, figures, and footnotes are
Typst's own `#table`/`#figure`/`#footnote` so numbering, captions, and cross-
references come from Typst's layout engine rather than being hand-rolled.

The compiled `.typ` file always lives directly inside the project's assets
directory (see `render/typst/compile.py`), so asset references here are bare
relative filenames — no `--root` juggling needed.
"""

from __future__ import annotations

from pathlib import Path

from adib_engine.models.document import AssetRef, DocNode, DocTree, NodeKind, TableData
from adib_engine.render.typst.markup import escape_typst, inline_to_typst


def _asset_path(tree: DocTree, ref: AssetRef, assets_dir: Path | None) -> str | None:
    """The asset's filename, or None if it can't actually be embedded.

    Checked against disk, not just the tree's registry: one image that failed
    to extract during ingest must not take the whole book's render down with it.
    """
    asset = tree.assets.get(ref.asset_id)
    if asset is None:
        return None
    if assets_dir is not None and not (assets_dir / asset.path).exists():
        return None
    return asset.path


def _table_block(table: TableData) -> str:
    if not table.rows:
        return ""
    n_cols = table.n_cols or 1
    cells: list[str] = []
    for row in table.rows:
        for cell in row:
            content = inline_to_typst(cell.text)
            wrapped = f"*{content}*" if cell.is_header else content
            if cell.colspan > 1:
                cells.append(f"table.cell(colspan: {cell.colspan})[{wrapped}]")
            else:
                cells.append(f"[{wrapped}]")
    body = ",\n  ".join(cells)
    tbl = f"table(\n  columns: {n_cols},\n  {body},\n)"
    if table.caption:
        caption = escape_typst(table.caption)
        return f"#figure(\n  {tbl},\n  caption: [{caption}],\n)"
    return f"#{tbl}"


def _figure_block(node: DocNode, tree: DocTree, assets_dir: Path | None) -> str:
    images = [
        f'image("{path}")'
        for ref in node.assets
        if (path := _asset_path(tree, ref, assets_dir))
    ]
    caption = next((ref.caption for ref in node.assets if ref.caption), None) or node.text
    if not images:
        return ""
    if len(images) == 1:
        body = images[0]
    else:
        body = f"grid(columns: {len(images)}, {', '.join(images)})"
    if caption:
        return f"#figure(\n  {body},\n  caption: [{inline_to_typst(caption)}],\n)"
    return f"#figure(\n  {body},\n)"


def _list_block(node: DocNode) -> str:
    marker = "+" if node.attrs.get("ordered") else "-"
    items = [f"{marker} {inline_to_typst(child.text or '')}" for child in node.children]
    return "\n".join(items)


def _code_block(node: DocNode) -> str:
    lang = node.attrs.get("language") or ""
    body = (node.text or "").replace("`", "\\`")
    return f"```{lang}\n{body}\n```"


def node_to_typst(node: DocNode, tree: DocTree, assets_dir: Path | None = None) -> str:
    """Render one node (not its children — callers walk the tree themselves)."""
    if node.kind is NodeKind.HEADING:
        level = node.level or 1
        return f"{'=' * level} {inline_to_typst(node.text or '')}"
    if node.kind is NodeKind.PARAGRAPH:
        return inline_to_typst(node.text or "")
    if node.kind is NodeKind.QUOTE:
        return f"#quote(block: true)[{inline_to_typst(node.text or '')}]"
    if node.kind is NodeKind.CODE:
        return _code_block(node)
    if node.kind is NodeKind.TABLE:
        return _table_block(node.table) if node.table else ""
    if node.kind is NodeKind.FIGURE:
        return _figure_block(node, tree, assets_dir)
    if node.kind is NodeKind.LIST:
        return _list_block(node)
    if node.kind is NodeKind.FOOTNOTE:
        return f"#footnote[{inline_to_typst(node.text or '')}]"
    if node.kind is NodeKind.PAGE_BREAK:
        return "#pagebreak()"
    if node.kind is NodeKind.EQUATION:
        return f"$ {node.text or ''} $"
    # front_matter/back_matter/toc are containers; their children are walked
    # separately and this node itself contributes no markup of its own.
    return ""


def tree_to_typst_body(tree: DocTree, assets_dir: Path | None = None) -> str:
    """Walk the whole tree (skipping list/container internals) into one .typ body.

    `assets_dir` lets figure references be checked against disk so one image
    that failed to extract during ingest degrades to a dropped figure rather
    than an unrenderable book; omit it to skip that check (e.g. pure markup
    unit tests with no real files on disk).
    """
    blocks: list[str] = []
    for node in tree.nodes:
        blocks.extend(_walk_top_level(node, tree, assets_dir))
    return "\n\n".join(b for b in blocks if b)


def _walk_top_level(node: DocNode, tree: DocTree, assets_dir: Path | None) -> list[str]:
    """Emit this node's markup, recursing into children only for containers.

    LIST is rendered whole by `_list_block` (it owns its children directly);
    every other container kind (front/back matter, TOC) has no markup of its
    own and just walks through to its children.
    """
    if node.kind is NodeKind.LIST:
        return [node_to_typst(node, tree, assets_dir)]

    blocks = [node_to_typst(node, tree, assets_dir)]
    for child in node.children:
        blocks.extend(_walk_top_level(child, tree, assets_dir))
    return blocks


__all__ = ["node_to_typst", "tree_to_typst_body"]
