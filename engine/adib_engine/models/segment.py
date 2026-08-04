"""Segments: the unit of translation, of resumption, and of review.

One row per translatable piece of the book. Each is committed to SQLite the
moment it comes back from the model, which is what makes a 400-page run
crash-resumable and lets Gate 3 retranslate a single paragraph.

Tables are one segment per table (serialized to a markdown pipe table), not one
per cell: cell-level segments lose the surrounding row context a translator needs
to pick consistent column headers, and the row/column count is cheap to verify
afterwards in the QA pass.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel
from sqlalchemy import Column
from sqlalchemy import Enum as SAEnum
from sqlmodel import JSON, Field, SQLModel

from adib_engine.models._sqltypes import UTCDateTime
from adib_engine.models.document import NodeKind


class SegmentStatus(StrEnum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    TRANSLATED = "translated"
    #: Translated, but the QA pass raised something worth a human look.
    FLAGGED = "flagged"
    #: Explicitly signed off in Gate 3.
    APPROVED = "approved"
    FAILED = "failed"
    #: Deliberately not translated (protected term, code block, user choice).
    SKIPPED = "skipped"


class QASeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class QAFlag(BaseModel):
    """A finding from the deterministic consistency pass."""

    rule: str
    severity: QASeverity
    message: str
    #: Rule-specific context: the term, the expected vs actual ratio, etc.
    detail: dict[str, object] = {}


def _utcnow() -> datetime:
    return datetime.now(UTC)


class SegmentBase(SQLModel):
    node_id: str = Field(index=True)
    #: Reading-order position. Named `ordinal` because `order` is reserved in SQL.
    ordinal: int = Field(index=True)
    kind: NodeKind = Field(sa_column=Column(SAEnum(NodeKind, native_enum=False)))
    source_text: str
    target_text: str | None = None
    status: SegmentStatus = Field(
        default=SegmentStatus.PENDING,
        sa_column=Column(SAEnum(SegmentStatus, native_enum=False), index=True),
    )
    #: Heading titles above this segment, outermost first. Given to the
    #: translator so terminology stays stable within a chapter.
    heading_path: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    qa_flags: list[dict] = Field(default_factory=list, sa_column=Column(JSON))

    #: Set when a human edits the translation in Gate 3. Retranslation and
    #: re-runs must never overwrite a locked segment.
    locked: bool = False
    note: str | None = None

    model_name: str | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    attempts: int = 0
    error: str | None = None
    updated_at: datetime = Field(default_factory=_utcnow, sa_column=Column(UTCDateTime))


class Segment(SegmentBase, table=True):
    id: str = Field(primary_key=True)

    def flags(self) -> list[QAFlag]:
        return [QAFlag.model_validate(f) for f in self.qa_flags]

    @property
    def needs_translation(self) -> bool:
        if self.locked:
            return False
        return self.status in (SegmentStatus.PENDING, SegmentStatus.FAILED)


class SegmentUpdate(BaseModel):
    """Gate 3 edits. Every field optional; only what is sent gets written."""

    target_text: str | None = None
    status: SegmentStatus | None = None
    locked: bool | None = None
    note: str | None = None
