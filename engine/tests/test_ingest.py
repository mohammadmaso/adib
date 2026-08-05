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

    # PDFs have no dedicated cover asset like EPUB's OPF manifest, so the first
    # page image stands in for one.
    assert tree.meta.cover_asset_id is not None
    cover = tree.assets[tree.meta.cover_asset_id]
    assert (tmp_path / "assets" / cover.path).exists()


def test_pdf_drops_a_printed_contents_page(tmp_path: Path):
    """A "Contents" page in the source PDF is redundant with the TOC the
    renderer builds from headings at render time, so it must not become
    translated prose alongside it."""
    from adib_engine.ingest.pdf import ingest_pdf

    path = tmp_path / "book_with_toc.pdf"
    fx.write_pdf_with_toc(path)

    tree = ingest_pdf(path, assets_dir=tmp_path / "assets")

    texts = [n.text for n in tree.walk() if n.text]
    assert not any(t.strip() == "Contents" for t in texts)
    assert not any("....." in t for t in texts)
    assert [h.text for h in tree.headings()] == ["Introduction"]


def test_epub_drops_a_printed_contents_page(tmp_path: Path):
    """Distinct from `EpubNav` (already excluded): a human-readable "Contents"
    chapter that ships as an ordinary spine item must also be dropped."""
    from adib_engine.ingest.epub import ingest_epub

    path = tmp_path / "book_with_toc.epub"
    fx.write_epub_with_toc_page(path)

    tree = ingest_epub(path, assets_dir=tmp_path / "assets")

    texts = [n.text for n in tree.walk() if n.text]
    assert not any(t.strip() == "Contents" for t in texts)
    assert not any(t == "Layers" for t in texts)  # only appears as a TOC link, not a heading
    assert [h.text for h in tree.headings()] == ["Introduction"]


def test_epub_cover_item_becomes_the_cover_asset(tmp_path: Path):
    """EPUB ingest recognizes a manifest item named like "cover" as the book's
    cover, distinct from any in-body figure."""
    from ebooklib import epub

    from adib_engine.ingest.epub import ingest_epub

    book = epub.EpubBook()
    book.set_identifier("cover-epub")
    book.set_title("A Book With A Cover")
    book.set_language("en")
    chapter = epub.EpubHtml(title="Intro", file_name="chap1.xhtml", lang="en")
    chapter.content = "<html><body><h1>Intro</h1><p>Hello.</p></body></html>"
    book.add_item(chapter)
    book.add_item(
        epub.EpubItem(
            uid="cover-image",
            file_name="images/cover.png",
            media_type="image/png",
            content=b"\x89PNG\r\n\x1a\n" + b"0" * 600,  # over the 512-byte floor
        )
    )
    book.toc = (epub.Link("chap1.xhtml", "Intro", "intro"),)
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.spine = ["nav", chapter]
    path = tmp_path / "book.epub"
    epub.write_epub(str(path), book)

    tree = ingest_epub(path, assets_dir=tmp_path / "assets")

    assert tree.meta.cover_asset_id is not None
    cover = tree.assets[tree.meta.cover_asset_id]
    assert cover.mime == "image/png"
    assert (tmp_path / "assets" / cover.path).exists()


def test_txt_infers_code_lists_and_quotes(tmp_path: Path):
    from adib_engine.ingest.txt import ingest_txt

    path = tmp_path / "book.txt"
    path.write_text(
        """Introduction

Networks move bytes between machines that will never meet.

    def handshake():
        return "syn-ack"

- one
- two

> a remark worth setting apart

Not a list - just a sentence with a dash in it.
""",
        encoding="utf-8",
    )

    tree = ingest_txt(path)
    kinds = [n.kind for n in tree.walk()]
    assert kinds == [
        NodeKind.HEADING,
        NodeKind.PARAGRAPH,
        NodeKind.CODE,
        NodeKind.LIST,
        NodeKind.LIST_ITEM,
        NodeKind.LIST_ITEM,
        NodeKind.QUOTE,
        NodeKind.PARAGRAPH,
    ]

    code = next(n for n in tree.walk() if n.kind is NodeKind.CODE)
    assert code.text == 'def handshake():\n    return "syn-ack"'

    quote = next(n for n in tree.walk() if n.kind is NodeKind.QUOTE)
    assert quote.text == "a remark worth setting apart"

    # A single indented line, or a lone leading dash, is too ambiguous with
    # ordinary prose to trust — both must fall back to a ordinary paragraph.
    paragraphs = [n for n in tree.walk() if n.kind is NodeKind.PARAGRAPH]
    assert paragraphs[-1].text == "Not a list - just a sentence with a dash in it."


def test_pdf_detects_a_monospace_code_block(tmp_path: Path):
    import pymupdf

    from adib_engine.ingest.pdf import ingest_pdf

    path = tmp_path / "book.pdf"
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Introduction", fontsize=24)
    page.insert_text((72, 110), "Some prose above the snippet.", fontsize=11)
    page.insert_textbox(
        pymupdf.Rect(72, 140, 300, 200),
        'def handshake():\n    return "syn-ack"',
        fontsize=11,
        fontname="Courier",
    )
    doc.save(str(path))
    doc.close()

    tree = ingest_pdf(path, assets_dir=tmp_path / "assets")
    kinds = [n.kind for n in tree.walk()]
    assert NodeKind.CODE in kinds
    code = next(n for n in tree.walk() if n.kind is NodeKind.CODE)
    assert "def handshake():" in code.text
    assert 'return "syn-ack"' in code.text


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


def test_router_reports_docling_missing_cleanly(tmp_path: Path, monkeypatch):
    """DOCX has no fast path; when the optional docling extra isn't installed
    it must fail with a clear message, not an ImportError traceback. Simulate
    that via the import hook rather than relying on the ambient environment,
    since dev/CI environments may have the docling extra installed."""
    import builtins

    from adib_engine.ingest.router import UnsupportedFormatError, route

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "docling" or name.startswith("docling."):
            raise ImportError(f"No module named {name!r}")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    path = tmp_path / "book.docx"
    path.write_bytes(b"not a real docx")

    with pytest.raises(UnsupportedFormatError, match="docling"):
        route(path)


def test_router_escalates_to_docling_when_forced(tmp_path: Path):
    """With the optional docling extra installed, forcing escalation on a
    normal PDF should route through docling instead of raising."""
    pytest.importorskip("docling")
    from adib_engine.ingest.router import route

    path = tmp_path / "book.pdf"
    fx.write_pdf(path)

    tree, report = route(path, assets_dir=tmp_path / "assets", force_docling=True)

    assert tree.parser == "docling"
    assert report.parser == "docling"
    assert report.escalated is True
    assert report.escalation_reason == "forced"
