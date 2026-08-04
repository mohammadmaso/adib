"""Pydantic AI agents: analysis, glossary adjudication, and translation.

All three share the model wiring in `base`; each returns structured/plain output
plus the pydantic-ai usage so the caller can persist cost per run.
"""

from adib_engine.agents.analyst import (
    ANALYST_SYSTEM_PROMPT,
    analyze_book,
    analyze_book_with_model,
    build_analyst_agent,
)
from adib_engine.agents.base import (
    AgentResult,
    build_model,
    cost_for,
    model_settings,
    result_from_run,
)
from adib_engine.agents.glossary import (
    GlossaryDecision,
    GlossaryVerdict,
    adjudicate_glossary,
    build_glossary_agent,
    decisions_to_terms,
)
from adib_engine.agents.translate import (
    TranslatedSegment,
    protect_spans,
    restore_spans,
    translate_book,
    translate_segment,
)

__all__ = [
    "ANALYST_SYSTEM_PROMPT",
    "AgentResult",
    "GlossaryDecision",
    "GlossaryVerdict",
    "TranslatedSegment",
    "adjudicate_glossary",
    "analyze_book",
    "analyze_book_with_model",
    "build_analyst_agent",
    "build_glossary_agent",
    "build_model",
    "cost_for",
    "decisions_to_terms",
    "model_settings",
    "protect_spans",
    "restore_spans",
    "result_from_run",
    "translate_book",
    "translate_segment",
]
