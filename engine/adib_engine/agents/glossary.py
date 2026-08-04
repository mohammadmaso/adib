"""The glossary adjudication agent: which mined candidates to keep, and their
target translations.

Two passes happen before translation: statistical mining (mine.py) surfaces
candidates; this LLM agent, shown the candidates with sample context and the
book's tone, decides keep/drop and supplies the target translation, the default
policy, and a 1–3 sentence explanation *in the target language*. That
explanation becomes the footnote/appendix body, so it must read like a publisher
wrote it, not a dictionary.
"""

from __future__ import annotations

import logging

from pydantic import BaseModel, Field
from pydantic_ai import Agent

from adib_engine.agents.base import AgentResult, build_model, model_settings, result_from_run
from adib_engine.models.analysis import BookAnalysis
from adib_engine.models.glossary import GlossaryCandidate, GlossaryTerm, TermPolicy
from adib_engine.models.project import ProviderSettings

log = logging.getLogger(__name__)

_TARGET_LANG_NAMES = {
    "fa": "Persian (فارسی)",
    "ar": "Arabic (العربية)",
    "en": "English",
    "de": "German",
    "fr": "French",
    "es": "Spanish",
}


class GlossaryDecision(BaseModel):
    """One candidate's keep/drop verdict, with a target form if kept."""

    index: int
    kept: bool = False
    target: str | None = None
    policy: TermPolicy = TermPolicy.TRANSLATE
    part_of_speech: str | None = None
    explanation: str | None = None


class GlossaryVerdict(BaseModel):
    """Verdicts for one batch of candidates."""

    decisions: list[GlossaryDecision] = Field(default_factory=list)
    rationale: str = ""


GLOSSARY_SYSTEM_PROMPT = """You are the terminology editor of a publishing house. \
You are given a list of candidate glossary terms mined from a book, each with a \
sample sentence and a reason it was surfaced. Decide for each whether to keep it \
as a book glossary term.

Keep:
- technical terms, names, products, organizations, acronyms, and code identifiers
  a translator must not variate across the book,
- terms whose translation will be contested or specialised.

Drop:
- capitalized phrases that are ordinary prose or a one-off sentence (e.g. "The \
  Quick Brown" with no domain weight),
- obvious stopword-heavy phrases with no terminological weight.

For every kept term supply a `target` in the target language, a default policy, \
and a 1–3 sentence `explanation` written IN the target language (this becomes \
the footnote)."""


def build_glossary_agent(provider: ProviderSettings, api_key: str | None = None) -> Agent:
    return Agent(
        build_model(provider, api_key),
        model_settings=model_settings(provider),
        system_prompt=GLOSSARY_SYSTEM_PROMPT,
        output_type=GlossaryVerdict,
        retries=2,
    )


def _glossary_agent(model) -> Agent:
    """An agent over an injected model (tests). Same system prompt/output type."""
    return Agent(
        model,
        system_prompt=GLOSSARY_SYSTEM_PROMPT,
        output_type=GlossaryVerdict,
        retries=0,
    )


def _batch_candidates(
    candidates: list[GlossaryCandidate], max_chars: int = 6000
) -> list[list[GlossaryCandidate]]:
    """Split candidates into prompt-sized batches, keeping indexes stable."""
    batches: list[list[GlossaryCandidate]] = []
    current: list[GlossaryCandidate] = []
    size = 0
    for cand in candidates:
        rough = len(cand.source) + len(cand.sample or "") + 40
        if current and size + rough > max_chars:
            batches.append(current)
            current = []
            size = 0
        current.append(cand)
        size += rough
    if current:
        batches.append(current)
    return batches


def _batch_prompt(
    batch: list[GlossaryCandidate],
    *,
    target_lang: str,
    first_index: int,
    analysis: BookAnalysis | None = None,
) -> str:
    lang = _TARGET_LANG_NAMES.get(target_lang.split("-")[0], target_lang)
    lines = [f"Target language: {lang}.", ""]
    if analysis:
        lines.append(f"Book tone: {analysis.tone}. Register: {analysis.language_register.value}.")
        lines.append("")
    lines.append("Candidates (index : source — reason, frequency, sample):")
    for i, cand in enumerate(batch):
        sample = (cand.sample or "").replace("\n", " ")
        head = f"{first_index + i}: {cand.source} — {cand.reason} x{cand.frequency}"
        lines.append(head + (f" — \"{sample}\"" if sample else ""))
    lines.append("")
    lines.append(
        "Return a decision for every index you were shown. The index numbers in your "
        "response MUST match the index numbers above — `index` is the exact integer on "
        "the left of each candidate line. Do not renumber."
    )
    return "\n".join(lines)


async def adjudicate_glossary(
    candidates: list[GlossaryCandidate],
    provider: ProviderSettings,
    *,
    target_lang: str,
    api_key: str | None = None,
    analysis: BookAnalysis | None = None,
    default_policy: TermPolicy = TermPolicy.TRANSLATE,
    model=None,
) -> AgentResult:
    """Adjudicate all candidates in batches, returning `AgentResult` with a
    `GlossaryVerdict` aggregate as `.output`.

    The caller (the API layer) persists kept terms via the store. `default_policy`
    applies to kept terms the agent did not specify a policy for. `model` lets
    tests inject a deterministic `FunctionModel`/`TestModel`; production uses the
    real bound model.
    """
    batches = _batch_candidates(candidates)
    agent = build_glossary_agent(provider, api_key) if model is None else _glossary_agent(model)

    all_decisions: list[GlossaryDecision] = []
    total_prompt = total_completion = requests = 0
    # Candidate index is global across batches, so an agent that returns sparse
    # indexes still maps cleanly into `decisions_to_terms`.
    global_index = 0

    for batch in batches:
        prompt = _batch_prompt(
            batch, target_lang=target_lang, first_index=global_index, analysis=analysis
        )
        run = await agent.run(prompt)
        res = result_from_run(run)
        total_prompt += res.prompt_tokens
        total_completion += res.completion_tokens
        requests += res.requests

        verdict = res.output
        assert isinstance(verdict, GlossaryVerdict), f"glossary agent returned {type(verdict)!r}"
        all_decisions.extend(verdict.decisions)
        global_index += len(batch)

    aggregate = GlossaryVerdict(decisions=all_decisions)
    return AgentResult(
        output=aggregate,
        prompt_tokens=total_prompt,
        completion_tokens=total_completion,
        requests=requests,
    )


def decisions_to_terms(
    candidates: list[GlossaryCandidate],
    decisions: list[GlossaryDecision],
    *,
    default_policy: TermPolicy,
) -> list[GlossaryTerm]:
    """Turn kept decisions back into persisted `GlossaryTerm`s.

    `decision.index` maps into `candidates` (0-based, matching the index shown in
    the prompt and accumulated across batches). A kept term without a target keeps
    its source as a placeholder so the row is valid but the user is nudged to fill
    the target in Gate 2.
    """
    by_index = {d.index: d for d in decisions if d.kept}
    terms: list[GlossaryTerm] = []
    for i, cand in enumerate(candidates):
        d = by_index.get(i)
        if d is None:
            continue
        terms.append(
            GlossaryTerm(
                source=cand.source,
                target=d.target,
                policy=d.policy if d.target else default_policy,
                explanation=d.explanation,
                part_of_speech=d.part_of_speech,
                first_seen_node=cand.first_seen_node,
                frequency=cand.frequency,
                origin="llm",
            )
        )
    return terms
