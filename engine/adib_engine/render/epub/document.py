"""DocTree -> XHTML chapter bodies.

Splits the tree into chapters at each top-level H1 (the common case for a
book with real part/chapter structure); a tree with no H1 at all becomes one
chapter. Each chapter is handed to ebooklib as one `EpubHtml` spine item, which
is what gives readers a per-chapter table of contents and lets EPUB readers
paginate sensibly instead of loading the whole book as one enormous document.
"""

from __future__ import annotations

from adib_engine.models.document import AssetRef, DocNode, DocTree, NodeKind, TableData
from adib_engine.render.epub.markup import inline_to_xhtml


class Chapter:
    """One spine item: a title (for the nav) and its rendered XHTML body."""

    def __init__(self, title: str, nodes: list[DocNode]):
        self.title = title
        self.nodes = nodes


def split_into_chapters(tree: DocTree) -> list[Chapter]:
    """Break top-level nodes into chapters at each H1."""
    chapters: list[Chapter] = []
    current: list[DocNode] | None = None
    current_title = tree.meta.title or "Untitled"

    for node in tree.nodes:
        if node.kind is NodeKind.HEADING and (node.level or 1) == 1:
            if current is not None:
                chapters.append(Chapter(current_title, current))
            current_title = node.text or "Untitled"
            current = [node]
        else:
            if current is None:
                current = []
            current.append(node)

    if current:
        chapters.append(Chapter(current_title, current))
    if not chapters:
        chapters.append(Chapter(current_title, []))
    return chapters


def _asset_path(tree: DocTree, ref: AssetRef) -> str | None:
    asset = tree.assets.get(ref.asset_id)
    return f"assets/{asset.path}" if asset else None


def _table_html(table: TableData) -> str:
    if not table.rows:
        return ""
    rows_html = []
    for row in table.rows:
        cells = "".join(
            f"<{'th' if c.is_header else 'td'}"
            + (f' colspan="{c.colspan}"' if c.colspan > 1 else "")
            + (f' rowspan="{c.rowspan}"' if c.rowspan > 1 else "")
            + f">{inline_to_xhtml(c.text)}</{'th' if c.is_header else 'td'}>"
            for c in row
        )
        rows_html.append(f"<tr>{cells}</tr>")
    caption = f"<caption>{inline_to_xhtml(table.caption)}</caption>" if table.caption else ""
    return f"<table>{caption}{''.join(rows_html)}</table>"


def _image_style(node: DocNode) -> str:
    """Keep the picture's original share of the page width, when ingest measured
    one (PDF sources). Other sources get the reader's default full width."""
    ratio = node.attrs.get("width_ratio")
    if not isinstance(ratio, int | float) or not 0 < ratio <= 1:
        return ""
    return f' style="width:{round(ratio * 100, 1)}%"'


def _figure_html(node: DocNode, tree: DocTree) -> str:
    style = _image_style(node)
    images = [
        f'<img src="{path}" alt="{ref.alt or ""}"{style}/>'
        for ref in node.assets
        if (path := _asset_path(tree, ref))
    ]
    caption = next((ref.caption for ref in node.assets if ref.caption), None) or node.text
    if not images:
        return ""
    body = "".join(images)
    if caption:
        return f"<figure>{body}<figcaption>{inline_to_xhtml(caption)}</figcaption></figure>"
    return f"<figure>{body}</figure>"


def _list_html(node: DocNode) -> str:
    tag = "ol" if node.attrs.get("ordered") else "ul"
    items = "".join(f"<li>{inline_to_xhtml(c.text or '')}</li>" for c in node.children)
    return f"<{tag}>{items}</{tag}>"


def _code_html(node: DocNode) -> str:
    import html as htmllib

    lang = node.attrs.get("language") or ""
    cls = f' class="language-{lang}"' if lang else ""
    return f"<pre><code{cls}>{htmllib.escape(node.text or '')}</code></pre>"


def node_to_xhtml(node: DocNode, tree: DocTree) -> str:
    """Render one node (not its children — callers walk the tree themselves)."""
    if node.kind is NodeKind.HEADING:
        level = min(max(node.level or 1, 1), 6)
        return f"<h{level}>{inline_to_xhtml(node.text or '')}</h{level}>"
    if node.kind is NodeKind.PARAGRAPH:
        text = inline_to_xhtml(node.text or "")
        return f"<p>{text}</p>" if text else ""
    if node.kind is NodeKind.QUOTE:
        return f"<blockquote><p>{inline_to_xhtml(node.text or '')}</p></blockquote>"
    if node.kind is NodeKind.CODE:
        return _code_html(node)
    if node.kind is NodeKind.TABLE:
        return _table_html(node.table) if node.table else ""
    if node.kind is NodeKind.FIGURE:
        return _figure_html(node, tree)
    if node.kind is NodeKind.LIST:
        return _list_html(node)
    if node.kind is NodeKind.FOOTNOTE:
        return f'<aside epub:type="footnote">{inline_to_xhtml(node.text or "")}</aside>'
    if node.kind is NodeKind.PAGE_BREAK:
        return '<div style="page-break-after: always;"></div>'
    if node.kind is NodeKind.EQUATION:
        return f"<p>{node.text or ''}</p>"
    return ""


def chapter_to_xhtml_body(chapter: Chapter, tree: DocTree) -> str:
    """Render one chapter's nodes (skipping the H1 the spine title already carries)."""
    blocks: list[str] = []
    for node in chapter.nodes:
        blocks.extend(_walk_top_level(node, tree))
    return "\n".join(b for b in blocks if b)


def _walk_top_level(node: DocNode, tree: DocTree) -> list[str]:
    if node.kind is NodeKind.LIST:
        return [node_to_xhtml(node, tree)]
    blocks = [node_to_xhtml(node, tree)]
    for child in node.children:
        blocks.extend(_walk_top_level(child, tree))
    return blocks


__all__ = ["Chapter", "chapter_to_xhtml_body", "node_to_xhtml", "split_into_chapters"]
