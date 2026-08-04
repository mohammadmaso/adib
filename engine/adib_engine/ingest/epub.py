"""EPUB importer: an .epub -> DocTree.

EPUB is the highest-fidelity input we accept: the document is already segmented
into spine items with real `<h1..h6>`/`<p>`/`<figure>`/`<table>` semantics, so
heading levels come from structure directly instead of font-size inference. We
walk the OPF spine in order and collect translated captions/alt as prose.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from ebooklib import ITEM_DOCUMENT, ITEM_IMAGE, epub
from lxml import html as lxml_html

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

#: (kind, level). `ul`/`ol`/`table`/`figure` are dispatched separately above
#: this lookup, since they need their own recursive collectors.
_BLOCK_TAGS = {
    "h1": (NodeKind.HEADING, 1),
    "h2": (NodeKind.HEADING, 2),
    "h3": (NodeKind.HEADING, 3),
    "h4": (NodeKind.HEADING, 4),
    "h5": (NodeKind.HEADING, 5),
    "h6": (NodeKind.HEADING, 6),
    "p": (NodeKind.PARAGRAPH, None),
    "pre": (NodeKind.CODE, None),
    "blockquote": (NodeKind.QUOTE, None),
}

def _inline_text(el) -> str:
    """Extract readable inline text from an element, normalizing whitespace."""
    parts = [t.strip() for t in el.itertext() if t and t.strip()]
    return cleanup_inline(" ".join(parts))


def _img_src_to_bytes(book: epub.EpubBook, item, src: str) -> tuple[bytes | None, str | None]:
    """Resolve an <img src> (zip path) against the book, returning (data, mime)."""
    import posixpath

    if not src or src.lower().startswith(("http:", "https:", "data:")):
        return None, None
    base = posixpath.dirname(item.get_name())
    resolved = posixpath.normpath(posixpath.join(base, src))
    for it in book.get_items_of_type(ITEM_IMAGE):
        name = it.get_name().replace("\\", "/")
        if name == resolved or name.endswith("/" + posixpath.basename(src)):
            return it.get_content(), it.media_type
    return None, None


def _collect_block(
    el,
    *,
    book: epub.EpubBook,
    item,
    stage: Callable[..., str],
    nodes: list[DocNode],
) -> None:
    """Append block nodes for `el` to `nodes`."""
    from lxml.etree import _Element

    for child in el:
        if not isinstance(child, _Element):
            continue
        tag = child.tag
        if not isinstance(tag, str) or tag in (
            "script",
            "style",
            "head",
            "meta",
            "link",
            "br",
            "hr",
            "img",
        ):
            if tag == "img":
                data, mime = _img_src_to_bytes(book, item, child.get("src") or "")
                if data:
                    alt = child.get("alt")
                    _append_asset_node(nodes, stage, data, mime or "image/png", alt)
            continue

        if tag == "figure":
            _collect_figure(child, book=book, item=item, stage=stage, nodes=nodes)
            continue
        if tag in ("ul", "ol"):
            _collect_list(child, ordered=(tag == "ol"), nodes=nodes)
            continue
        if tag == "table":
            _collect_table(child, nodes=nodes)
            continue
        if tag in ("div", "section", "article", "main", "aside"):
            # container: recurse, but only if it has no own text
            _collect_block(child, book=book, item=item, stage=stage, nodes=nodes)
            continue

        kind, level = _BLOCK_TAGS.get(tag, (None, None))
        if kind is None:
            # unknown tag: recurse into children
            _collect_block(child, book=book, item=item, stage=stage, nodes=nodes)
            continue

        if kind is NodeKind.CODE:
            # preserve inner whitespace for code; markdown-flattening would lose indentation.
            text = (child.text or "").strip()
            if not text:
                continue
            nodes.append(
                DocNode(
                    id=make_node_id(NodeKind.CODE, text, len(nodes)),
                    kind=NodeKind.CODE,
                    text=text,
                    attrs={"language": child.get("class") or ""},
                )
            )
            continue

        text = _inline_text(child)
        if not text:
            continue
        if kind is NodeKind.HEADING:
            nodes.append(
                DocNode(
                    id=make_node_id(NodeKind.HEADING, text, len(nodes)),
                    kind=NodeKind.HEADING,
                    text=text,
                    level=level,
                )
            )
            continue
        if kind is NodeKind.QUOTE:
            nodes.append(
                DocNode(
                    id=make_node_id(NodeKind.QUOTE, text, len(nodes)),
                    kind=NodeKind.QUOTE,
                    text=text,
                )
            )
            continue
        # p, pre, blockquote
        nodes.append(
            DocNode(
                id=make_node_id(kind, text, len(nodes)),
                kind=kind,
                text=text,
            )
        )


def _collect_figure(
    el,
    *,
    book: epub.EpubBook,
    item,
    stage: Callable[..., str],
    nodes: list[DocNode],
) -> None:
    """A <figure> becomes a FIGURE node holding its images plus a caption."""
    caption = None
    figcap = el.find(".//figcaption")
    if figcap is not None:
        caption = _inline_text(figcap)
    refs = _collect_images(el, book=book, item=item, stage=stage)
    if not refs and caption is None:
        return
    fig = DocNode(
        id=make_node_id(NodeKind.FIGURE, caption or "figure", len(nodes)),
        kind=NodeKind.FIGURE,
        text=caption,
        assets=refs,
    )
    # Attach the caption as translatable prose on the node.
    nodes.append(fig)


def _collect_images(el, *, book, item, stage: Callable[..., str]) -> list[AssetRef]:
    refs: list[AssetRef] = []
    for img in el.iter("img"):
        data, mime = _img_src_to_bytes(book, item, img.get("src") or "")
        if not data:
            continue
        refs.append(
            AssetRef(
                asset_id=stage(data, mime or "image/png"),
                caption=None,
                alt=img.get("alt"),
            )
        )
    return refs


def _append_asset_node(
    nodes: list[DocNode], stage: Callable[..., str], data: bytes, mime: str, alt: str | None
) -> None:
    aid = stage(data, mime)
    nodes.append(
        DocNode(
            id=make_node_id(NodeKind.FIGURE, alt or "", len(nodes)),
            kind=NodeKind.FIGURE,
            text=None,
            assets=[AssetRef(asset_id=aid, caption=None, alt=alt)],
        )
    )


def _collect_list(el, *, ordered: bool, nodes: list[DocNode]) -> None:
    lst = DocNode(
        id=make_node_id(NodeKind.LIST, "", len(nodes)),
        kind=NodeKind.LIST,
        text=None,
        attrs={"ordered": ordered},
    )
    for li in el.iter("li"):
        t = _inline_text(li)
        if t:
            lst.children.append(
                DocNode(
                    id=make_node_id(NodeKind.LIST_ITEM, t, len(nodes)),
                    kind=NodeKind.LIST_ITEM,
                    text=t,
                )
            )
    if lst.children:
        nodes.append(lst)


def _collect_table(el, *, nodes: list[DocNode]) -> None:
    caption = None
    cap = el.find(".//caption")
    if cap is not None:
        caption = _inline_text(cap)

    rows: list[list[tuple[bool, str]]] = []
    for tr in el.iter("tr"):
        cells = [(c.tag == "th", _inline_text(c)) for c in list(tr) if c.tag in ("th", "td")]
        if cells:
            rows.append(cells)
    if not rows:
        return

    width = max(len(row) for row in rows)
    table = TableData(caption=caption)
    for row in rows:
        cells = [TableCell(text=text, is_header=is_th) for is_th, text in row]
        cells += [TableCell(text="") for _ in range(width - len(cells))]
        table.rows.append(cells)

    nodes.append(
        DocNode(
            id=make_node_id(NodeKind.TABLE, caption or "table", len(nodes)),
            kind=NodeKind.TABLE,
            text=caption,
            table=table,
        )
    )


def ingest_epub(
    path: Path,
    *,
    assets_dir: Path | None = None,
) -> DocTree:
    """Parse an EPUB file into a DocTree.

    The `assets_dir` (default `<stem>.assets` beside the epub) receives extracted
    images; the tree references them by id.
    """
    book = epub.read_epub(str(path))
    doc = DocTree(parser="epub")
    assets_dir = assets_dir or path.parent / f"{path.stem}.assets"
    stage = make_asset_stager(doc, assets_dir)

    title = book.get_metadata("DC", "title")
    if title:
        doc.meta.title = (title[0][0] if isinstance(title[0], (tuple, list)) else str(title[0]))
    creators = book.get_metadata("DC", "creator")
    if creators:
        doc.meta.authors = [
            c[0] if isinstance(c, (tuple, list)) else str(c) for c in creators
        ]
    lang = book.get_metadata("DC", "language")
    if lang:
        doc.meta.source_lang = (lang[0][0] if isinstance(lang[0], (tuple, list)) else str(lang[0]))
    # cover
    for it in book.get_items_of_type(ITEM_IMAGE):
        name = it.get_name().lower()
        if "cover" in name and it.get_content() and len(it.get_content()) > 512:
            doc.meta.cover_asset_id = stage(it.get_content(), it.media_type)
            break

    # EpubNav is the machine-generated table of contents, not book content: it
    # duplicates every heading title as a link and must not become prose.
    items_by_id = {
        it.get_id(): it
        for it in book.get_items_of_type(ITEM_DOCUMENT)
        if not isinstance(it, epub.EpubNav)
    }
    nodes: list[DocNode] = []

    # The spine is the true reading order: (idref, linear) pairs after read_epub.
    for idref, _linear in book.spine:
        item = items_by_id.get(idref)
        if item is None:
            continue
        body = item.get_content()
        if not body:
            continue
        try:
            root = lxml_html.fromstring(body)
        except Exception:
            continue
        body_el = root.find(".//body")
        _collect_block(
            body_el if body_el is not None else root,
            book=book,
            item=item,
            stage=stage,
            nodes=nodes,
        )

    doc.nodes = nodes
    return doc


__all__ = ["ingest_epub"]
