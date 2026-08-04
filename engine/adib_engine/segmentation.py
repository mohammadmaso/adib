"""Turning a document tree into translation units, and merging results back.

Segment ids are content-addressed over `node_id + source_text`. That single
choice gives three properties for free:

* re-ingesting an unchanged file produces identical ids, so existing
  translations survive;
* editing the structure in Gate 1 keeps every untouched segment;
* if a node's source text *does* change, its id changes, so the stale
  translation is dropped rather than silently kept against new text.
"""

from __future__ import annotations

import hashlib
import re

from pydantic import BaseModel

from adib_engine.models.document import DocNode, DocTree, NodeKind, TableData

#: Above this many characters a paragraph is split, to stay well inside model
#: output limits and to keep a single retry cheap.
MAX_SEGMENT_CHARS = 4000

#: Sentence boundary for Latin and Arabic-script punctuation. Deliberately
#: conservative: a missed split just yields a longer segment.
_SENTENCE_END = re.compile(r"(?<=[.!?۔؟…])\s+(?=[^\s])")


class SegmentSpec(BaseModel):
    """A translation unit derived from the tree, before it is persisted."""

    id: str
    node_id: str
    ordinal: int
    kind: NodeKind
    source_text: str
    heading_path: list[str] = []
    #: Index of this piece within its node, for paragraphs split by length.
    part: int = 0
    #: Total pieces the node was split into; 1 for the common case.
    parts: int = 1


def make_segment_id(node_id: str, source_text: str, part: int) -> str:
    digest = hashlib.sha256(f"{node_id}\x00{source_text}\x00{part}".encode()).hexdigest()
    return f"seg-{digest[:16]}"


def table_to_markdown(table: TableData) -> str:
    """Serialize a table to a pipe table for translation.

    Spans cannot be expressed in markdown, so the original TableData is kept as
    the layout template and only cell *text* round-trips through the model.
    """
    if not table.rows:
        return ""

    lines: list[str] = []
    for i, row in enumerate(table.rows):
        cells = [c.text.replace("|", r"\|").replace("\n", " ").strip() for c in row]
        lines.append("| " + " | ".join(cells) + " |")
        if i == 0:
            lines.append("| " + " | ".join("---" for _ in row) + " |")
    return "\n".join(lines)


def markdown_to_table(markdown: str, template: TableData) -> TableData:
    """Merge translated pipe-table text back onto the original table's layout.

    The template supplies structure (spans, header flags); the markdown supplies
    text. Cells the model dropped keep their source text rather than vanishing.
    """
    parsed: list[list[str]] = []
    for line in markdown.strip().splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        # Skip the |---|---| separator row.
        if re.fullmatch(r"\|(\s*:?-{2,}:?\s*\|)+", line):
            continue
        cells = [c.strip().replace(r"\|", "|") for c in line.strip("|").split("|")]
        parsed.append(cells)

    merged = template.model_copy(deep=True)
    for r, row in enumerate(merged.rows):
        for c, cell in enumerate(row):
            if r < len(parsed) and c < len(parsed[r]):
                cell.text = parsed[r][c]
    return merged


def split_long_text(text: str, limit: int = MAX_SEGMENT_CHARS) -> list[str]:
    """Split on sentence boundaries, packing into pieces under `limit`."""
    if len(text) <= limit:
        return [text]

    pieces: list[str] = []
    current = ""
    for sentence in _SENTENCE_END.split(text):
        candidate = f"{current} {sentence}".strip() if current else sentence
        if current and len(candidate) > limit:
            pieces.append(current)
            current = sentence
        else:
            current = candidate
    if current:
        pieces.append(current)

    # A single sentence longer than the limit cannot be split sensibly; send it
    # whole rather than cutting mid-clause.
    return pieces or [text]


def _node_source_text(node: DocNode) -> str | None:
    """The text a translator should receive for this node, or None to skip it."""
    if node.kind is NodeKind.TABLE and node.table is not None:
        return table_to_markdown(node.table) or None
    if node.is_translatable:
        return node.text
    return None


def build_segments(tree: DocTree) -> list[SegmentSpec]:
    """Derive every translation unit from a tree, in reading order.

    Figure captions and alt text become their own segments so that images keep
    translated captions without the figure node itself carrying prose.
    """
    heading_paths = tree.heading_paths()
    specs: list[SegmentSpec] = []
    ordinal = 0

    def emit(node_id: str, kind: NodeKind, text: str, path: list[str]) -> None:
        nonlocal ordinal
        chunks = split_long_text(text)
        for part, chunk in enumerate(chunks):
            specs.append(
                SegmentSpec(
                    id=make_segment_id(node_id, chunk, part),
                    node_id=node_id,
                    ordinal=ordinal,
                    kind=kind,
                    source_text=chunk,
                    heading_path=path,
                    part=part,
                    parts=len(chunks),
                )
            )
            ordinal += 1

    for node in tree.walk():
        path = heading_paths.get(node.id, [])

        text = _node_source_text(node)
        if text:
            emit(node.id, node.kind, text, path)

        for ref in node.assets:
            if ref.caption and ref.caption.strip():
                emit(f"{node.id}:caption:{ref.asset_id}", NodeKind.FIGURE, ref.caption, path)
            if ref.alt and ref.alt.strip():
                emit(f"{node.id}:alt:{ref.asset_id}", NodeKind.FIGURE, ref.alt, path)

    return specs


def apply_translations(tree: DocTree, translations: dict[str, str]) -> DocTree:
    """Return a copy of `tree` with translated text substituted in.

    Keyed by segment id, so a node split into several segments is rejoined in
    part order. Missing translations leave the source text in place, which keeps
    a partially translated book renderable for preview.
    """
    result = tree.model_copy(deep=True)
    specs = build_segments(tree)

    # node_id -> its pieces in order, so multi-part paragraphs rejoin correctly.
    by_node: dict[str, list[SegmentSpec]] = {}
    for spec in sorted(specs, key=lambda s: (s.node_id, s.part)):
        by_node.setdefault(spec.node_id, []).append(spec)

    def translated_for(node_id: str) -> str | None:
        pieces = by_node.get(node_id)
        if not pieces:
            return None
        if not any(translations.get(p.id) for p in pieces):
            return None
        return " ".join(translations.get(p.id) or p.source_text for p in pieces)

    for node in result.walk():
        if node.kind is NodeKind.TABLE and node.table is not None:
            merged = translated_for(node.id)
            if merged is not None:
                node.table = markdown_to_table(merged, node.table)
        else:
            text = translated_for(node.id)
            if text is not None:
                node.text = text

        for ref in node.assets:
            caption = translated_for(f"{node.id}:caption:{ref.asset_id}")
            if caption is not None:
                ref.caption = caption
            alt = translated_for(f"{node.id}:alt:{ref.asset_id}")
            if alt is not None:
                ref.alt = alt

    return result


__all__ = [
    "MAX_SEGMENT_CHARS",
    "SegmentSpec",
    "apply_translations",
    "build_segments",
    "make_segment_id",
    "markdown_to_table",
    "split_long_text",
    "table_to_markdown",
]
