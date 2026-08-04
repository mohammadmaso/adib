"""The translator agent: one segment at a time, with protected spans, glossary
injection, rolling context, concurrency, and per-segment SQLite resumption.

A segment is one leaf node (or one sentence-group of an oversized paragraph)
from the ingest tree. Each is translated independently — that is what makes the
run resumable and cancellable — but the prompt carries enough context that
chapter-dependent terminology stays stable:
  * the preset system prompt + the approved style guide,
  * the current heading path (chapter/section the segment sits under),
  * the previous 1–2 translated segments in the same spot,
  * the glossary entries whose source term actually appears in this segment.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass

from adib_engine.agents.base import AgentResult, cost_for, result_from_run
from adib_engine.agents.placeholders import PLACEHOLDER_CLOSE, PLACEHOLDER_OPEN
from adib_engine.models.glossary import GlossaryTerm
from adib_engine.models.project import ProviderSettings
from adib_engine.models.segment import Segment, SegmentStatus
from adib_engine.qa import run_qa
from adib_engine.store.project_store import ProjectStore

log = logging.getLogger(__name__)


class PlaceholderError(ValueError):
    """A protected span was lost or mangled during translation."""


def protect_spans(text: str, terms: list[str]) -> tuple[str, dict[str, str]]:
    """Replace protected source forms with sentinel tokens.

    Returns (protected_text, token->original). Every token is restored by
    `restore_spans`. Terms are matched longest-first so "TCP/IP" wins over "TCP".
    """
    protected: dict[str, str] = {}
    order: list[str] = []

    def repl(match: re.Match[str]) -> str:
        term = match.group(0)
        for token, orig in protected.items():
            if orig == term:
                return token
        token = f"{PLACEHOLDER_OPEN}{len(order)}{PLACEHOLDER_CLOSE}"
        order.append(term)
        protected[token] = term
        return token

    for term in sorted((t for t in terms if t), key=len, reverse=True):
        text = re.sub(re.escape(term), repl, text)
    return text, protected


def restore_spans(text: str, protected: dict[str, str]) -> str:
    for token, term in protected.items():
        text = text.replace(token, term)
    return text


def _appears_in(text: str, term: str) -> bool:
    return term.lower() in text.lower()


def _segment_glossary(segment: Segment, terms: list[GlossaryTerm]) -> list[GlossaryTerm]:
    """Glossary entries whose source form actually appears in this segment."""
    return [t for t in terms if _appears_in(segment.source_text, t.source)]


def _rolling_context(previous: list[tuple[str, str]]) -> str:
    if not previous:
        return ""
    lines = ["Recently translated (keep terminology and voice consistent):"]
    for source, target in previous:
        lines.append(f"- {source}\n  → {target}")
    return "\n".join(lines)


@dataclass
class TranslatedSegment:
    segment_id: str
    target_text: str
    prompt_tokens: int
    completion_tokens: int
    requests: int

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


async def translate_segment(
    agent,
    segment: Segment,
    *,
    provider: ProviderSettings,
    glossary_terms: list[GlossaryTerm],
    previous: list[tuple[str, str]],
    heading_path: list[str] | None = None,
) -> TranslatedSegment:
    """Translate one segment, protecting/restoring protected spans.

    `agent` is a pydantic-ai `Agent` returning plain text. `provider` drives
    usage-based cost accounting.
    """
    path = heading_path or segment.heading_path
    near = _segment_glossary(segment, glossary_terms)
    protected_terms = [t.source for t in near if t.policy.value in ("keep", "translate_paren")]
    protected_text, protected_map = protect_spans(segment.source_text, protected_terms)

    prompt = _build_prompt(
        source=protected_text,
        target="the target language",
        heading_path=path,
        glossary=near,
        previous=previous,
    )

    run = await agent.run(prompt)
    result: AgentResult = result_from_run(run)
    target = str(result.output)

    # Restore, then guarantee nothing protected is lost.
    target = restore_spans(target, protected_map)
    for token, orig in protected_map.items():
        if orig not in target and token in target:
            # Model echoed the sentinel without the original back; rare, but the
            # term is more important than faithful prose.
            target = target.replace(token, orig)

    return TranslatedSegment(
        segment_id=segment.id,
        target_text=target,
        prompt_tokens=result.prompt_tokens,
        completion_tokens=result.completion_tokens,
        requests=result.requests,
    )


def _build_prompt(
    *,
    source: str,
    target: str,
    heading_path: list[str] | None,
    glossary: list[GlossaryTerm],
    previous: list[tuple[str, str]],
) -> str:
    parts: list[str] = []
    if heading_path:
        parts.append(f"Section heading path: {' › '.join(heading_path)}")
    if glossary:
        lines = ["Glossary (use these exact target forms):"]
        for t in glossary:
            lines.append(f"- {t.source} → {t.target or ''}")
        parts.append("\n".join(lines))
    prev = _rolling_context(previous)
    if prev:
        parts.append(prev)
    parts.append(
        f"Translate the following segment into {target}. Reproduce every "
        f"placeholder token ({PLACEHOLDER_OPEN}…{PLACEHOLDER_CLOSE}) exactly. "
        "Preserve inline formatting (bold/italic/links/code). Output only the translation."
    )
    parts.append(source)
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Orchestration: translate outstanding segments with a semaphore and per-segment
# commits, so a crash resumes exactly where it stopped.
# ---------------------------------------------------------------------------


def _previous_for(
    segment: Segment, segments: list[Segment]
) -> list[tuple[str, str]]:
    """The one or two already-translated segments immediately before `segment`."""
    # segments are ordered and unique in a run; index() is O(n) but n is small
    # per chapter and this is not the hot path.
    try:
        idx = segments.index(segment)
    except ValueError:
        return []
    out: list[tuple[str, str]] = []
    for other in reversed(segments[:idx]):
        if other.target_text and other.status in (SegmentStatus.TRANSLATED, SegmentStatus.APPROVED):
            out.append((other.source_text, other.target_text))
            if len(out) >= 2:
                break
    return list(reversed(out))


async def translate_book(
    store: ProjectStore,
    provider: ProviderSettings,
    preset,
    *,
    api_key: str | None = None,
    style_guide: str | None = None,
    concurrency: int | None = None,
    glossary_terms: list[GlossaryTerm] | None = None,
    progress=None,
    model=None,
    should_pause=None,
) -> dict[str, object]:
    """Translate every pending segment, committing each the moment it lands.

    Resumable: reads `store.pending_segments()`, so a second call after a crash
    (or a paused run) continues from exactly where the last one stopped. Returns
    run stats for the UI meter. `model` lets tests inject a deterministic
    `FunctionModel`/`TestModel`; production uses the real bound model via
    `build_model`.

    A fixed pool of worker coroutines pulls from a shared queue (rather than
    firing every segment at once behind a semaphore) so `should_pause` — polled
    between segments, not mid-flight — can stop the run from picking up new
    work while letting whatever's already in progress finish cleanly.
    """
    from pydantic_ai import Agent

    from adib_engine.agents.base import build_model, model_settings

    segments = store.pending_segments()
    if not segments:
        return {
            "queued": 0,
            "segments": 0,
            "tokens": 0,
            "cost_usd": 0.0,
            "failed": [],
            "paused": False,
        }

    if model is None:
        model = build_model(provider, api_key)
    agent = Agent(
        model,
        model_settings=model_settings(provider),
        system_prompt=preset.system_prompt + (f"\n\n{style_guide}" if style_guide else ""),
        retries=2,
    )

    all_terms = glossary_terms or store.terms(enabled_only=True)
    results: list[dict[str, object]] = []
    failed: list[tuple[str, str]] = []
    total_tokens = 0
    total_cost = 0.0
    paused = False

    queue_index = 0
    queue_lock = asyncio.Lock()

    async def next_segment() -> Segment | None:
        nonlocal queue_index, paused
        async with queue_lock:
            if should_pause is not None and should_pause():
                paused = True
                return None
            if queue_index >= len(segments):
                return None
            segment = segments[queue_index]
            queue_index += 1
            return segment

    async def worker() -> None:
        nonlocal total_tokens, total_cost
        while True:
            segment = await next_segment()
            if segment is None:
                return
            previous = _previous_for(segment, segments)
            try:
                ts = await translate_segment(
                    agent, segment, provider=provider, glossary_terms=all_terms, previous=previous
                )
                cost = cost_for(provider, ts.prompt_tokens, ts.completion_tokens)
                flags = run_qa(segment.source_text, ts.target_text, glossary_terms=all_terms)
                store.record_translation(
                    segment.id,
                    target_text=ts.target_text,
                    model_name=provider.model,
                    prompt_tokens=ts.prompt_tokens,
                    completion_tokens=ts.completion_tokens,
                    cost_usd=cost,
                    status=SegmentStatus.TRANSLATED,
                    qa_flags=[f.model_dump(mode="json") for f in flags],
                )
                total_tokens += ts.total_tokens
                total_cost += cost
                results.append({"id": segment.id, "tokens": ts.total_tokens, "cost": cost})
                if progress is not None:
                    progress(segment.id)
            except Exception as exc:  # noqa: PERF203 - retryable network/model errors
                failed.append((segment.id, str(exc)))
                store.record_failure(segment.id, str(exc))

    worker_count = concurrency or provider.concurrency
    await asyncio.gather(*(worker() for _ in range(worker_count)))
    return {
        "queued": len(segments),
        "segments": len(results),
        "tokens": total_tokens,
        "cost_usd": total_cost,
        "failed": failed,
        "paused": paused,
    }
