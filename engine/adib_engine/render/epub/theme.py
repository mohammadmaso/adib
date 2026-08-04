"""Preset -> EPUB CSS.

Mirrors `render/typst/theme.py`'s job for the PDF renderer: every field on
`Typography` becomes one CSS rule. Reading systems (Apple Books, Calibre,
Thorium) all respect `dir`/`writing-mode` and `@font-face`, so RTL layout here
is standard CSS rather than anything EPUB-specific.
"""

from __future__ import annotations

from adib_engine.models.preset import Typography
from adib_engine.models.project import TextDirection, direction_for


def build_css(typo: Typography, *, target_lang: str, font_files: dict[str, str]) -> str:
    """Render the book-wide stylesheet.

    `font_files` maps a CSS font-family name to the filename it should be
    declared with in `@font-face` (the manifest embeds the same files).
    """
    direction = direction_for(target_lang)
    is_rtl = direction is TextDirection.RTL

    faces = "\n".join(
        f'@font-face {{ font-family: "{family}"; src: url("fonts/{filename}"); }}'
        for family, filename in font_files.items()
    )

    body_dir = "rtl" if is_rtl else "ltr"
    margin_start = typo.margin_inner_mm if is_rtl else typo.margin_outer_mm
    margin_end = typo.margin_outer_mm if is_rtl else typo.margin_inner_mm

    return f"""{faces}

html {{
  direction: {body_dir};
}}

body {{
  font-family: "{typo.body_font}", serif;
  font-size: {typo.body_size_pt}pt;
  line-height: {typo.leading};
  direction: {body_dir};
  margin: {typo.margin_top_mm}mm {margin_end}mm {typo.margin_bottom_mm}mm {margin_start}mm;
  text-align: {"justify" if typo.justify else "start"};
}}

p {{
  margin: 0 0 {typo.paragraph_spacing_em}em 0;
  text-indent: {typo.paragraph_indent_em}em;
}}

h1, h2, h3, h4, h5, h6 {{
  font-family: "{typo.heading_font or typo.body_font}", serif;
  direction: {body_dir};
}}

{_heading_sizes(typo)}

code, pre {{
  font-family: "{typo.mono_font}", monospace;
  direction: ltr;
  unicode-bidi: isolate;
}}

a {{
  direction: ltr;
  unicode-bidi: isolate;
}}

table {{
  width: 100%;
  border-collapse: collapse;
  direction: {body_dir};
}}

th, td {{
  border: 1px solid currentColor;
  padding: 0.4em 0.6em;
  text-align: start;
}}

th {{
  font-weight: bold;
}}

figure {{
  margin: 1em 0;
  text-align: center;
}}

figcaption {{
  font-size: 0.9em;
  font-style: italic;
}}

blockquote {{
  margin-inline-start: 1.5em;
  border-inline-start: 3px solid currentColor;
  padding-inline-start: 1em;
}}
"""


def _heading_sizes(typo: Typography) -> str:
    lines = []
    for level, scale in enumerate(typo.heading_scale, start=1):
        lines.append(f"h{level} {{ font-size: {typo.body_size_pt * scale}pt; }}")
    return "\n".join(lines)


__all__ = ["build_css"]
