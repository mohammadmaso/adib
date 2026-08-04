"""Glossary: the mechanism that keeps terminology stable across a whole book.

Built before translation starts and injected into every translation call, so
"backpropagation" renders the same way in chapter 12 as in chapter 2. Each term
also carries an LLM-authored explanation *written in the target language*, which
becomes the footnote or appendix entry.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel
from sqlalchemy import Column
from sqlalchemy import Enum as SAEnum
from sqlmodel import Field, SQLModel

from adib_engine.models._sqltypes import UTCDateTime


class TermPolicy(StrEnum):
    """What the renderer does with a term. Set per term by the user in Gate 2."""

    #: Leave the source form untouched, e.g. "TCP". Protected from the translator
    #: by placeholder substitution.
    KEEP = "keep"
    #: Translate normally, using `target` for consistency.
    TRANSLATE = "translate"
    #: Translated form with the original in parentheses on every occurrence.
    TRANSLATE_PAREN = "translate_paren"
    #: Translated form, plus a footnote carrying `explanation` on first use in
    #: each chapter.
    FOOTNOTE = "footnote"
    #: Translated form, with the explanation collected into the end-of-book
    #: glossary appendix and linked from the first occurrence.
    APPENDIX = "appendix"


def _utcnow() -> datetime:
    return datetime.now(UTC)


class GlossaryTermBase(SQLModel):
    #: The term as it appears in the source. Unique within a project.
    source: str = Field(index=True, unique=True)
    target: str | None = None
    policy: TermPolicy = Field(
        default=TermPolicy.TRANSLATE,
        sa_column=Column(SAEnum(TermPolicy, native_enum=False)),
    )
    #: 1-3 sentences in the TARGET language. This is the footnote body.
    explanation: str | None = None
    part_of_speech: str | None = None
    #: Node where the term first appears, used to place the first-use footnote.
    first_seen_node: str | None = None
    frequency: int = 0
    #: User-edited terms are never overwritten by a re-run of the glossary agent.
    locked: bool = False
    #: False for mined candidates the user or the agent rejected. Kept rather
    #: than deleted so a re-run does not resurface them.
    enabled: bool = True
    #: "mined" (statistical) or "llm" (adjudicated) or "user".
    origin: str = "mined"
    updated_at: datetime = Field(default_factory=_utcnow, sa_column=Column(UTCDateTime))


class GlossaryTerm(GlossaryTermBase, table=True):
    id: str = Field(primary_key=True)


class GlossaryTermUpdate(BaseModel):
    target: str | None = None
    policy: TermPolicy | None = None
    explanation: str | None = None
    locked: bool | None = None
    enabled: bool | None = None


class GlossaryCandidate(BaseModel):
    """Output of statistical mining, before the LLM decides whether to keep it."""

    source: str
    frequency: int
    first_seen_node: str | None = None
    #: Why the miner surfaced it: "acronym", "capitalized_phrase", "code_span", ...
    reason: str = ""
    #: A sentence containing the term, given to the adjudicating agent as context.
    sample: str | None = None
