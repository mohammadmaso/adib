"""Preset -> Typst preamble.

Every typographic decision in `Preset.typography` becomes one `#set`/`#show`
rule here. This is the module that turns "Persian, formal register, A5 trade
book" into `binding: right`, mirrored margins, Vazirmatn at 11pt with 1.75
leading, and Persian-Indic digits — the whole reason a preset carries typography
alongside translation instructions.
"""

from __future__ import annotations

from adib_engine.models.preset import DigitStyle, PageSize, Typography
from adib_engine.models.project import TextDirection, direction_for

_PAGE_SIZE_TYST = {
    PageSize.A4: "a4",
    PageSize.A5: "a5",
    PageSize.B5: "b5",
    PageSize.US_LETTER: "us-letter",
    # Typst has no built-in "trade" paper; express it as explicit dimensions.
    PageSize.TRADE: "(6in, 9in)",
}

_PERSIAN_DIGITS = "۰۱۲۳۴۵۶۷۸۹"
_ARABIC_DIGITS = "٠١٢٣٤٥٦٧٨٩"

_DIGIT_TABLES = {
    DigitStyle.PERSIAN: _PERSIAN_DIGITS,
    DigitStyle.ARABIC: _ARABIC_DIGITS,
}


def _digit_show_rule(style: DigitStyle) -> str:
    table = _DIGIT_TABLES.get(style)
    if table is None:
        return ""
    glyphs = ", ".join(f'"{d}"' for d in table)
    return (
        "#let __digits = (" + glyphs + ")\n"
        '#show regex("[0-9]"): it => __digits.at(int(it.text))\n'
    )


def build_theme(
    typo: Typography,
    *,
    target_lang: str,
    heading_font: str | None = None,
) -> str:
    """Render the `#set`/`#show` preamble for one book's typography."""
    direction = direction_for(target_lang)
    is_rtl = direction is TextDirection.RTL
    page_size = _PAGE_SIZE_TYST.get(typo.page_size, "a5")

    inside = typo.margin_inner_mm
    outside = typo.margin_outer_mm
    lines: list[str] = []

    # `paper:` only accepts a named string; PageSize.TRADE has no Typst preset,
    # so it's expressed as explicit width/height instead.
    if page_size.startswith("("):
        w, h = page_size.strip("()").split(",")
        lines.append(f"#set page(width: {w.strip()}, height: {h.strip()})")
    else:
        lines.append(f'#set page(paper: "{page_size}")')

    binding = "right" if is_rtl else "left"
    numbering = '"1"' if typo.show_page_numbers else "none"
    lines.append(
        f"#set page(binding: {binding}, "
        f"margin: (inside: {inside}mm, outside: {outside}mm, "
        f"top: {typo.margin_top_mm}mm, bottom: {typo.margin_bottom_mm}mm), "
        f"numbering: {numbering})"
    )

    body_font = typo.body_font
    lines.append(
        "#set text("
        f'dir: {"rtl" if is_rtl else "ltr"}, '
        f'lang: "{target_lang.split("-")[0]}", '
        f'font: "{body_font}", '
        f"size: {typo.body_size_pt}pt"
        ")"
    )
    lines.append(
        f"#set par(justify: {str(typo.justify).lower()}, "
        f"leading: {typo.leading}em, "
        f"first-line-indent: {typo.paragraph_indent_em}em)"
    )
    lines.append(f"#set block(spacing: {typo.paragraph_spacing_em}em)")

    hfont = heading_font or typo.heading_font or body_font
    lines.append(f'#show heading: set text(font: "{hfont}")')
    for level, scale in enumerate(typo.heading_scale, start=1):
        size = typo.body_size_pt * scale
        lines.append(f"#show heading.where(level: {level}): set text(size: {size}pt)")

    # LTR islands (URLs, code, foreign technical terms) inside an RTL page must
    # not have their internal character order mirrored by the paragraph's bidi
    # algorithm; force them back to ltr and the Latin fallback font explicitly.
    lines.append(
        f'#show raw: set text(font: "{typo.mono_font}", dir: ltr)'
    )

    digit_rule = _digit_show_rule(typo.digit_style)
    if digit_rule:
        lines.append(digit_rule.rstrip())

    if not typo.show_running_heads:
        lines.append("#set page(header: none)")

    return "\n".join(line for line in lines if line) + "\n"


__all__ = ["build_theme"]
