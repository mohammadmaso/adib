"""PDF fast path: a born-digital PDF -> DocTree, using pymupdf directly.

This is the cheap path for PDFs with a real text layer: no ML model, just
pymupdf's own text/table/image extraction. `probe_pdf()` decides whether a file
even qualifies; weak text layers or dense multi-column layouts should escalate
to the Docling fallback (`ingest.docling`) instead of running this module.

Heading levels come from two sources, in priority order:
1. The PDF's own outline/bookmarks (`doc.get_toc()`) — authoritative when present.
2. Font-size clustering across the whole document — sizes above the body size
   are bucketed into up to 6 heading levels, largest first.
Neither is perfect; Gate 1 in the UI is where the user fixes what's left.

A printed "Contents" page is a different problem from either of those: it's
prose-shaped (a heading plus a wall of text) but it's not book content — every
entry it lists already exists as a heading elsewhere in the tree, and the
renderer regenerates a real navigational TOC from those headings at render
time. Left alone it would get translated and rendered as a duplicate, stale
copy of the book's own structure, so `ingest_pdf` recognizes and drops it
(see `_looks_like_toc_page`).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import pymupdf

from adib_engine.ingest.kit import (
    cleanup_inline,
    is_toc_title,
    looks_like_toc_lines,
    make_asset_stager,
    table_from_pipe,
)
from adib_engine.models.document import (
    AssetRef,
    DocNode,
    DocTree,
    NodeKind,
    SourceSpan,
    make_node_id,
)

#: Below this, a page is considered to have too little extractable text to
#: trust the fast path — scans, image-only pages, or heavy OCR artifacts.
MIN_CHARS_PER_PAGE = 40

#: Smallest on-page image side (PDF points) still treated as a picture rather
#: than a rule, spacer, or decorative shim.
MIN_IMAGE_PT = 24.0

#: Above this, extraction quality tends to degrade (multi-column magazine
#: layouts, dense forms) and Docling's layout model should take over instead.
MAX_COLUMNS_HEURISTIC = 3

#: Substrings (casefolded) of font names that indicate a monospace typeface —
#: the strongest signal a text block is code rather than prose, since authors
#: overwhelmingly typeset code blocks in a dedicated monospace font.
_MONOSPACE_FONT_HINTS = ("mono", "courier", "consolas", "menlo", "typewriter")

#: Share of a block's characters that must sit in a monospace font, and the
#: minimum line count, before it's trusted as code rather than prose that
#: happens to use a monospace face for a word or two.
MIN_MONOSPACE_RATIO = 0.9
MIN_CODE_LINES = 2


@dataclass
class ProbeResult:
    pages: int
    chars_per_page: float
    has_text_layer: bool
    likely_scanned: bool
    should_escalate: bool
    reason: str = ""


def probe_pdf(path: Path) -> ProbeResult:
    """Decide whether the fast path can handle this PDF."""
    doc = pymupdf.open(str(path))
    try:
        pages = doc.page_count
        if pages == 0:
            return ProbeResult(0, 0.0, False, True, True, "empty document")
        total_chars = 0
        max_cols = 0
        for page in doc:
            text = page.get_text("text")
            total_chars += len(text)
            max_cols = max(max_cols, _estimate_columns(page))
        chars_per_page = total_chars / pages
        has_text_layer = chars_per_page >= MIN_CHARS_PER_PAGE
        likely_scanned = not has_text_layer
        should_escalate = likely_scanned or max_cols > MAX_COLUMNS_HEURISTIC
        reason = (
            "no reliable text layer"
            if likely_scanned
            else (f"{max_cols} columns detected" if should_escalate else "")
        )
        return ProbeResult(
            pages, chars_per_page, has_text_layer, likely_scanned, should_escalate, reason
        )
    finally:
        doc.close()


def _estimate_columns(page: pymupdf.Page) -> int:
    """Rough column count: distinct x0 clusters among left-aligned text blocks."""
    blocks = page.get_text("blocks")
    xs = sorted({round(b[0] / 20) * 20 for b in blocks if b[4].strip()})
    return max(1, len(xs))


# -- heading level inference ---------------------------------------------------


def _bookmark_levels(doc: pymupdf.Document) -> dict[int, list[tuple[int, str]]]:
    """Map page number -> [(level, title), ...] from the PDF outline."""
    by_page: dict[int, list[tuple[int, str]]] = {}
    for level, title, page1based in doc.get_toc():
        page = max(page1based - 1, 0)
        by_page.setdefault(page, []).append((level, title.strip()))
    return by_page


def _font_size_levels(doc: pymupdf.Document) -> dict[float, int]:
    """Cluster font sizes across the document into heading levels 1..6.

    The most frequent size is assumed to be body text; everything larger is
    bucketed by rank into levels, largest first.
    """
    sizes = Counter()
    for page in doc:
        for block in page.get_text("dict")["blocks"]:
            if block.get("type") != 0:
                continue
            for line in block["lines"]:
                for span in line["spans"]:
                    if span["text"].strip():
                        sizes[round(span["size"], 1)] += len(span["text"])

    if not sizes:
        return {}
    body_size = sizes.most_common(1)[0][0]
    larger = sorted((s for s in sizes if s > body_size), reverse=True)
    return {size: min(i + 1, 6) for i, size in enumerate(larger)}


# -- main ingest -----------------------------------------------------------


def ingest_pdf(path: Path, *, assets_dir: Path | None = None) -> DocTree:
    """Parse a born-digital PDF into a DocTree using pymupdf's text/table/image APIs."""
    doc = pymupdf.open(str(path))
    tree = DocTree(parser="pymupdf")
    stage = make_asset_stager(tree, assets_dir or path.parent / f"{path.stem}.assets")

    tree.meta.title = (doc.metadata.get("title") or "").strip() or None
    author = (doc.metadata.get("author") or "").strip()
    if author:
        tree.meta.authors = [a.strip() for a in author.replace(";", ",").split(",") if a.strip()]

    bookmarks = _bookmark_levels(doc)
    font_levels = _font_size_levels(doc)

    nodes: list[DocNode] = []

    try:
        # Once a contents page is found, a following page that's still mostly
        # leader-dot entries is treated as its continuation even without its
        # own "Contents" heading (multi-page TOCs are common in longer books).
        in_toc = False
        for page_no in range(doc.page_count):
            page = doc[page_no]
            page_nodes, page_lines = _ingest_page(
                page, page_no, tree, stage, bookmarks, font_levels, nodes
            )
            in_toc = _looks_like_toc_page(page_nodes, page_lines, continuing=in_toc)
            if in_toc:
                continue
            nodes.extend(page_nodes)
        if doc.page_count:
            # PDFs carry no dedicated cover asset (unlike EPUB's OPF manifest),
            # so the first page image stands in for one — that's what Gate 3's
            # cover panel and the export cover wiring key off.
            cover_png = doc[0].get_pixmap(dpi=150).tobytes("png")
            tree.meta.cover_asset_id = stage(cover_png, "image/png", ext="png")
    finally:
        doc.close()

    tree.nodes = nodes
    return tree


