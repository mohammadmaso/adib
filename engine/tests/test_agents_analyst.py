"""Analyst agent tests — deterministic via `analyze_book_with_model`.

The analyst detects language/tone/register and proposes a preset + style delta.
The model seam (`analyze_book_with_model`) lets us run the exact same prompt
builder against a `FunctionModel` that returns a canned `BookAnalysis`.
"""

from __future__ import annotations

import asyncio

from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from adib_engine.agents.analyst import analyze_book_with_model
from adib_engine.agents.context import stratified_sample
from adib_engine.models.analysis import BookAnalysis
from adib_engine.models.project import ProviderSettings
from tests.fixtures import simple_book

PROVIDER = ProviderSettings(base_url="http://test", model="test")


def _analysis_fn() -> FunctionModel:
    def fn(messages: list[ModelMessage], info) -> ModelResponse:
        return ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name="final_result",
                    args={
                        "detected_source_lang": "en",
                        "genre": "technical-nonfiction",
                        "tone": "direct, explanatory",
                        "language_register": "technical",
                        "audience": "engineers",
                        "suggested_preset": "technical-manual",
                        "style_delta": {"extra_instructions": "Keep sentences short."},
                        "style_guide": (
                            "Use standard Persian networking terms; keep acronyms as-is."
                        ),
                        "reader_notes": ["TCP/IP is used inconsistently in the source."],
                        "confidence": 0.8,
                    },
                    tool_call_id="tc1",
                )
            ]
        )

    return FunctionModel(fn)


def test_stratified_sample_reaches_front_middle_and_end():
    tree = simple_book()
    sample = stratified_sample(tree, max_chars=20_000)
    # Simple fixture has two H1s (Introduction, Conclusion) and mid-prose.
    assert "networks" in sample or "machines" in sample
    assert len(sample) > 0


def test_stratified_sample_respects_budget():
    tree = simple_book()
    sample = stratified_sample(tree, max_chars=50)
    assert len(sample) <= 50


def test_analyze_book_returns_structured_analysis():
    tree = simple_book()
    result = asyncio.run(
        analyze_book_with_model(tree, _analysis_fn(), PROVIDER)
    )
    analysis = result.output
    assert isinstance(analysis, BookAnalysis)
    assert analysis.detected_source_lang == "en"
    assert analysis.language_register.value == "technical"
    assert analysis.suggested_preset == "technical-manual"
    assert "standard Persian" in analysis.style_guide
    assert result.prompt_tokens > 0
    assert result.completion_tokens > 0


def test_analyze_book_falls_back_when_preset_unknown():
    """The analyst guarantees a resolvable preset id even if the model invents one."""
    tree = simple_book()

    def fn(messages: list[ModelMessage], info) -> ModelResponse:
        return ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name="final_result",
                    args={
                        "detected_source_lang": "en",
                        "genre": "x", "tone": "y",
                        "language_register": "neutral",
                        "audience": "z",
                        "suggested_preset": "not-a-real-preset",
                        "style_guide": "translate",
                        "reader_notes": [],
                        "confidence": 0.5,
                    },
                    tool_call_id="tc1",
                )
            ]
        )

    result = asyncio.run(analyze_book_with_model(tree, FunctionModel(fn), PROVIDER))
    assert result.output.suggested_preset == "general"  # fallback
