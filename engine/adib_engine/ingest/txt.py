"""Plain text importer: a .txt file -> DocTree.

No structural markup exists, so every block type is inferred from line shape:
blank-line-delimited paragraphs, short standalone all-caps/title-case lines as
headings, runs of 4-space/tab-indented lines as code, "> "-prefixed lines as
quotes, and bullet/numbered runs as lists. Every heuristic here is
deliberately conservative — a block is only pulled out of the paragraph flow
when its shape is unambiguous (e.g. a single indented line stays prose; two
or more in a row, standing alone between blank lines, become code). Missing a
block just means it becomes a paragraph, which is always safe.
"""

from __future__ import annotations

import re
from pathlib import Path

from adib_engine.ingest.kit import cleanup_inline
from adib_engine.models.document import DocNode, DocTree, NodeKind, make_node_id

_MAX_HEADING_CHARS = 80
_INDENT_CODE = re.compile(r"^(?: {4,}|\t)")
_LIST_ITEM = re.compile(r"^\s*([-*•]|\d+[.)])\s+(.*)$")
_QUOTE = re.compile(r"^>\s?(.*)$")


def _looks_like_heading(line: str, *, blank_before: bool, blank_after: bool) -> bool:
    if not (blank_before and blank_after):
        return False
    stripped = line.strip()
    if not stripped or len(stripped) > _MAX_HEADING_CHARS:
        return False
    if stripped.endswith((".", ",", ";")):
        return False
    return stripped.isupper() or stripped.istitle()


def _scan_code_block(lines: list[str], i: int) -> tuple[list[str], int] | None:
    """A run of >=2 consecutive indented lines, standing alone before a blank
    line (or EOF). A lone indented line is too common in ordinary prose
    (poetry, block quotes) to trust by itself.
    """
    if not _INDENT_CODE.match(lines[i]):
        return None
    j = i
    run: list[str] = []
    while j < len(lines) and lines[j].strip() and _INDENT_CODE.match(lines[j]):
        run.append(lines[j])
        j += 1
    blank_after = j >= len(lines) or not lines[j].strip()
    if len(run) >= 2 and blank_after:
        return run, j
    return None


def _scan_quote(lines: list[str], i: int) -> tuple[list[str], int] | None:
    if not _QUOTE.match(lines[i]):
        return None
    j = i
    run: list[str] = []
    while j < len(lines) and _QUOTE.match(lines[j]):
        run.append(_QUOTE.match(lines[j]).group(1))
        j += 1
    return run, j


def _scan_list(lines: list[str], i: int) -> tuple[list[str], int] | None:
    """A run of >=2 consecutive bullet/numbered lines. A single line starting
    with "-" is too likely to be a dash in prose to trust alone.
    """
    if not _LIST_ITEM.match(lines[i]):
        return None
    j = i
    items: list[str] = []
    while j < len(lines):
        m = _LIST_ITEM.match(lines[j])
        if not m:
            break
        items.append(m.group(2))
        j += 1
    if len(items) >= 2:
        return items, j
    return None


def ingest_txt(path: Path) -> DocTree:
    text = path.read_text(encoding="utf-8", errors="replace")
    raw_lines = text.split("\n")
    n = len(raw_lines)

    doc = DocTree(parser="txt")
    nodes: list[DocNode] = []
    para_buf: list[str] = []

    def flush_para() -> None:
        if not para_buf:
            return
        joined = cleanup_inline(" ".join(para_buf))
        para_buf.clear()
        if joined:
            nodes.append(
                DocNode(
                    id=make_node_id(NodeKind.PARAGRAPH, joined, len(nodes)),
                    kind=NodeKind.PARAGRAPH,
                    text=joined,
                )
            )

    def append_list(items: list[str]) -> None:
        lst = DocNode(id=make_node_id(NodeKind.LIST, "", len(nodes)), kind=NodeKind.LIST)
        for item in items:
            text = cleanup_inline(item)
            if not text:
                continue
            lst.children.append(
                DocNode(
                    id=make_node_id(NodeKind.LIST_ITEM, text, len(nodes)),
                    kind=NodeKind.LIST_ITEM,
                    text=text,
                )
            )
        if lst.children:
            nodes.append(lst)

    i = 0
    while i < n:
        line = raw_lines[i]

        if not line.strip():
            flush_para()
            i += 1
            continue

        if not para_buf:
            blank_before = i == 0 or not raw_lines[i - 1].strip()

            if blank_before:
                code = _scan_code_block(raw_lines, i)
                if code:
                    run, j = code
                    code_text = "\n".join(re.sub(r"^(?: {4}|\t)", "", ln) for ln in run)
                    nodes.append(
                        DocNode(
                            id=make_node_id(NodeKind.CODE, code_text, len(nodes)),
                            kind=NodeKind.CODE,
                            text=code_text,
                        )
                    )
                    i = j
                    continue

            quote = _scan_quote(raw_lines, i)
            if quote:
                run, j = quote
                text = cleanup_inline(" ".join(run))
                if text:
                    nodes.append(
                        DocNode(
                            id=make_node_id(NodeKind.QUOTE, text, len(nodes)),
                            kind=NodeKind.QUOTE,
                            text=text,
                        )
                    )
                i = j
                continue

            lst = _scan_list(raw_lines, i)
            if lst:
                items, j = lst
                append_list(items)
                i = j
                continue

            blank_after = i + 1 >= n or not raw_lines[i + 1].strip()
            if _looks_like_heading(line, blank_before=blank_before, blank_after=blank_after):
                text = cleanup_inline(line)
                nodes.append(
                    DocNode(
                        id=make_node_id(NodeKind.HEADING, text, len(nodes)),
                        kind=NodeKind.HEADING,
                        text=text,
                        level=1,
                    )
                )
                i += 1
                continue

        para_buf.append(line)
        i += 1

    flush_para()
    doc.nodes = nodes
    if nodes and nodes[0].kind is NodeKind.HEADING:
        doc.meta.title = nodes[0].text
    return doc


__all__ = ["ingest_txt"]