def _looks_like_toc_page(
    page_nodes: list[DocNode], page_lines: list[str], *, continuing: bool
) -> bool:
    """True when a page's own content, not its position, marks it as a TOC.

    A page starts a TOC run when one of its headings names one ("Contents"),
    regardless of how the rest of the page reads. A page continues a run only
    while it's still mostly leader-dot entries — a "Contents" page's opening
    paragraph is one thing, but a page after it that's back to normal prose
    ends the run rather than being swallowed too. `page_lines` carries each
    text block's *visual* lines (pymupdf joins them with spaces into
    `DocNode.text`, which would hide the one-entry-per-line shape a contents
    page relies on).
    """
    if any(n.kind is NodeKind.HEADING and is_toc_title(n.text or "") for n in page_nodes):
        return True
    if continuing:
        return looks_like_toc_lines(page_lines)
    return False


def _block_text_lines(block: dict) -> list[str]:
    """This block's text, one entry per visual line (spans joined, not lines)."""
    lines: list[str] = []
    for line in block["lines"]:
        joined = "".join(span["text"] for span in line["spans"] if span["text"])
        joined = cleanup_inline(joined)
        if joined:
            lines.append(joined)
    return lines


def _ingest_page(
    page: pymupdf.Page,
    page_no: int,
    tree: DocTree,
    stage,
    bookmarks: dict[int, list[tuple[int, str]]],
    font_levels: dict[float, int],
    nodes: list[DocNode],
) -> tuple[list[DocNode], list[str]]:
    """Parse one page into nodes, in reading order, plus its raw text lines.

    `nodes` is the tree's accumulated node list so far — used only to seed
    stable ordinals for this page's node ids, never mutated here. The caller
    decides whether to keep or drop the page (see `_looks_like_toc_page`); the
    returned lines feed that decision without being a node's own text.
    """
    #: (y0, x0, DocNode) so every element on the page can be emitted in true
    #: reading order regardless of which pymupdf API produced it.
    page_items: list[tuple[float, float, DocNode]] = []
    page_lines: list[str] = []

    def span(bbox: pymupdf.Rect) -> SourceSpan:
        return SourceSpan(page_start=page_no + 1, page_end=page_no + 1, bbox=tuple(bbox))

    table_bboxes: list[pymupdf.Rect] = []
    for table in page.find_tables().tables:
        if table.row_count < 2 or table.col_count < 2:
            continue
        md = table.to_markdown()
        parsed = table_from_pipe(md.splitlines())
        if parsed is None:
            continue
        bbox = pymupdf.Rect(table.bbox)
        page_items.append(
            (
                bbox.y0,
                bbox.x0,
                DocNode(
                    id=make_node_id(NodeKind.TABLE, md, len(nodes) + len(page_items)),
                    kind=NodeKind.TABLE,
                    table=parsed,
                    source=span(bbox),
                ),
            )
        )
        table_bboxes.append(bbox)

    page_width = page.rect.width or 1.0
    for info in page.get_image_info(xrefs=True):
        xref = info.get("xref", 0)
        if not xref:
            continue
        bbox = pymupdf.Rect(info["bbox"])
        if bbox.width < MIN_IMAGE_PT or bbox.height < MIN_IMAGE_PT:
            # Rules, spacers, and 1px shims: laying these out as figures adds
            # noise to a book that never showed them as pictures.
            continue
        try:
            extracted = page.parent.extract_image(xref)
        except Exception:
            continue
        data = extracted.get("image")
        if not data:
            continue
        mime = f"image/{extracted.get('ext', 'png')}"
        aid = stage(data, mime, ext=extracted.get("ext"))
        asset = tree.assets.get(aid)
        if asset is not None and asset.width is None:
            asset.width = extracted.get("width")
            asset.height = extracted.get("height")
        page_items.append(
            (
                bbox.y0,
                bbox.x0,
                DocNode(
                    id=make_node_id(NodeKind.FIGURE, f"{xref}", len(nodes) + len(page_items)),
                    kind=NodeKind.FIGURE,
                    assets=[AssetRef(asset_id=aid)],
                    source=span(bbox),
                    # How wide the picture was on its original page, so the
                    # renderer can keep a half-page diagram half-page wide
                    # instead of blowing every image up to the text column.
                    attrs={"width_ratio": round(min(bbox.width / page_width, 1.0), 4)},
                ),
            )
        )

    # Prose: pymupdf's text blocks, skipping regions already claimed by a table.
    heading_hits = {title: level for level, title in bookmarks.get(page_no, [])}

    for block in page.get_text("dict")["blocks"]:
        if block.get("type") != 0:
            continue
        bbox = pymupdf.Rect(block["bbox"])
        if any(bbox.intersects(t) for t in table_bboxes):
            continue

        block_text, dominant_size = _block_text_and_size(block)
        block_text = cleanup_inline(block_text)
        if not block_text:
            continue
        page_lines.extend(_block_text_lines(block))

        if _looks_like_code(block):
            code_text = _block_code_text(block)
            page_items.append(
                (
                    bbox.y0,
                    bbox.x0,
                    DocNode(
                        id=make_node_id(NodeKind.CODE, code_text, len(nodes) + len(page_items)),
                        kind=NodeKind.CODE,
                        text=code_text,
                        source=span(bbox),
                    ),
                )
            )
            continue

        level = _heading_level_for(block_text, heading_hits, dominant_size, font_levels)
        kind = NodeKind.HEADING if level else NodeKind.PARAGRAPH
        page_items.append(
            (
                bbox.y0,
                bbox.x0,
                DocNode(
                    id=make_node_id(kind, block_text, len(nodes) + len(page_items)),
                    kind=kind,
                    text=block_text,
                    level=level,
                    source=span(bbox),
                ),
            )
        )

    page_items.sort(key=lambda item: (item[0], item[1]))
    return [node for _, _, node in page_items], page_lines


