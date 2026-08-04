"""Plain text importer: a .txt file -> DocTree.

No structural markup exists, so paragraphs are inferred from blank-line breaks
and headings from short, title-cased or all-caps lines standing alone between
blank lines. This is deliberately conservative: a missed heading just becomes a
paragraph, which is safe.
"""

from __future__ import annotations

from pathlib import Path

from adib_engine.ingest.kit import cleanup_inline
from adib_engine.models.document import DocNode, DocTree, NodeKind, make_node_id

_MAX_HEADING_CHARS = 80


def _looks_like_heading(line: str, *, blank_before: bool, blank_after: bool) -> bool:
    if not (blank_before and blank_after):
        return False
    stripped = line.strip()
    if not stripped or len(stripped) > _MAX_HEADING_CHARS:
        return False
    if stripped.endswith((".", ",", ";")):
        return False
    return stripped.isupper() or stripped.istitle()


def ingest_txt(path: Path) -> DocTree:
    text = path.read_text(encoding="utf-8", errors="replace")
    raw_lines = text.split("\n")

    doc = DocTree(parser="txt")
    nodes: list[DocNode] = []
    para_buf: list[str] = []

    def flush() -> None:
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

    for i, line in enumerate(raw_lines):
        if not line.strip():
            flush()
            continue
        blank_before = i == 0 or not raw_lines[i - 1].strip()
        blank_after = i + 1 >= len(raw_lines) or not raw_lines[i + 1].strip()
        if not para_buf and _looks_like_heading(
            line, blank_before=blank_before, blank_after=blank_after
        ):
            text = cleanup_inline(line)
            nodes.append(
                DocNode(
                    id=make_node_id(NodeKind.HEADING, text, len(nodes)),
                    kind=NodeKind.HEADING,
                    text=text,
                    level=1,
                )
            )
            continue
        para_buf.append(line)

    flush()
    doc.nodes = nodes
    if nodes and nodes[0].kind is NodeKind.HEADING:
        doc.meta.title = nodes[0].text
    return doc


__all__ = ["ingest_txt"]
