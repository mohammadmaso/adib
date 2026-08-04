"""Output of the analysis agent — the Gate 2 proposal the user approves or edits.

Structured rather than prose so the UI can render each decision as its own
editable control, and so `style_guide` can be injected into every translation
call without re-parsing anything.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from adib_engine.models.preset import Register, StyleDelta


class BookAnalysis(BaseModel):
    #: BCP-47. Confirms or corrects what ingest guessed.
    detected_source_lang: str
    genre: str
    #: Prose description of voice, e.g. "formal-academic, third person, dry
    #: humor in asides". Shown to the user and fed to the translator.
    tone: str
    language_register: Register
    audience: str

    #: Id of the closest built-in preset.
    suggested_preset: str
    #: Book-specific tuning on top of that preset.
    style_delta: StyleDelta = Field(default_factory=StyleDelta)

    #: Instructions injected into every translation call. The single most
    #: important field for output quality.
    style_guide: str
    #: Translation hazards found while sampling: puns, verse, culture-bound
    #: references, heavy code, inconsistent source terminology.
    reader_notes: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0, default=0.5)