def _block_text_and_size(block: dict) -> tuple[str, float]:
    """Join a text block's spans into one string; return its dominant font size."""
    parts: list[str] = []
    sizes: Counter = Counter()
    for line in block["lines"]:
        line_parts = []
        for span in line["spans"]:
            if span["text"]:
                line_parts.append(span["text"])
                sizes[round(span["size"], 1)] += len(span["text"])
        if line_parts:
            parts.append("".join(line_parts))
    text = " ".join(parts)
    dominant = sizes.most_common(1)[0][0] if sizes else 0.0
    return text, dominant


def _looks_like_code(block: dict) -> bool:
    """True when a text block is set almost entirely in a monospace font.

    Font family is a much stronger signal than anything shape-based (unlike
    plain text, a PDF block already carries the typeface the author chose),
    so this only needs two guards: enough lines that it isn't a single
    monospaced word or figure label, and a high monospace character ratio so
    a stray inline `code` span in an otherwise prose block doesn't trip it.
    """
    if len(block["lines"]) < MIN_CODE_LINES:
        return False
    total = 0
    mono = 0
    for line in block["lines"]:
        for sp in line["spans"]:
            length = len(sp["text"])
            total += length
            font = sp.get("font", "").casefold()
            if length and any(hint in font for hint in _MONOSPACE_FONT_HINTS):
                mono += length
    return total > 0 and mono / total >= MIN_MONOSPACE_RATIO


def _block_code_text(block: dict) -> str:
    """Join a code block's lines preserving line breaks and indentation,
    unlike `_block_text_and_size` which flattens everything into prose.
    """
    lines = ["".join(sp["text"] for sp in line["spans"]).rstrip() for line in block["lines"]]
    return "\n".join(lines).strip("\n")


def _heading_level_for(
    text: str,
    heading_hits: dict[str, int],
    size: float,
    font_levels: dict[float, int],
) -> int | None:
    # Bookmark match is authoritative: exact or prefix match against the
    # block's start (bookmark titles are sometimes truncated by the PDF writer).
    for title, level in heading_hits.items():
        if text.startswith(title) or title.startswith(text):
            return level
    return font_levels.get(size)


__all__ = ["ProbeResult", "ingest_pdf", "probe_pdf"]
