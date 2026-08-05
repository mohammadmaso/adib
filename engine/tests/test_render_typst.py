"""End-to-end PDF renderer tests: DocTree + Preset -> a real compiled PDF.

These shell out to the actual typst binary rather than just asserting on
generated markup text, because the whole point of front-loading this task (per
the build order) is to prove Typst can typeset RTL Persian well *before* the
translation agents are built on top of it. A markup-string assertion would not
catch a font that fails to embed or a bidi paragraph that silently mis-renders.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from adib_engine.models.document import (
    Asset,
    AssetRef,
    BookMeta,
    DocNode,
    DocTree,
    NodeKind,
    SourceSpan,
    make_node_id,
)
from adib_engine.models.preset import Preset
from adib_engine.render.typst.compile import TypstCompileError, compile_pdf
from tests.fixtures import simple_book

#: The dev binary lives under the Tauri app's own binaries dir (see
#: scripts/prepare-binaries.sh), not on PATH; fall back to PATH so a machine
#: with a system-installed typst still runs these tests.
_BUNDLED_TYPST = next(
    (
        p
        for p in (Path(__file__).parents[2] / "apps/desktop/src-tauri/binaries").glob("typst-*")
        if p.is_file()
    ),
    None,
)
TYPST_BIN = _BUNDLED_TYPST or shutil.which("typst")
FONTS_DIR = Path(__file__).parents[2] / "apps/desktop/src-tauri/resources/fonts"

pytestmark = pytest.mark.skipif(TYPST_BIN is None, reason="no typst binary found")


def _preset(**typography_overrides) -> Preset:
    from adib_engine.models.preset import Typography

    typo = Typography(**typography_overrides) if typography_overrides else Typography()
    return Preset(id="test", name="Test", system_prompt="Translate faithfully.", typography=typo)


def _compile(tree, preset, *, target_lang, assets_dir, out_path):
    """compile_pdf bound to the bundled typst binary + font set found above."""
    return compile_pdf(
        tree,
        preset,
        target_lang=target_lang,
        assets_dir=assets_dir,
        out_path=out_path,
        typst_bin=TYPST_BIN,
        fonts_dir=FONTS_DIR,
    )


def _tiny_png() -> bytes:
    """A real, valid 1x1 red PNG, built rather than hand-copied as hex."""
    import struct
    import zlib

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))

    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
    raw = b"\x00\xff\x00\x00"  # filter byte + one RGB pixel
    idat = chunk(b"IDAT", zlib.compress(raw))
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


def test_compiles_a_born_digital_english_book(tmp_path: Path, assets_dir: Path):
    out = _compile(
        _tree_with_image(),
        _preset(),
        target_lang="en",
        assets_dir=assets_dir,
        out_path=tmp_path / "book.pdf",
    )
    assert out.exists()
    assert out.stat().st_size > 0


def test_persian_rtl_book_round_trips_its_text(tmp_path: Path, assets_dir: Path):
    """The core risk this task exists to de-risk: Persian, RTL, real fonts."""
    import pymupdf

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
    out = _compile(
        tree,
        _preset(body_font="Vazirmatn"),
        target_lang="fa",
        assets_dir=assets_dir,
        out_path=tmp_path / "book-fa.pdf",
    )

    doc = pymupdf.open(str(out))
    try:
        text = doc[0].get_text()
    finally:
        doc.close()

    # get_text() returns logical (not visual) order, so the Persian words
    # themselves must survive the round trip even though whitespace/joiners
    # around them do not.
    assert "مقدمه" in text
    assert "شبکه" in text
    assert "بایت" in text


def test_table_and_figure_render_without_error(tmp_path: Path, assets_dir: Path):
    out = _compile(
        _tree_with_image(),
        _preset(),
        target_lang="en",
        assets_dir=assets_dir,
        out_path=tmp_path / "book.pdf",
    )
    import pymupdf

    doc = pymupdf.open(str(out))
    try:
        full_text = "\n".join(page.get_text() for page in doc)
        image_count = sum(len(page.get_images()) for page in doc)
    finally:
        doc.close()

    assert "Protocol layers" in full_text
    assert "A packet in flight" in full_text
    assert image_count >= 1


def test_cover_asset_renders_as_a_leading_full_bleed_page(tmp_path: Path, assets_dir: Path):
    import pymupdf

    tree_without_cover = _tree_with_image()
    out_without = _compile(
        tree_without_cover,
        _preset(),
        target_lang="en",
        assets_dir=assets_dir,
        out_path=tmp_path / "no-cover.pdf",
    )
    doc = pymupdf.open(str(out_without))
    try:
        pages_without = doc.page_count
    finally:
        doc.close()

    tree_with_cover = _tree_with_image()
    tree_with_cover.meta.cover_asset_id = "img1"
    out_with = _compile(
        tree_with_cover,
        _preset(),
        target_lang="en",
        assets_dir=assets_dir,
        out_path=tmp_path / "with-cover.pdf",
    )
    doc = pymupdf.open(str(out_with))
    try:
        pages_with = doc.page_count
        cover_page_images = len(doc[0].get_images())
    finally:
        doc.close()

    assert pages_with == pages_without + 1
    assert cover_page_images >= 1


def test_page_binding_mirrors_for_rtl_targets(tmp_path: Path, assets_dir: Path):
    """RTL books bind on the right; this is what makes margins mirror correctly."""
    from adib_engine.render.typst.theme import build_theme

    theme_fa = build_theme(_preset().typography, target_lang="fa")
    theme_en = build_theme(_preset().typography, target_lang="en")

    assert "binding: right" in theme_fa
    assert "binding: left" in theme_en


def test_missing_asset_reference_does_not_crash_the_compile(tmp_path: Path):
    """A figure whose asset id was never staged must be skipped, not fatal."""
    tree = simple_book()  # references "img1" but no asset is staged this time
    empty_assets = tmp_path / "assets"
    empty_assets.mkdir()

    out = _compile(
        tree,
        _preset(),
        target_lang="en",
        assets_dir=empty_assets,
        out_path=tmp_path / "book.pdf",
    )
    assert out.exists()


def test_invalid_typst_source_raises_with_diagnostics(tmp_path: Path, assets_dir: Path):
    """A malformed .typ document must surface typst's own error, not a silent failure."""
    from adib_engine.render.typst import compile as compile_mod

    original = compile_mod.render_typst_source
    compile_mod.render_typst_source = lambda *a, **kw: "#this-is-not-a-function-call(("

    try:
        with pytest.raises(TypstCompileError) as exc_info:
            _compile(
                simple_book(),
                _preset(),
                target_lang="en",
                assets_dir=assets_dir,
                out_path=tmp_path / "book.pdf",
            )
        assert exc_info.value.stderr
    finally:
        compile_mod.render_typst_source = original


