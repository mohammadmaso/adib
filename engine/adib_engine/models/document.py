"""The document tree: one structure the whole pipeline operates on.

Every parser (PyMuPDF, Docling, ebooklib, ...) emits a `DocTree`, and both
renderers (Typst, EPUB) consume one. Nothing downstream of ingest knows or cares
which parser ran.

Translation swaps `DocNode.text` in place and leaves `id` untouched, so a
translated tree stays structurally identical to its source and the two can be
diffed, re-merged, or rendered side by side.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class NodeKind(StrEnum):
    HEADING = "heading"
    PARAGRAPH = "paragraph"
    LIST = "list"
    LIST_ITEM = "list_item"
    TABLE = "table"
    FIGURE = "figure"
    CODE = "code"
    QUOTE = "quote"
    FOOTNOTE = "footnote"
    TOC = "toc"
    FRONT_MATTER = "front_matter"
    BACK_MATTER = "back_matter"
    PAGE_BREAK = "page_break"
    EQUATION = "equation"


#: Kinds whose text must never be sent to a translator as prose. Code and
#: equations are protected wholesale; page breaks carry no text at all.
UNTRANSLATABLE_KINDS = frozenset({NodeKind.CODE, NodeKind.EQUATION, NodeKind.PAGE_BREAK})

#: Kinds that carry structural children rather than their own prose.
CONTAINER_KINDS = frozenset(
    {NodeKind.LIST, NodeKind.FRONT_MATTER, NodeKind.BACK_MATTER, NodeKind.TOC}
)


class SourceSpan(BaseModel):
    """Where a node came from in the original file, for provenance and previews."""

    page_start: int | None = None
    page_end: int | None = None
    #: (x0, y0, x1, y1) in PDF points, used to highlight the region in Gate 1.
    bbox: tuple[float, float, float, float] | None = None
    #: For EPUB/HTML sources: the spine item this node came from.
    href: str | None = None


class Asset(BaseModel):
    """An extracted image, stored on disk beside the project database."""

    id: str
    #: Path relative to the project's asset directory, so projects stay movable.
    path: str
    mime: str
    width: int | None = None
    height: int | None = None
    sha256: str | None = None
    #: Set by the figure report when heuristics suggest baked-in text that the
    #: pipeline cannot translate.
    has_embedded_text: bool = False


class AssetRef(BaseModel):
    asset_id: str
    #: Caption and alt text are prose and DO get translated.
    caption: str | None = None
    alt: str | None = None


class TableCell(BaseModel):
    #: Inline markdown, same as DocNode.text.
    text: str = ""
    colspan: int = 1
    rowspan: int = 1
    is_header: bool = False


class TableData(BaseModel):
    rows: list[list[TableCell]] = Field(default_factory=list)
    caption: str | None = None

    @property
    def n_cols(self) -> int:
        return max((sum(c.colspan for c in row) for row in self.rows), default=0)


class DocNode(BaseModel):
    """A single structural element. Children nest for lists and matter sections."""

    id: str
    kind: NodeKind
    #: Heading depth 1..6. None for every other kind.
    level: int | None = None
    #: Inline markdown: bold, italic, links, inline code. Block structure lives
    #: in `kind` and `children`, never in this string.
    text: str | None = None
    children: list[DocNode] = Field(default_factory=list)
    assets: list[AssetRef] = Field(default_factory=list)
    table: TableData | None = None
    source: SourceSpan = Field(default_factory=SourceSpan)
    #: Kind-specific extras: code language, list ordered/start, footnote marker,
    #: numbering prefix, explicit lang override for LTR islands.
    attrs: dict[str, Any] = Field(default_factory=dict)

    def walk(self) -> Iterator[DocNode]:
        """Depth-first over this node and its descendants, in reading order."""
        yield self
        for child in self.children:
            yield from child.walk()

    @property
    def is_translatable(self) -> bool:
        """True when this node carries prose a translator should see."""
        if self.kind in UNTRANSLATABLE_KINDS:
            return False
        return bool(self.text and self.text.strip())


class BookMeta(BaseModel):
    title: str | None = None
    subtitle: str | None = None
    authors: list[str] = Field(default_factory=list)
    publisher: str | None = None
    isbn: str | None = None
    published: str | None = None
    #: BCP-47, detected at ingest and confirmed by the analysis agent.
    source_lang: str | None = None
    cover_asset_id: str | None = None
    #: Free-form extras the source format carried (EPUB OPF metadata, PDF info dict).
    extra: dict[str, Any] = Field(default_factory=dict)


class DocTree(BaseModel):
    """A whole book: metadata, ordered top-level nodes, and the asset table."""

    meta: BookMeta = Field(default_factory=BookMeta)
    nodes: list[DocNode] = Field(default_factory=list)
    assets: dict[str, Asset] = Field(default_factory=dict)
    #: Which parser produced this tree, e.g. "pymupdf4llm" or "docling". Recorded
    #: so a re-ingest can tell whether escalating parsers would change anything.
    parser: str | None = None

    def walk(self) -> Iterator[DocNode]:
        for node in self.nodes:
            yield from node.walk()

    def by_id(self) -> dict[str, DocNode]:
        return {n.id: n for n in self.walk()}

    def headings(self) -> list[DocNode]:
        return [n for n in self.walk() if n.kind is NodeKind.HEADING]

    def heading_paths(self) -> dict[str, list[str]]:
        """Map each node id to the heading titles above it, outermost first.

        Gives the translator its "where am I in the book" context, which is what
        keeps chapter-dependent terminology stable.
        """
        paths: dict[str, list[str]] = {}
        stack: list[tuple[int, str]] = []

        for node in self.walk():
            if node.kind is NodeKind.HEADING:
                level = node.level or 1
                while stack and stack[-1][0] >= level:
                    stack.pop()
                paths[node.id] = [title for _, title in stack]
                stack.append((level, (node.text or "").strip()))
            else:
                paths[node.id] = [title for _, title in stack]

        return paths


DocNode.model_rebuild()


def make_node_id(kind: NodeKind | str, text: str | None, ordinal: int) -> str:
    """Stable id for a node.

    Content-hashed so that re-ingesting an unchanged file yields the same ids and
    already-translated segments still match. The ordinal disambiguates nodes with
    identical text (repeated headers, "Figure 1" captions, blank paragraphs).
    """
    digest = hashlib.sha256(f"{kind}\x00{text or ''}\x00{ordinal}".encode()).hexdigest()
    return f"{kind}-{digest[:12]}"
