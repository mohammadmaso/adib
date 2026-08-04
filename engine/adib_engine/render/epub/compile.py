"""Orchestrates one EPUB render: DocTree + Preset -> book.epub via ebooklib.

Chapters, CSS, and fonts are assembled into an `EpubBook` and written with
`ebooklib.epub.write_epub`, which is the actual EPUB3 packager (zip layout,
OPF, nav document) — this module's job is only to decide what goes in.
"""

from __future__ import annotations

from pathlib import Path

from ebooklib import epub

from adib_engine.models.document import DocTree
from adib_engine.models.preset import Preset
from adib_engine.models.project import TextDirection, direction_for
from adib_engine.render.epub.document import chapter_to_xhtml_body, split_into_chapters
from adib_engine.render.epub.theme import build_css

#: CSS family name -> the font file bundled under resources/fonts/. Only the
#: family actually referenced by the preset's typography is embedded, so a
#: book that only uses Vazirmatn doesn't carry Noto Naskh Arabic's weight too.
_FONT_FILES = {
    "Vazirmatn": "Vazirmatn-Regular.ttf",
    "Noto Naskh Arabic": "NotoNaskhArabic-Regular.ttf",
    "Noto Serif": "NotoSerif-Regular.ttf",
    "JetBrains Mono": "JetBrainsMono-Regular.ttf",
}

_MIME_BY_SUFFIX = {
    ".ttf": "font/ttf",
    ".otf": "font/otf",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
}


def _referenced_fonts(preset: Preset) -> dict[str, str]:
    typo = preset.typography
    families = {typo.body_font, typo.mono_font, typo.latin_font}
    if typo.heading_font:
        families.add(typo.heading_font)
    return {f: _FONT_FILES[f] for f in families if f in _FONT_FILES}


def compile_epub(
    tree: DocTree,
    preset: Preset,
    *,
    target_lang: str,
    assets_dir: Path,
    out_path: Path,
    fonts_dir: Path | None = None,
) -> Path:
    """Render `tree` to an EPUB3 file at `out_path`.

    `assets_dir` must be the directory holding the tree's staged images (see
    `ingest.kit.make_asset_stager`).
    """
    direction = direction_for(target_lang)
    is_rtl = direction is TextDirection.RTL

    book = epub.EpubBook()
    book.set_identifier(f"adib-{abs(hash(out_path)) & 0xFFFFFFFF:x}")
    book.set_title(tree.meta.title or "Untitled")
    book.set_language(target_lang.split("-")[0])
    book.set_direction("rtl" if is_rtl else "ltr")
    for author in tree.meta.authors:
        book.add_author(author)

    fonts = _referenced_fonts(preset)
    css_text = build_css(preset.typography, target_lang=target_lang, font_files=fonts)
    css_item = epub.EpubItem(
        uid="style_main", file_name="style/main.css", media_type="text/css", content=css_text
    )
    book.add_item(css_item)

    if fonts_dir is not None:
        for filename in fonts.values():
            src = fonts_dir / filename
            if not src.exists():
                continue
            mime = _MIME_BY_SUFFIX.get(src.suffix.lower(), "application/octet-stream")
            book.add_item(
                epub.EpubItem(
                    uid=f"font_{filename}",
                    file_name=f"fonts/{filename}",
                    media_type=mime,
                    content=src.read_bytes(),
                )
            )

    for asset in tree.assets.values():
        src = assets_dir / asset.path
        if not src.exists():
            continue
        book.add_item(
            epub.EpubItem(
                uid=f"asset_{asset.id}",
                file_name=f"assets/{asset.path}",
                media_type=asset.mime,
                content=src.read_bytes(),
            )
        )

    chapters = split_into_chapters(tree)
    spine: list = ["nav"]
    toc: list = []
    for i, chapter in enumerate(chapters):
        body = chapter_to_xhtml_body(chapter, tree)
        html_item = epub.EpubHtml(
            uid=f"chap_{i}",
            file_name=f"chapters/chap_{i}.xhtml",
            title=chapter.title,
            lang=target_lang.split("-")[0],
            direction="rtl" if is_rtl else "ltr",
            # ebooklib's HTML parser rejects a `str` carrying an XML encoding
            # declaration; bytes sidesteps that lxml restriction entirely.
            content=_wrap_xhtml(chapter.title, body, is_rtl).encode("utf-8"),
        )
        html_item.add_link(href="../style/main.css", rel="stylesheet", type="text/css")
        book.add_item(html_item)
        spine.append(html_item)
        toc.append(html_item)

    book.toc = toc
    book.add_item(epub.EpubNcx())
    nav = epub.EpubNav()
    book.add_item(nav)
    book.spine = spine

    out_path.parent.mkdir(parents=True, exist_ok=True)
    epub.write_epub(str(out_path), book)
    return out_path


def _wrap_xhtml(title: str, body: str, is_rtl: bool) -> str:
    dir_attr = "rtl" if is_rtl else "ltr"
    return f"""<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" \
dir="{dir_attr}">
<head><title>{title}</title></head>
<body>
{body}
</body>
</html>"""


__all__ = ["compile_epub"]
