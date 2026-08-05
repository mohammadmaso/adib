"""Project: one book being translated, persisted as a single `.adib` SQLite file.

Everything needed to resume a run lives in that one file — the document tree, the
segments and their translations, the glossary, the approved preset, and the cost
log. Copying the file (plus its asset directory) moves the whole job.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel
from sqlalchemy import Column
from sqlalchemy import Enum as SAEnum
from sqlmodel import JSON, Field, SQLModel

from adib_engine.models._sqltypes import UTCDateTime


class ProjectStage(StrEnum):
    """Where the pipeline is. Also drives which screen the UI opens on."""

    CREATED = "created"
    INGESTING = "ingesting"
    #: Gate 1 — the user fixes the heading tree.
    STRUCTURE_REVIEW = "structure_review"
    ANALYZING = "analyzing"
    #: Gate 2 — the user approves tone, preset, and glossary.
    STYLE_REVIEW = "style_review"
    TRANSLATING = "translating"
    #: The user paused a translation run; segments already done are kept, the
    #: rest stay pending until `/translate` is called again.
    PAUSED = "paused"
    #: Gate 3 — side-by-side review before export.
    REVIEW = "review"
    EXPORTING = "exporting"
    DONE = "done"
    FAILED = "failed"


class RunStatus(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TextDirection(StrEnum):
    LTR = "ltr"
    RTL = "rtl"


#: Languages whose script runs right-to-left. Drives page mirroring, EPUB spine
#: direction, and the Typst text direction.
RTL_LANGUAGES = frozenset({"fa", "ar", "he", "ur", "ps", "sd", "ckb", "yi", "dv"})


def direction_for(lang: str | None) -> TextDirection:
    """Text direction for a BCP-47 tag, matching on the primary subtag."""
    if not lang:
        return TextDirection.LTR
    primary = lang.replace("_", "-").split("-")[0].lower()
    return TextDirection.RTL if primary in RTL_LANGUAGES else TextDirection.LTR


def _utcnow() -> datetime:
    return datetime.now(UTC)


class ProviderSettings(BaseModel):
    """OpenAI-compatible endpoint config.

    The API key is deliberately absent: it lives in the OS keychain and is
    injected at call time, so it never reaches a project file or a log.
    """

    base_url: str = "https://api.openai.com/v1"
    model: str = "gpt-4o-mini"
    temperature: float = 0.3
    #: Parallel in-flight translation requests.
    concurrency: int = 4
    max_retries: int = 5
    request_timeout_s: float = 180.0
    #: Abort the run if accumulated cost exceeds this. None disables the cap.
    max_spend_usd: float | None = None
    #: Per-million-token prices, used for the cost estimate and the live meter.
    #: Endpoint-specific, so the user sets them alongside the model.
    price_per_mtok_in: float = 0.0
    price_per_mtok_out: float = 0.0


class ImageProviderSettings(BaseModel):
    """Multimodal chat-completions endpoint config, for cover translation.

    Image-editing models are almost always served through `/chat/completions`
    with `modalities: ["image", "text"]` rather than a dedicated images API —
    that's how aggregators like OpenRouter expose them — so this is the same
    "OpenAI-compatible endpoint" shape as `ProviderSettings`. The API key lives
    in the OS keychain (a separate slot from the text provider's) and is
    injected at call time, never persisted here.
    """

    base_url: str = "https://openrouter.ai/api/v1"
    model: str = "google/gemini-2.5-flash-image-preview"
    max_retries: int = 3
    request_timeout_s: float = 180.0
    #: Flat per-image price, used for the cost estimate — image APIs typically
    #: bill per generated image rather than per token.
    price_per_image_usd: float = 0.0


class ProjectMeta(SQLModel, table=True):
    """Single-row table holding the project's identity and current state."""

    #: Always 1. A project file describes exactly one book.
    id: int = Field(default=1, primary_key=True)
    name: str
    #: Absolute path to the file the user imported. Informational after ingest.
    source_path: str
    source_format: str = ""
    source_lang: str | None = None
    target_lang: str = "fa"

    stage: ProjectStage = Field(
        default=ProjectStage.CREATED,
        sa_column=Column(SAEnum(ProjectStage, native_enum=False)),
    )
    #: Which stage was running when it failed, e.g. `analyzing` or `translating`.
    #: `stage` itself just says `failed`, which is not enough for the UI to know
    #: which gate to send the user back to or what to offer retrying.
    failed_stage: ProjectStage | None = Field(
        default=None,
        sa_column=Column(SAEnum(ProjectStage, native_enum=False), nullable=True),
    )
    #: Human-readable reason for the last failure, shown by the UI next to the retry action.
    failed_reason: str | None = None
    #: Resolved preset (built-in + approved style delta), stored as JSON so the
    #: run is reproducible even if the preset library later changes.
    preset: dict | None = Field(default=None, sa_column=Column(JSON))
    analysis: dict | None = Field(default=None, sa_column=Column(JSON))
    #: Serialized `Asset` (id/path/mime/dims/sha256) for the target-language
    #: cover, once cover translation has run. The PNG itself lives in
    #: `assets_dir` like any other staged asset; this is not tied to either
    #: the source or target `DocTree` since it's produced independently of a
    #: translation run and both trees get rebuilt/copied around it.
    translated_cover_asset: dict | None = Field(default=None, sa_column=Column(JSON))

    created_at: datetime = Field(default_factory=_utcnow, sa_column=Column(UTCDateTime))
    updated_at: datetime = Field(default_factory=_utcnow, sa_column=Column(UTCDateTime))

    @property
    def target_direction(self) -> TextDirection:
        return direction_for(self.target_lang)


class DocumentRecord(SQLModel, table=True):
    """A serialized DocTree.

    The tree is stored whole rather than shredded into rows: it is read and
    written as a unit by ingest and by the renderers, and only the segments need
    row-level updates.
    """

    #: "source" or "target".
    id: str = Field(primary_key=True)
    tree: dict = Field(sa_column=Column(JSON))
    updated_at: datetime = Field(default_factory=_utcnow, sa_column=Column(UTCDateTime))


class Run(SQLModel, table=True):
    """One execution of a pipeline stage, for history and cost attribution."""

    id: str = Field(primary_key=True)
    stage: ProjectStage = Field(sa_column=Column(SAEnum(ProjectStage, native_enum=False)))
    status: RunStatus = Field(
        default=RunStatus.RUNNING,
        sa_column=Column(SAEnum(RunStatus, native_enum=False)),
    )
    started_at: datetime = Field(default_factory=_utcnow, sa_column=Column(UTCDateTime))
    finished_at: datetime | None = Field(default=None, sa_column=Column(UTCDateTime, nullable=True))
    error: str | None = None
    #: Stage-specific counters: segments done, terms mined, pages rendered.
    stats: dict = Field(default_factory=dict, sa_column=Column(JSON))


class UsageEvent(SQLModel, table=True):
    """One model call. Summed for the live cost meter and the spend cap."""

    id: str = Field(primary_key=True)
    run_id: str | None = Field(default=None, index=True)
    segment_id: str | None = Field(default=None, index=True)
    #: "analysis", "glossary", "translation", "qa".
    purpose: str = ""
    model: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    created_at: datetime = Field(default_factory=_utcnow, sa_column=Column(UTCDateTime))


class ProjectSummary(BaseModel):
    """What the Library screen shows per project, without opening the full file."""

    path: str
    name: str
    source_lang: str | None
    target_lang: str
    stage: ProjectStage
    failed_stage: ProjectStage | None = None
    segments_total: int = 0
    segments_done: int = 0
    cost_usd: float = 0.0
    updated_at: datetime
    #: Set when the file could not be read, so the Library can show it greyed out
    #: rather than failing the whole listing.
    error: str | None = None


class ProjectCreate(BaseModel):
    name: str
    source_path: str
    target_lang: str = "fa"
    preset_id: str | None = None


class ProjectCreated(BaseModel):
    """Response for `POST /projects`.

    `ProjectMeta` describes the file's *contents*, not its own filename, so the
    id the rest of the API expects (the `.adib` stem) has to be surfaced
    separately here.
    """

    project_id: str
    meta: ProjectMeta
