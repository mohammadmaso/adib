"""The analysis agent: reads a sample, proposes a preset + style delta.

This is Gate 2's input. It returns a structured `BookAnalysis` — detected
language, genre, tone, register, audience, the closest built-in preset id, a
style delta on top of it, a prose style guide injected into every translation
call, and a set of translation hazards found while sampling.
"""

from __future__ import annotations

import logging

from pydantic_ai import Agent

from adib_engine.agents.base import AgentResult, build_model, model_settings, result_from_run
from adib_engine.agents.context import stratified_sample
from adib_engine.models.analysis import BookAnalysis
from adib_engine.models.document import DocTree
from adib_engine.models.project import ProviderSettings
from adib_engine.presets.library import PresetLibrary

log = logging.getLogger(__name__)

ANALYST_SYSTEM_PROMPT = """You are the chief editor of a publishing house that \
translates whole books professionally. You are given a sample of one book and \
must propose how it should be translated.

Produce a structured analysis. Be specific but honest: if the sample is too \
small to judge tone confidently, return a lower confidence and keep reader \
notes focused on what you can see."""


def build_analyst_agent(provider: ProviderSettings, api_key: str | None = None) -> Agent:
    """The analyst agent, bound to the project's endpoint.

    Deterministic structured output: the agent always emits a `BookAnalysis`,
    which Gate 2 renders as editable controls rather than free prose.
    """
    return Agent(
        build_model(provider, api_key),
        model_settings=model_settings(provider),
        system_prompt=ANALYST_SYSTEM_PROMPT,
        output_type=BookAnalysis,
        retries=2,
    )


def _preset_catalog(library: PresetLibrary) -> str:
    """A short description of every preset, for the agent to pick from."""
    lines = ["Available presets:", ""]
    for preset in library.all():
        lines.append(f"- `{preset.id}` — {preset.name}: {preset.description}")
    return "\n".join(lines)


def _sample_prompt(sample: str, catalog: str, source_lang_hint: str | None) -> str:
    parts = [catalog, "", "Book sample:", sample]
    if source_lang_hint:
        parts.append("")
        parts.append(
            f"(Ingest guessed the source language as {source_lang_hint}; "
            "confirm or correct it.)"
        )
    return "\n".join(parts)


async def analyze_book(
    tree: DocTree,
    provider: ProviderSettings,
    *,
    api_key: str | None = None,
    library: PresetLibrary | None = None,
    max_sample_chars: int = 12_000,
) -> AgentResult:
    """Run the analyst over a stratified sample of `tree`.

    Returns an `AgentResult` whose `.output` is a `BookAnalysis`. The caller
    persists it via the store and renders it in Gate 2.
    """
    agent = build_analyst_agent(provider, api_key)
    return await analyze_book_with_model(
        tree, agent, provider, api_key=api_key, library=library, max_sample_chars=max_sample_chars
    )


async def analyze_book_with_model(
    tree: DocTree,
    model,
    provider: ProviderSettings,
    *,
    api_key: str | None = None,
    library: PresetLibrary | None = None,
    max_sample_chars: int = 12_000,
) -> AgentResult:
    """Run the analyst against an injected agent or model.

    `model` may be a pydantic-ai `Agent` (production, built by
    `build_analyst_agent`) or a bare model object (`TestModel`/`FunctionModel`,
    tests). Returns an `AgentResult` with the `BookAnalysis` and usage.
    """
    sample = stratified_sample(tree, max_chars=max_sample_chars)
    catalog = _preset_catalog(library or PresetLibrary())
    prompt = _sample_prompt(sample, catalog, tree.meta.source_lang)

    if isinstance(model, Agent):
        run = await model.run(prompt)
    else:
        agent: Agent = Agent(
            model,
            system_prompt=ANALYST_SYSTEM_PROMPT,
            output_type=BookAnalysis,
            retries=0,
        )
        run = await agent.run(prompt)

    result = result_from_run(run)
    analysis = result.output
    assert isinstance(analysis, BookAnalysis), f"analyst output was {type(analysis)!r}"

    # Keep the caller honest: the suggested preset id must exist.
    if not (library or PresetLibrary()).load(analysis.suggested_preset):
        log.warning(
            "analyst suggested unknown preset %r; falling back to 'general'",
            analysis.suggested_preset,
        )
        analysis.suggested_preset = "general"

    return result
