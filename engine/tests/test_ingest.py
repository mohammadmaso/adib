"""Golden-file tests for every ingest importer.

Each format's fixture (tests/fixture_documents.py) encodes the same book:
two H1s, one nested H2, a table, a captioned figure, a list, and a code block.
Every importer must reconstruct the same shape — kinds, heading levels, and
reading order — regardless of how different the source syntax is.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from adib_engine.models.document import NodeKind
from tests import fixture_documents as fx


def _kinds(tree) -> list[NodeKind]:
    # tree.walk() already recurses into list children, so this is a flat pass.
    return [n.kind for n in tree.walk()]

# Every fixture reconstructs: H1, P, H2, P, TABLE, FIGURE, LIST(+2 items), CODE, H1, P.
_EXPECTED_KINDS = [
    NodeKind.HEADING,
    NodeKind.PARAGRAPH,
    NodeKind.HEADING,
    NodeKind.PARAGRAPH,
    NodeKind.TABLE,
    NodeKind.FIGURE,
    NodeKind.LIST,
    NodeKind.LIST_ITEM,
    NodeKind.LIST_ITEM,
    NodeKind.CODE,
    NodeKind.HEADING,
    NodeKind.PARAGRAPH,
]


def test_epub_matches_the_golden_shape(tmp_path: Path):
    from adib_engine.ingest.epub import ingest_epub

    path = tmp_path / "book.epub"
    fx.write_epub(path)

    tree = ingest_epub(path, assets_dir=tmp_path / "assets")

    assert tree.meta.title == "A Short Book About Networks"
    assert tree.meta.authors == ["Ada Lovelace"]
    assert _kinds(tree) == _EXPECTED_KINDS

    headings = tree.headings()
    assert [h.level for h in headings] == [1, 2, 1]
    assert [h.text for h in headings] == ["Introduction", "Layers", "Conclusion"]

    table = next(n for n in tree.walk() if n.kind is NodeKind.TABLE)
    assert [c.text for c in table.table.rows[0]] == ["Layer", "Purpose"]
    assert table.table.rows[0][0].is_header is True
    assert table.table.rows[1][0].text == "Transport"

    figure = next(n for n in tree.walk() if n.kind is NodeKind.FIGURE)
    assert figure.assets[0].alt == "Diagram"
    assert figure.text == "A packet in flight"
    assert len(tree.assets) == 1


def test_markdown_matches_the_golden_shape(tmp_path: Path):
    from adib_engine.ingest.markdown import ingest_markdown

    path = tmp_path / "book.md"
    fx.write_markdown(path)

    tree = ingest_markdown(path)

    # The markdown fixture carries no image reference, unlike the other formats.
    expected = [k for k in _EXPECTED_KINDS if k is not NodeKind.FIGURE]
    assert _kinds(tree) == expected
    headings = tree.headings()
    assert [h.level for h in headings] == [1, 2, 1]
    table = next(n for n in tree.walk() if n.kind is NodeKind.TABLE)
    assert table.table.rows[1][0].text == "Transport"


def test_txt_infers_headings_and_paragraphs(tmp_path: Path):
    from adib_engine.ingest.txt import ingest_txt

    path = tmp_path / "book.txt"
    fx.write_txt(path)

    tree = ingest_txt(path)

    kinds = [n.kind for n in tree.walk()]
    assert kinds == [
        NodeKind.HEADING,
        NodeKind.PARAGRAPH,
        NodeKind.HEADING,
        NodeKind.PARAGRAPH,
    ]
    assert [h.text for h in tree.headings()] == ["Introduction", "Layers"]


def test_html_matches_the_golden_shape(tmp_path: Path):
    from adib_engine.ingest.html import ingest_html

    path = tmp_path / "book.html"
    fx.write_html(path)

    tree = ingest_html(path)

    assert tree.meta.title == "A Short Book About Networks"
    assert _kinds(tree) == _EXPECTED_KINDS
    figure = next(n for n in tree.walk() if n.kind is NodeKind.FIGURE)
    assert len(figure.assets) == 1
    assert len(tree.assets) == 1


def test_pdf_fast_path_infers_headings_from_font_size(tmp_path: Path):
    from adib_engine.ingest.pdf import ingest_pdf

    path = tmp_path / "book.pdf"
    fx.write_pdf(path)

    tree = ingest_pdf(path, assets_dir=tmp_path / "assets")

    kinds = [n.kind for n in tree.walk()]
    assert kinds == [
        NodeKind.HEADING,
        NodeKind.PARAGRAPH,
        NodeKind.HEADING,
        NodeKind.PARAGRAPH,
        NodeKind.TABLE,
        NodeKind.HEADING,
        NodeKind.PARAGRAPH,
    ]
    headings = tree.headings()
    assert [h.level for h in headings] == [1, 2, 1]
    assert [h.text for h in headings] == ["Introduction", "Layers", "Conclusion"]

    table = next(n for n in tree.walk() if n.kind is NodeKind.TABLE)
    assert table.table.rows[0][0].text == "Layer"
    assert table.table.rows[1][0].text == "Transport"

    # Every node carries page provenance for Gate 1's bbox highlighting.
    assert all(n.source.page_start == 1 for n in tree.walk())


def test_pdf_probe_flags_a_scanned_document(tmp_path: Path):
    from adib_engine.ingest.pdf import probe_pdf

    path = tmp_path / "scanned.pdf"
    fx.write_scanned_pdf(path)

    result = probe_pdf(path)

    assert result.likely_scanned is True
    assert result.should_escalate is True


# -- router ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("writer", "filename", "expected_parser"),
    [
        (fx.write_epub, "book.epub", "epub"),
        (fx.write_markdown, "book.md", "markdown"),
        (fx.write_txt, "book.txt", "txt"),
        (fx.write_html, "book.html", "html"),
        (fx.write_pdf, "book.pdf", "pymupdf"),
    ],
)
def test_router_dispatches_by_extension(tmp_path: Path, writer, filename, expected_parser):
    from adib_engine.ingest.router import route

    path = tmp_path / filename
    writer(path)

    tree, report = route(path, assets_dir=tmp_path / "assets")

    assert tree.parser == expected_parser
    assert report.parser == expected_parser
    assert report.escalated is False


def test_router_rejects_a_scanned_pdf(tmp_path: Path):
    from adib_engine.ingest.router import ScannedPdfError, route

    path = tmp_path / "scanned.pdf"
    fx.write_scanned_pdf(path)

    with pytest.raises(ScannedPdfError):
        route(path)


def test_router_rejects_an_unsupported_extension(tmp_path: Path):
    from adib_engine.ingest.router import UnsupportedFormatError, route

    path = tmp_path / "book.xyz"
    path.write_text("nothing")

    with pytest.raises(UnsupportedFormatError):
        route(path)


def test_router_reports_docling_missing_cleanly(tmp_path: Path):
    """DOCX has no fast path yet; without the optional docling extra it must
    fail with a clear message, not an ImportError traceback."""
    from adib_engine.ingest.router import UnsupportedFormatError, route

    path = tmp_path / "book.docx"
    path.write_bytes(b"not a real docx")

    with pytest.raises(UnsupportedFormatError, match="docling"):
        route(path)
