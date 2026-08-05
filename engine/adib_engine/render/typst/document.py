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


def _image_width(node: DocNode) -> str:
    """Typst `width:` for this figure's images.

    Ingest records `width_ratio` — how much of the source page's width the
    picture occupied — so a half-page diagram stays half-page here instead of
    being stretched to the text column. Sources that carry no such measurement
    (EPUB, HTML, markdown) fall back to the full column, which is what a web
    layout gives them anyway.
    """
    ratio = node.attrs.get("width_ratio")
    if not isinstance(ratio, int | float) or not 0 < ratio <= 1:
        return "100%"
    return f"{round(ratio * 100, 1)}%"


def _figure_block(node: DocNode, tree: DocTree, assets_dir: Path | None) -> str:
    paths = [
        path for ref in node.assets if (path := _asset_path(tree, ref, assets_dir))
    ]
    caption = next((ref.caption for ref in node.assets if ref.caption), None) or node.text
    if not paths:
        return ""
    if len(paths) == 1:
        body = f'image("{paths[0]}", width: {_image_width(node)})'
    else:
        # Each image fills its own equal-width column, so a row of pictures
        # keeps the side-by-side arrangement the source had.
        cells = ", ".join(f'image("{p}", width: 100%)' for p in paths)
        body = f"grid(columns: (1fr,) * {len(paths)}, column-gutter: 6pt, {cells})"
    if caption:
        # `placement: none` keeps the figure between the same two paragraphs it
        # sat between in the source book, rather than floating to a page top.
        return (
            f"#figure(\n  {body},\n  caption: [{inline_to_typst(caption)}],"
            "\n  placement: none,\n)"
        )
    # No caption in the source means no caption — and no "Figure 1:" number
    # the original book never had. Just the picture, in place, centred.
    return f"#align(center, block(breakable: false, {body}))"


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


def cover_page(tree: DocTree, assets_dir: Path | None = None) -> str:
    """A full-bleed page for the book's cover, or "" if it has none.

    Placed ahead of the rest of the body by `render_typst_source`, with its own
    `#pagebreak()` so the first content page still starts clean.
    """
    cover_id = tree.meta.cover_asset_id
    if not cover_id:
        return ""
    asset = tree.assets.get(cover_id)
    if asset is None:
        return ""
    if assets_dir is not None and not (assets_dir / asset.path).exists():
        return ""
    return (
        f'#page(margin: 0pt)[#image("{asset.path}", width: 100%, height: 100%, fit: "cover")]'
        "\n#pagebreak()"
    )


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


__all__ = ["cover_page", "node_to_typst", "tree_to_typst_body"]
