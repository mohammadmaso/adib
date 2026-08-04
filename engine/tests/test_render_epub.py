"""End-to-end EPUB renderer tests: DocTree + Preset -> a real .epub file.

Written the same way as test_render_typst.py: shell out to the real machinery
(ebooklib's writer, then ebooklib's own reader) rather than asserting on
generated XHTML strings, so a structurally broken EPUB (bad spine, missing
manifest entry) fails here instead of surfacing only in a real reader.
"""

from __future__ import annotations

import struct
import zipfile
import zlib
from pathlib import Path

import pytest
from ebooklib import ITEM_DOCUMENT, epub

from adib_engine.models.document import Asset, BookMeta, DocNode, DocTree, NodeKind, make_node_id
from adib_engine.models.preset import Preset, Typography
from adib_engine.render.epub.compile import compile_epub
from tests.fixtures import simple_book

FONTS_DIR = Path(__file__).parents[2] / "apps/desktop/src-tauri/resources/fonts"


def _chapters(book: epub.EpubBook) -> list:
    # EpubNav subclasses EpubHtml, so ITEM_DOCUMENT includes the generated TOC
    # page too; every real assertion here cares about actual book content.
    return [it for it in book.get_items_of_type(ITEM_DOCUMENT) if not isinstance(it, epub.EpubNav)]


def _preset(**typography_overrides) -> Preset:
    typo = Typography(**typography_overrides) if typography_overrides else Typography()
    return Preset(id="test", name="Test", system_prompt="Translate faithfully.", typography=typo)


def _tiny_png() -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))

    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
    idat = chunk(b"IDAT", zlib.compress(b"\x00\xff\x00\x00"))
    iend = chunk(b"IEND", b"")
    return sig + ihdr + idat + iend


def _tree_with_image() -> DocTree:
    tree = simple_book()
    tree.assets["img1"] = Asset(id="img1", path="img1.png", mime="image/png")
    return tree


@pytest.fixture
def assets_dir(tmp_path: Path) -> Path:
    d = tmp_path / "assets"
    d.mkdir()
    (d / "img1.png").write_bytes(_tiny_png())
    return d


def test_compiles_an_english_book(tmp_path: Path, assets_dir: Path):
    out = compile_epub(
        _tree_with_image(),
        _preset(),
        target_lang="en",
        assets_dir=assets_dir,
        out_path=tmp_path / "book.epub",
        fonts_dir=FONTS_DIR,
    )
    assert out.exists()
    assert zipfile.is_zipfile(out)


def test_ltr_book_spine_direction_is_ltr(tmp_path: Path, assets_dir: Path):
    out = compile_epub(
        _tree_with_image(),
        _preset(),
        target_lang="en",
        assets_dir=assets_dir,
        out_path=tmp_path / "book.epub",
        fonts_dir=FONTS_DIR,
    )
    book = epub.read_epub(str(out))
    assert book.direction == "ltr"

    zf = zipfile.ZipFile(out)
    opf = next(n for n in zf.namelist() if n.endswith(".opf"))
    assert 'page-progression-direction="ltr"' in zf.read(opf).decode()


def test_persian_book_sets_rtl_spine_and_chapter_direction(tmp_path: Path, assets_dir: Path):
    """The core risk this renderer exists to de-risk: RTL spine + per-chapter dir."""
    tree = DocTree(
        meta=BookMeta(title="کتاب آزمایشی", source_lang="fa"),
        nodes=[
            DocNode(
                id=make_node_id(NodeKind.HEADING, "مقدمه", 0),
                kind=NodeKind.HEADING,
                text="مقدمه",
                level=1,
            ),
            DocNode(
                id=make_node_id(NodeKind.PARAGRAPH, "p1", 1),
                kind=NodeKind.PARAGRAPH,
                text="شبکه‌ها بایت‌ها را بین دستگاه‌ها جابه‌جا می‌کنند.",
            ),
        ],
    )
    out = compile_epub(
        tree,
        _preset(body_font="Vazirmatn"),
        target_lang="fa",
        assets_dir=assets_dir,
        out_path=tmp_path / "book-fa.epub",
        fonts_dir=FONTS_DIR,
    )

    book = epub.read_epub(str(out))
    assert book.direction == "rtl"

    zf = zipfile.ZipFile(out)
    opf = next(n for n in zf.namelist() if n.endswith(".opf"))
    assert 'page-progression-direction="rtl"' in zf.read(opf).decode()

    chapter = _chapters(book)[0]
    body = chapter.get_body_content().decode()
    assert 'dir="rtl"' in body
    assert "مقدمه" in body
    assert "شبکه" in body