def _figure_tree(**attrs) -> DocTree:
    tree = DocTree(assets={"img1": Asset(id="img1", path="img1.png", mime="image/png")})
    tree.nodes = [
        DocNode(
            id=make_node_id(NodeKind.FIGURE, "f", 0),
            kind=NodeKind.FIGURE,
            assets=[AssetRef(asset_id="img1")],
            attrs=attrs,
        )
    ]
    return tree


def test_figure_keeps_its_share_of_the_source_page_width():
    """A half-page picture in the source book stays half-page wide."""
    from adib_engine.render.typst.document import tree_to_typst_body

    body = tree_to_typst_body(_figure_tree(width_ratio=0.45))
    assert 'image("img1.png", width: 45.0%)' in body


def test_figure_without_a_measured_width_fills_the_text_column():
    from adib_engine.render.typst.document import tree_to_typst_body

    body = tree_to_typst_body(_figure_tree())
    assert 'image("img1.png", width: 100%)' in body


def test_uncaptioned_image_renders_in_place_without_figure_numbering():
    """The source book had no "Figure 1:" under it, so neither does the export."""
    from adib_engine.render.typst.document import tree_to_typst_body

    body = tree_to_typst_body(_figure_tree())
    assert "#figure" not in body
    assert body.startswith("#align(center,")


def test_captioned_figure_is_pinned_to_its_place_in_the_text():
    from adib_engine.render.typst.document import tree_to_typst_body

    tree = _figure_tree()
    tree.nodes[0].assets[0].caption = "Diagram one"
    body = tree_to_typst_body(tree)
    assert "#figure" in body
    assert "placement: none" in body


def test_multi_image_figure_compiles_side_by_side(tmp_path: Path, assets_dir: Path):
    """The grid markup for a row of pictures has to be valid Typst, not just plausible."""
    (assets_dir / "img2.png").write_bytes(_tiny_png())
    tree = _figure_tree()
    tree.assets["img2"] = Asset(id="img2", path="img2.png", mime="image/png")
    tree.nodes[0].assets.append(AssetRef(asset_id="img2"))

    out = _compile(
        tree, _preset(), target_lang="en", assets_dir=assets_dir, out_path=tmp_path / "book.pdf"
    )
    assert out.exists()


def test_missing_typst_binary_explains_how_to_get_one(monkeypatch, tmp_path: Path):
    """The raw FileNotFoundError this replaces read only "[Errno 2] ... 'typst'"."""
    from adib_engine.render.typst import compile as compile_mod

    monkeypatch.setattr(compile_mod.shutil, "which", lambda _name: None)
    monkeypatch.setattr(compile_mod.sys, "executable", str(tmp_path / "python"))

    with pytest.raises(compile_mod.TypstNotFoundError) as exc_info:
        compile_mod.resolve_typst_bin(tmp_path / "nope" / "typst")
    assert "prepare-binaries.sh" in str(exc_info.value)


def test_explicit_typst_path_is_preferred_over_path_lookup():
    from adib_engine.render.typst.compile import resolve_typst_bin

    assert resolve_typst_bin(TYPST_BIN) == str(TYPST_BIN)


def test_source_span_page_numbers_do_not_leak_into_typst_markup():
    """SourceSpan is provenance metadata, not something that should print."""
    from adib_engine.render.typst.document import tree_to_typst_body

    tree = DocTree(
        nodes=[
            DocNode(
                id=make_node_id(NodeKind.PARAGRAPH, "p", 0),
                kind=NodeKind.PARAGRAPH,
                text="Body text.",
                source=SourceSpan(page_start=42, page_end=42),
            )
        ]
    )
    body = tree_to_typst_body(tree)
    assert "42" not in body
