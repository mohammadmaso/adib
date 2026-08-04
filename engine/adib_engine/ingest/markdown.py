"""Markdown importer: a .md file -> DocTree.

Markdown already carries its structure in the syntax (ATX headings, fenced code,
pipe tables), so this is a straightforward line-oriented parse rather than
layout inference. Images referenced by relative path are staged as assets.
"""

from __future__ import annotations

import re
from pathlib import Path

from adib_engine.ingest.kit import cleanup_inline, make_asset_stager, table_from_pipe
from adib_engine.models.document import AssetRef, DocNode, DocTree, NodeKind, make_node_id

_ATX = re.compile(r"^(#{1,6})\s+(.*)$")
_FENCE = re.compile(r"^(```|~~~)(\w*)\s*$")
_IMG = re.compile(r"!\[(?P<alt>[^\]]*)\]\((?P<src>[^)\s]+)(?:\s+\"(?P<title>[^\"]*)\")?\)")
_LIST_ITEM = re.compile(r"^(\s*)([-*+]|\d+[.)])\s+(.*)$")
_QUOTE = re.compile(r"^>\s?(.*)$")


def ingest_markdown(path: Path, *, assets_dir: Path | None = None) -> DocTree:
    text = path.read_text(encoding="utf-8", errors="replace")
    doc = DocTree(parser="markdown")
    stage = make_asset_stager(doc, assets_dir or path.parent / f"{path.stem}.assets")

    nodes: list[DocNode] = []
    lines = text.split("\n")
    i = 0
    para_buf: list[str] = []
    list_buf: list[tuple[int, str]] = []

    def flush_para() -> None:
        if not para_buf:
            return
        joined = cleanup_inline(" ".join(para_buf))
        para_buf.clear()
        if not joined:
            return
        img = _IMG.match(joined)
        if img:
            data = _load_local_image(path, img.group("src"))
            if data:
                content, mime = data
                aid = stage(content, mime)
                nodes.append(
                    DocNode(
                        id=make_node_id(NodeKind.FIGURE, img.group("alt"), len(nodes)),
                        kind=NodeKind.FIGURE,
                        text=img.group("title"),
                        assets=[
                            AssetRef(asset_id=aid, caption=img.group("title"), alt=img.group("alt"))
                        ],
                    )
                )
                return
        nodes.append(
            DocNode(
                id=make_node_id(NodeKind.PARAGRAPH, joined, len(nodes)),
                kind=NodeKind.PARAGRAPH,
                text=joined,
            )
        )

    def flush_list() -> None:
        if not list_buf:
            return
        lst = DocNode(
            id=make_node_id(NodeKind.LIST, "", len(nodes)),
            kind=NodeKind.LIST,
            attrs={"ordered": list_buf[0][0] == 1},
        )
        for _indent, text in list_buf:
            lst.children.append(
                DocNode(
                    id=make_node_id(NodeKind.LIST_ITEM, text, len(nodes)),
                    kind=NodeKind.LIST_ITEM,
                    text=cleanup_inline(text),
                )
            )
        list_buf.clear()
        if lst.children:
            nodes.append(lst)

    while i < len(lines):
        line = lines[i]

        m = _FENCE.match(line)
        if m:
            flush_para()
            flush_list()
            lang = m.group(2)
            body_lines: list[str] = []
            i += 1
            while i < len(lines) and not _FENCE.match(lines[i]):
                body_lines.append(lines[i])
                i += 1
            i += 1  # skip closing fence
            code_text = "\n".join(body_lines)
            nodes.append(
                DocNode(
                    id=make_node_id(NodeKind.CODE, code_text, len(nodes)),
                    kind=NodeKind.CODE,
                    text=code_text,
                    attrs={"language": lang} if lang else {},
                )
            )
            continue

        heading = _ATX.match(line)
        if heading:
            flush_para()
            flush_list()
            level = len(heading.group(1))
            text = cleanup_inline(heading.group(2))
            if text:
                nodes.append(
                    DocNode(
                        id=make_node_id(NodeKind.HEADING, text, len(nodes)),
                        kind=NodeKind.HEADING,
                        text=text,
                        level=level,
                    )
                )
            i += 1
            continue

        if line.strip().startswith("|") and i + 1 < len(lines) and re.match(
            r"^\|[\s:|-]+\|$", lines[i + 1].strip()
        ):
            flush_para()
            flush_list()
            table_lines = [line]
            i += 1
            while i < len(lines) and lines[i].strip().startswith("|"):
                table_lines.append(lines[i])
                i += 1
            table = table_from_pipe(table_lines)
            if table:
                nodes.append(
                    DocNode(
                        id=make_node_id(NodeKind.TABLE, table_lines[0], len(nodes)),
                        kind=NodeKind.TABLE,
                        table=table,
                    )
                )
            continue

        quote = _QUOTE.match(line)
        if quote:
            flush_para()
            flush_list()
            quote_lines = [quote.group(1)]
            i += 1
            while i < len(lines) and _QUOTE.match(lines[i]):
                quote_lines.append(_QUOTE.match(lines[i]).group(1))
                i += 1
            text = cleanup_inline(" ".join(quote_lines))
            if text:
                nodes.append(
                    DocNode(
                        id=make_node_id(NodeKind.QUOTE, text, len(nodes)),
                        kind=NodeKind.QUOTE,
                        text=text,
                    )
                )
            continue

        li = _LIST_ITEM.match(line)
        if li:
            flush_para()
            indent = len(li.group(1))
            list_buf.append((indent, li.group(3)))
            i += 1
            continue

        if not line.strip():
            flush_para()
            flush_list()
            i += 1
            continue

        flush_list()
        para_buf.append(line)
        i += 1

    flush_para()
    flush_list()

    doc.nodes = nodes
    doc.meta.title = _guess_title(nodes)
    return doc


def _guess_title(nodes: list[DocNode]) -> str | None:
    for n in nodes:
        if n.kind is NodeKind.HEADING and n.level == 1:
            return n.text
    return None


def _load_local_image(md_path: Path, src: str) -> tuple[bytes, str] | None:
    if src.startswith(("http://", "https://", "data:")):
        return None
    target = (md_path.parent / src).resolve()
    if not target.exists():
        return None
    mime = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".webp": "image/webp",
        ".svg": "image/svg+xml",
    }.get(target.suffix.lower(), "application/octet-stream")
    return target.read_bytes(), mime


__all__ = ["ingest_markdown"]
