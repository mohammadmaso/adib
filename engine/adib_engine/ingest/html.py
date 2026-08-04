"""Standalone HTML importer: a .html file -> DocTree.

Structurally the same problem as one EPUB spine item, so this reuses the same
block-tag mapping. The difference is asset resolution: images come from the
local filesystem next to the HTML file (or are skipped if remote), not from a
zip archive.
"""

from __future__ import annotations

from pathlib import Path

from bs4 import BeautifulSoup
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

_IMG_MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".svg": "image/svg+xml",
}


def _inline_text(el) -> str:
    parts = [t.strip() for t in el.itertext() if t and t.strip()]
    return cleanup_inline(" ".join(parts))


def _resolve_image(html_path: Path, src: str) -> tuple[bytes, str] | None:
    if not src or src.startswith(("http://", "https://", "data:")):
        return None
    target = (html_path.parent / src).resolve()
    if not target.exists():
        return None
    return target.read_bytes(), _IMG_MIME.get(target.suffix.lower(), "application/octet-stream")


def _collect(el, *, html_path: Path, stage, nodes: list[DocNode]) -> None:
    from lxml.etree import _Element

    for child in el:
        if not isinstance(child, _Element):
            continue
        tag = child.tag
        if not isinstance(tag, str):
            continue

        if tag == "img":
            data = _resolve_image(html_path, child.get("src") or "")
            if data:
                content, mime = data
                nodes.append(
                    DocNode(
                        id=make_node_id(NodeKind.FIGURE, child.get("alt") or "", len(nodes)),
                        kind=NodeKind.FIGURE,
                        assets=[
                            AssetRef(
                                asset_id=stage(content, mime),
                                alt=child.get("alt"),
                            )
                        ],
                    )
                )
            continue
        if tag in ("script", "style", "head", "meta", "link", "br", "hr"):
            continue
        if tag == "figure":
            caption = None
            figcap = child.find(".//figcaption")
            if figcap is not None:
                caption = _inline_text(figcap)
            refs = []
            for img in child.iter("img"):
                data = _resolve_image(html_path, img.get("src") or "")
                if data:
                    content, mime = data
                    refs.append(
                        AssetRef(asset_id=stage(content, mime), caption=caption, alt=img.get("alt"))
                    )
            if refs or caption:
                nodes.append(
                    DocNode(
                        id=make_node_id(NodeKind.FIGURE, caption or "figure", len(nodes)),
                        kind=NodeKind.FIGURE,
                        text=caption,
                        assets=refs,
                    )
                )
            continue
        if tag in ("ul", "ol"):
            lst = DocNode(
                id=make_node_id(NodeKind.LIST, "", len(nodes)),
                kind=NodeKind.LIST,
                attrs={"ordered": tag == "ol"},
            )
            for li in child.iter("li"):
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
            continue
        if tag == "table":
            caption = None
            cap = child.find(".//caption")
            if cap is not None:
                caption = _inline_text(cap)
            rows: list[list[tuple[bool, str]]] = []
            for tr in child.iter("tr"):
                cells = [
                    (c.tag == "th", _inline_text(c)) for c in list(tr) if c.tag in ("th", "td")
                ]
                if cells:
                    rows.append(cells)
            if rows:
                width = max(len(r) for r in rows)
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
            continue
        if tag in ("div", "section", "article", "main", "aside", "body", "html"):
            _collect(child, html_path=html_path, stage=stage, nodes=nodes)
            continue

        kind, level = _BLOCK_TAGS.get(tag, (None, None))
        if kind is None:
            _collect(child, html_path=html_path, stage=stage, nodes=nodes)
            continue
        if kind is NodeKind.CODE:
            text = (child.text or "").strip()
            if text:
                nodes.append(
                    DocNode(
                        id=make_node_id(NodeKind.CODE, text, len(nodes)),
                        kind=NodeKind.CODE,
                        text=text,
                    )
                )
            continue
        text = _inline_text(child)
        if not text:
            continue
        nodes.append(
            DocNode(id=make_node_id(kind, text, len(nodes)), kind=kind, text=text, level=level)
        )


def ingest_html(path: Path, *, assets_dir: Path | None = None) -> DocTree:
    raw = path.read_bytes()
    doc = DocTree(parser="html")
    stage = make_asset_stager(doc, assets_dir or path.parent / f"{path.stem}.assets")

    # BeautifulSoup handles real-world malformed HTML more forgivingly than lxml
    # alone; re-serialize the cleaned tree for lxml's faster element walk.
    soup = BeautifulSoup(raw, "html.parser")
    title_tag = soup.find("title")
    if title_tag and title_tag.text.strip():
        doc.meta.title = title_tag.text.strip()

    root = lxml_html.fromstring(str(soup))
    body = root.find(".//body")
    nodes: list[DocNode] = []
    _collect(body if body is not None else root, html_path=path, stage=stage, nodes=nodes)

    doc.nodes = nodes
    if not doc.meta.title:
        for n in nodes:
            if n.kind is NodeKind.HEADING and n.level == 1:
                doc.meta.title = n.text
                break
    return doc


__all__ = ["ingest_html"]
