"""Domain models — the single source of truth for the API contract.

The TypeScript client is generated from these via scripts/generate-types.sh;
nothing in the frontend hand-writes a request or response type.
"""

from adib_engine.models.analysis import BookAnalysis
from adib_engine.models.document import (
    Asset,
    AssetRef,
    BookMeta,
    DocNode,
    DocTree,
    NodeKind,
    SourceSpan,
    TableCell,
    TableData,
    make_node_id,
)
from adib_engine.models.glossary import (
    GlossaryCandidate,
    GlossaryTerm,
    GlossaryTermUpdate,
    TermPolicy,
)
from adib_engine.models.preset import (
    DigitStyle,
    FootnotePolicy,
    PageSize,
    Preset,
    Register,
    StyleDelta,
    Typography,
)
from adib_engine.models.project import (
    DocumentRecord,
    ProjectCreate,
    ProjectMeta,
    ProjectStage,
    ProjectSummary,
    ProviderSettings,
    Run,
    RunStatus,
    TextDirection,
    UsageEvent,
    direction_for,
)
from adib_engine.models.segment import (
    QAFlag,
    QASeverity,
    Segment,
    SegmentStatus,
    SegmentUpdate,
)

__all__ = [
    "Asset",
    "AssetRef",
    "BookAnalysis",
    "BookMeta",
    "DigitStyle",
    "DocNode",
    "DocTree",
    "DocumentRecord",
    "FootnotePolicy",
    "GlossaryCandidate",
    "GlossaryTerm",
    "GlossaryTermUpdate",
    "NodeKind",
    "PageSize",
    "Preset",
    "ProjectCreate",
    "ProjectMeta",
    "ProjectStage",
    "ProjectSummary",
    "ProviderSettings",
    "QAFlag",
    "QASeverity",
    "Register",
    "Run",
    "RunStatus",
    "Segment",
    "SegmentStatus",
    "SegmentUpdate",
    "SourceSpan",
    "StyleDelta",
    "TableCell",
    "TableData",
    "TermPolicy",
    "TextDirection",
    "Typography",
    "UsageEvent",
    "direction_for",
    "make_node_id",
]
