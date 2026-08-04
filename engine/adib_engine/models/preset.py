"""Translation presets: the "professional skill" the app applies to a book.

A preset bundles the translator's instructions with the typography of the
finished book, because those two decisions are not independent — an academic
paper and a children's book differ in register *and* in leading, margins, and
type size.

Built-ins ship as YAML in adib_engine/presets/. User presets live in the app data
directory and shadow built-ins of the same id.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

from adib_engine.models.glossary import TermPolicy


class Register(StrEnum):
    FORMAL = "formal"
    NEUTRAL = "neutral"
    COLLOQUIAL = "colloquial"
    LITERARY = "literary"
    TECHNICAL = "technical"


class DigitStyle(StrEnum):
    """Which digit glyphs the rendered book uses."""

    LATIN = "latin"
    #: ۰۱۲۳۴۵۶۷۸۹ — expected in Persian trade books.
    PERSIAN = "persian"
    #: ٠١٢٣٤٥٦٧٨٩ — Arabic-Indic.
    ARABIC = "arabic"


class PageSize(StrEnum):
    A4 = "a4"
    A5 = "a5"
    B5 = "b5"
    US_LETTER = "us-letter"
    #: 6x9in, the common trade paperback.
    TRADE = "trade"


class Typography(BaseModel):
    """Everything the renderers need to lay out the target book."""

    page_size: PageSize = PageSize.A5
    #: Margins in millimetres. `inner` is the binding edge, mirrored automatically
    #: for RTL books.
    margin_inner_mm: float = 22.0
    margin_outer_mm: float = 16.0
    margin_top_mm: float = 20.0
    margin_bottom_mm: float = 20.0

    body_font: str = "Vazirmatn"
    heading_font: str | None = None
    mono_font: str = "JetBrains Mono"
    #: Font used for Latin-script runs inside RTL text, so technical terms and
    #: URLs do not fall back to an arbitrary system face.
    latin_font: str = "Noto Serif"

    body_size_pt: float = 11.0
    #: Line height as a multiple of font size. RTL scripts want more than Latin:
    #: Persian and Arabic have deep descenders and stacked diacritics.
    leading: float = 1.75
    #: Multiplier applied per heading level, largest first.
    heading_scale: list[float] = Field(default_factory=lambda: [2.0, 1.6, 1.3, 1.15, 1.0, 1.0])
    paragraph_indent_em: float = 0.0
    paragraph_spacing_em: float = 0.65

    justify: bool = True
    hyphenate: bool = False
    digit_style: DigitStyle = DigitStyle.LATIN

    show_page_numbers: bool = True
    show_running_heads: bool = True


class FootnotePolicy(BaseModel):
    #: Emit a footnote only on a term's first use within each chapter, rather
    #: than every occurrence, which would drown the page.
    first_use_per_chapter: bool = True
    #: Also collect every footnoted term into the end-of-book appendix.
    build_appendix: bool = True
    #: Link occurrences to the appendix entry in EPUB output.
    link_to_appendix: bool = True


class Preset(BaseModel):
    id: str
    name: str
    description: str = ""
    #: Injected verbatim as the translation agent's instructions.
    system_prompt: str
    language_register: Register = Register.NEUTRAL
    #: Applied to mined terms that the glossary agent does not override.
    default_glossary_policy: TermPolicy = TermPolicy.TRANSLATE
    footnotes: FootnotePolicy = Field(default_factory=FootnotePolicy)
    typography: Typography = Field(default_factory=Typography)
    #: Rule ids from the QA pass to enable beyond the always-on set.
    qa_rules: list[str] = Field(default_factory=list)
    #: True for presets shipped with the app; user copies are False and editable.
    builtin: bool = False


class StyleDelta(BaseModel):
    """Book-specific overrides the analysis agent proposes on top of a preset.

    Kept separate from the preset so the user can see exactly what the model
    wants to change, accept it, and still have the preset stay pristine for the
    next book.
    """

    language_register: Register | None = None
    #: Appended to the preset's system prompt, not replacing it.
    extra_instructions: str | None = None
    default_glossary_policy: TermPolicy | None = None
    typography_overrides: dict[str, object] = Field(default_factory=dict)

    def apply(self, preset: Preset) -> Preset:
        """Return a new preset with this delta applied. Never mutates `preset`."""
        merged = preset.model_copy(deep=True)
        if self.language_register is not None:
            merged.language_register = self.language_register
        if self.default_glossary_policy is not None:
            merged.default_glossary_policy = self.default_glossary_policy
        if self.extra_instructions:
            merged.system_prompt = f"{merged.system_prompt.rstrip()}\n\n{self.extra_instructions}"
        if self.typography_overrides:
            merged.typography = Typography.model_validate(
                merged.typography.model_dump() | self.typography_overrides
            )
        # A tuned preset is no longer the shipped one.
        merged.builtin = False
        return merged