def test_chapters_split_at_each_top_level_heading(tmp_path: Path, assets_dir: Path):
    out = compile_epub(
        _tree_with_image(),
        _preset(),
        target_lang="en",
        assets_dir=assets_dir,
        out_path=tmp_path / "book.epub",
        fonts_dir=FONTS_DIR,
    )
    book = epub.read_epub(str(out))
    chapters = _chapters(book)
    # simple_book() has two top-level H1s: Introduction, Conclusion.
    assert len(chapters) == 2

    intro = chapters[0].get_body_content().decode()
    assert "Introduction" in intro
    assert "Protocol layers" in intro
    assert "A packet in flight" in intro

    conclusion = chapters[1].get_body_content().decode()
    assert "Conclusion" in conclusion
    assert "That is all for now" in conclusion


def test_nav_toc_lists_every_chapter_by_title(tmp_path: Path, assets_dir: Path):
    out = compile_epub(
        _tree_with_image(),
        _preset(),
        target_lang="en",
        assets_dir=assets_dir,
        out_path=tmp_path / "book.epub",
        fonts_dir=FONTS_DIR,
    )
    zf = zipfile.ZipFile(out)
    nav = zf.read("EPUB/nav.xhtml").decode()
    assert "Introduction" in nav
    assert "Conclusion" in nav


def test_only_referenced_fonts_are_embedded(tmp_path: Path, assets_dir: Path):
    """A book that only uses Vazirmatn should not carry Noto Naskh Arabic's weight."""
    out = compile_epub(
        _tree_with_image(),
        _preset(body_font="Vazirmatn", mono_font="JetBrains Mono", latin_font="Noto Serif"),
        target_lang="fa",
        assets_dir=assets_dir,
        out_path=tmp_path / "book.epub",
        fonts_dir=FONTS_DIR,
    )
    zf = zipfile.ZipFile(out)
    font_files = {Path(n).name for n in zf.namelist() if n.startswith("EPUB/fonts/")}
    expected = {"Vazirmatn-Regular.ttf", "JetBrainsMono-Regular.ttf", "NotoSerif-Regular.ttf"}
    assert font_files == expected
    assert "NotoNaskhArabic-Regular.ttf" not in font_files


def test_missing_asset_reference_does_not_crash_the_compile(tmp_path: Path):
    """An image that failed to extract during ingest must degrade, not fail the export."""
    tree = simple_book()  # references "img1" via caption but no Asset is staged
    empty_assets = tmp_path / "assets"
    empty_assets.mkdir()

    out = compile_epub(
        tree,
        _preset(),
        target_lang="en",
        assets_dir=empty_assets,
        out_path=tmp_path / "book.epub",
        fonts_dir=FONTS_DIR,
    )
    assert out.exists()


def test_table_renders_as_real_html_table(tmp_path: Path, assets_dir: Path):
    out = compile_epub(
        _tree_with_image(),
        _preset(),
        target_lang="en",
        assets_dir=assets_dir,
        out_path=tmp_path / "book.epub",
        fonts_dir=FONTS_DIR,
    )
    book = epub.read_epub(str(out))
    body = _chapters(book)[0].get_body_content().decode()

    assert "<table>" in body
    assert "<th>Layer</th>" in body
    assert "<td>Transport</td>" in body
