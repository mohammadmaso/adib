"""The translator agent: batched segments, with protected spans, glossary
injection, rolling context, concurrency, and per-segment SQLite resumption.

A segment is one leaf node (a paragraph, a heading, a single list item) or one
sentence-group of an oversized paragraph. Most of them are *short* — a list item
is often five words — and the per-call overhead (system prompt + style guide +
glossary + rolling context + instructions) dwarfs them by an order of magnitude.
So segments are packed into **batches** that share one request: the overhead is
paid once per ~20 segments instead of once per segment, which is where nearly
all of the token and request savings come from.

Each batch is still committed *per segment* the moment it is parsed, so the run
stays resumable and cancellable at segment granularity. Correctness is not
traded away for the packing: if a batched call fails or comes back unparseable,
the batch is split in half and retried, down to single segments — so one bad
response can never poison twenty translations.

The prompt still carries enough context that chapter-dependent terminology stays
stable:
  * the preset system prompt + the approved style guide,
  * the heading path of each run of segments (chapter/section they sit under),
  * the previous 1–2 translated segments before the batch,
  * the glossary entries whose source term actually appears in the batch.
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

#: Source characters packed into one batched request. Sized so the *output*
#: (which for a script like Persian is roughly as long as the input in
#: characters, but denser in tokens) stays well inside a conservative response
#: limit, and so a batch that has to be retried is still cheap.
MAX_BATCH_CHARS = 5000

#: Hard cap on segments per batch, independent of size: a hundred one-word list
#: items in a single response is asking the model to lose count.
MAX_BATCH_SEGMENTS = 24

#: Rolling-context excerpts are trimmed to this many characters. Two full 4000
#: character paragraphs of context would cost more than the batch they precede.
CONTEXT_EXCERPT_CHARS = 240

#: Cap on glossary lines injected per batch, most frequent first.
MAX_GLOSSARY_LINES = 40

#: Block markers for batched requests. U+27E6/U+27E7 do not occur in prose.
BATCH_OPEN = "⟦"
BATCH_CLOSE = "⟧"
#: Some models "ASCII-ify" the U+27E6/U+27E7 marker glyphs into `[[`/`]]` when
#: echoing them back, rather than reproducing the exact characters. Matching
#: both open and close independently means a marker like `[[#3⟧` or `⟦#3]]`
#: still parses as a delimiter instead of leaking into a translated segment's
#: body text (see: batch output occasionally containing a literal `[[#N]]`).
_MARKER_OPEN = rf"(?:{BATCH_OPEN}|\[\[)"
_MARKER_CLOSE = rf"(?:{BATCH_CLOSE}|\]\])"
_BATCH_MARKER = re.compile(
    rf"^[ \t>*-]*{_MARKER_OPEN}#(\d+){_MARKER_CLOSE}[ \t]*", re.MULTILINE
)

#: A segment with no letter in it (bullet glyphs, bare numbers, "—", "1.2.3")
#: has nothing for a translator to do; sending it is a wasted request.
_HAS_LETTER = re.compile(r"[^\W\d_]", re.UNICODE)


class PlaceholderError(ValueError):
    """A protected span was lost or mangled during translation."""


def protect_spans(
    text: str, terms: list[str], *, start_index: int = 0
) -> tuple[str, dict[str, str]]:
    """Replace protected source forms with sentinel tokens.

    Returns (protected_text, token->original). Every token is restored by
    `restore_spans`. Terms are matched longest-first so "TCP/IP" wins over "TCP".
    `start_index` keeps token numbers unique across the segments of one batch,
    so a model reordering blocks cannot swap one segment's terms into another's.
    """
    protected: dict[str, str] = {}
    order: list[str] = []

    def repl(match: re.Match[str]) -> str:
        term = match.group(0)
        for token, orig in protected.items():
            if orig == term:
                return token
        token = f"{PLACEHOLDER_OPEN}{start_index + len(order)}{PLACEHOLDER_CLOSE}"
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


def _enforce_protected(target: str, protected: dict[str, str]) -> str:
    """Restore sentinels, then guarantee nothing protected was lost."""
    target = restore_spans(target, protected)
    for token, orig in protected.items():
        if orig not in target and token in target:
            # Model echoed the sentinel without the original back; rare, but the
            # term is more important than faithful prose.
            target = target.replace(token, orig)
    return target


def _appears_in(text: str, term: str) -> bool:
    return term.lower() in text.lower()


def _segment_glossary(segment: Segment, terms: list[GlossaryTerm]) -> list[GlossaryTerm]:
    """Glossary entries whose source form actually appears in this segment."""
    return [t for t in terms if _appears_in(segment.source_text, t.source)]


def _batch_glossary(segments: list[Segment], terms: list[GlossaryTerm]) -> list[GlossaryTerm]:
    """Glossary entries appearing anywhere in the batch, scanned once.

    One lowercased haystack for the whole batch instead of one per (segment,
    term) pair — the naive version is O(segments × terms) string searches over a
    book-sized glossary, which is measurable even before the model is called.
    """
    haystack = "\n".join(s.source_text for s in segments).lower()
    hits = [t for t in terms if t.source and t.source.lower() in haystack]
    return hits[:MAX_GLOSSARY_LINES]


def _excerpt(text: str, limit: int = CONTEXT_EXCERPT_CHARS) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


def _rolling_context(previous: list[tuple[str, str]]) -> str:
    if not previous:
        return ""
    lines = ["Recently translated (keep terminology and voice consistent):"]
    for source, target in previous:
        lines.append(f"- {_excerpt(source)}\n  → {_excerpt(target)}")
    return "\n".join(lines)


def needs_model(text: str) -> bool:
    """False for segments with nothing translatable — pure digits, bullets, rules."""
    return bool(_HAS_LETTER.search(text))


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


# ---------------------------------------------------------------------------
# Single-segment path. Still used for retries of a batch that failed down to one
# segment, and as the simplest thing that can possibly be tested.
# ---------------------------------------------------------------------------


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
    target = _enforce_protected(str(result.output), protected_map)

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
# Batching: pack segments into one request, and take them apart again.
# ---------------------------------------------------------------------------


def build_batches(
    segments: list[Segment],
    *,
    max_chars: int = MAX_BATCH_CHARS,
    max_segments: int = MAX_BATCH_SEGMENTS,
) -> list[list[Segment]]:
    """Pack consecutive segments into batches under a size and count budget.

    Reading order is preserved and never interleaved, so the rolling context of
    batch N is genuinely the prose immediately before it. A segment larger than
    the budget on its own gets a batch to itself rather than being split further
    — `segmentation.split_long_text` has already done that job upstream.
    """
    batches: list[list[Segment]] = []
    current: list[Segment] = []
    size = 0

    for segment in segments:
        length = len(segment.source_text)
        too_big = current and (size + length > max_chars or len(current) >= max_segments)
        if too_big:
            batches.append(current)
            current, size = [], 0
        current.append(segment)
        size += length

    if current:
        batches.append(current)
    return batches


def _render_batch(
    segments: list[Segment], protected_terms: list[str]
) -> tuple[str, dict[int, dict[str, str]]]:
    """Render the numbered source blocks, plus each block's protected-span map.

    Heading paths are emitted only when they *change*, which for a run of
    paragraphs inside one section means one line per batch rather than one per
    segment.
    """
    blocks: list[str] = []
    maps: dict[int, dict[str, str]] = {}
    last_path: list[str] | None = None
    token_index = 0

    for i, segment in enumerate(segments, start=1):
        path = segment.heading_path or []
        if path and path != last_path:
            blocks.append(f"Section: {' › '.join(path)}")
        last_path = path

        protected_text, protected_map = protect_spans(
            segment.source_text, protected_terms, start_index=token_index
        )
        token_index += len(protected_map)
        maps[i] = protected_map
        blocks.append(f"{BATCH_OPEN}#{i}{BATCH_CLOSE}\n{protected_text}")

    return "\n\n".join(blocks), maps


def _build_batch_prompt(
    *,
    body: str,
    count: int,
    target: str,
    glossary: list[GlossaryTerm],
    previous: list[tuple[str, str]],
) -> str:
    parts: list[str] = []
    if glossary:
        lines = ["Glossary (use these exact target forms):"]
        for t in glossary:
            lines.append(f"- {t.source} → {t.target or ''}")
        parts.append("\n".join(lines))
    prev = _rolling_context(previous)
    if prev:
        parts.append(prev)
    parts.append(
        f"Translate all {count} numbered blocks below into {target}.\n"
        f"Output format — for every block, in order, repeat its marker "
        f"{BATCH_OPEN}#N{BATCH_CLOSE} (with the block's own number in place of N) "
        f"on its own line, followed by that block's translation on the lines "
        f"after it.\n"
        f"Rules:\n"
        f"- Emit every marker exactly once, even for a block you would leave "
        f"unchanged. Never merge, split, reorder, or skip blocks.\n"
        f"- Translate each block on its own; blocks are separate parts of the "
        f"document (a heading, a list item, a paragraph), not one continuous text.\n"
        f"- Reproduce every placeholder token ({PLACEHOLDER_OPEN}…{PLACEHOLDER_CLOSE}) exactly.\n"
        f"- Preserve inline formatting (bold/italic/links/code); a block that is "
        f"a markdown table must stay a markdown table with the same row and "
        f"column count.\n"
        f"- 'Section:' lines are context only — do not translate or echo them.\n"
        f"- Output nothing but markers and translations."
    )
    parts.append(body)
    return "\n\n".join(parts)


def parse_batch_output(text: str, count: int) -> dict[int, str]:
    """Split a batched response into block index -> translation.

    Tolerant on purpose: markers may carry the translation on the same line, may
    be preceded by list/quote punctuation the model added, and blocks may be
    missing. Missing blocks are simply absent from the result — the caller
    retries exactly those rather than guessing.
    """
    matches = list(_BATCH_MARKER.finditer(text))
    out: dict[int, str] = {}
    for i, match in enumerate(matches):
        index = int(match.group(1))
        if not 1 <= index <= count or index in out:
            continue
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[match.end() : end].strip()
        if body:
            out[index] = body
    return out


def _split_usage(result: AgentResult, segments: list[Segment]) -> list[tuple[int, int]]:
    """Apportion a batch's token usage across its segments by source length.

    The per-segment cost column has to keep summing to what the run actually
    spent; without this a batched run would report zero cost for every segment
    but the first. Rounding remainder goes to the last segment.
    """
    weights = [max(len(s.source_text), 1) for s in segments]
    total = sum(weights)
    out: list[tuple[int, int]] = []
    used_in = used_out = 0
    for i, weight in enumerate(weights):
        if i == len(weights) - 1:
            out.append((result.prompt_tokens - used_in, result.completion_tokens - used_out))
            break
        share_in = result.prompt_tokens * weight // total
        share_out = result.completion_tokens * weight // total
        used_in += share_in
        used_out += share_out
        out.append((share_in, share_out))
    return out


async def translate_batch(
    agent,
    segments: list[Segment],
    *,
    glossary_terms: list[GlossaryTerm],
    previous: list[tuple[str, str]],
) -> list[TranslatedSegment]:
    """Translate a batch in one request; return only the blocks that came back.

    A short result is not an error here — the caller decides whether to retry
    the missing segments individually or give up on them.
    """
    near = _batch_glossary(segments, glossary_terms)
    protected_terms = [t.source for t in near if t.policy.value in ("keep", "translate_paren")]
    body, maps = _render_batch(segments, protected_terms)

    prompt = _build_batch_prompt(
        body=body,
        count=len(segments),
        target="the target language",
        glossary=near,
        previous=previous,
    )

    run = await agent.run(prompt)
    result: AgentResult = result_from_run(run)
    blocks = parse_batch_output(str(result.output), len(segments))
    usage = _split_usage(result, segments)

    out: list[TranslatedSegment] = []
    for i, segment in enumerate(segments, start=1):
        if i not in blocks:
            continue
        prompt_tokens, completion_tokens = usage[i - 1]
        out.append(
            TranslatedSegment(
                segment_id=segment.id,
                target_text=_enforce_protected(blocks[i], maps[i]),
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                requests=result.requests,
            )
        )
    return out


# ---------------------------------------------------------------------------
# Orchestration: translate outstanding segments batch by batch, committing each
# segment as it lands, so a crash resumes exactly where it stopped.
# ---------------------------------------------------------------------------


def _previous_index(segments: list[Segment]) -> dict[str, int]:
    return {segment.id: i for i, segment in enumerate(segments)}


def _previous_for(
    segment: Segment, segments: list[Segment], index: dict[str, int] | None = None
) -> list[tuple[str, str]]:
    """The one or two already-translated segments immediately before `segment`."""
    positions = index if index is not None else _previous_index(segments)
    idx = positions.get(segment.id)
    if idx is None:
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
    only_ids: set[str] | None = None,
    batch_chars: int | None = None,
    batch_segments: int | None = None,
) -> dict[str, object]:
    """Translate every pending segment, committing each the moment it lands.

    Resumable: reads `store.pending_segments()`, so a second call after a crash
    (or a paused run) continues from exactly where the last one stopped. Returns
    run stats for the UI meter. `model` lets tests inject a deterministic
    `FunctionModel`/`TestModel`; production uses the real bound model via
    `build_model`. `only_ids` narrows the run to specific segments, which is what
    a single-segment retranslate needs — without it, retranslating one paragraph
    would quietly re-run every other pending segment in the book.

    Segments are packed into batches (see `build_batches`) so the fixed prompt
    overhead is amortized across ~20 segments per request. A fixed pool of worker
    coroutines pulls batches from a shared queue (rather than firing everything
    at once behind a semaphore) so `should_pause` — polled between batches, not
    mid-flight — can stop the run from picking up new work while letting
    whatever's already in progress finish cleanly.
    """
    from pydantic_ai import Agent

    from adib_engine.agents.base import build_model, model_settings

    segments = store.pending_segments()
    if only_ids is not None:
        segments = [s for s in segments if s.id in only_ids]

    empty_result: dict[str, object] = {
        "queued": 0,
        "segments": 0,
        "skipped": 0,
        "requests": 0,
        "tokens": 0,
        "cost_usd": 0.0,
        "failed": [],
        "paused": False,
    }
    if not segments:
        return empty_result

    # Segments with nothing to translate (bullets, bare numbers, separators) are
    # settled locally: a request for "3." is pure overhead.
    trivial = [s for s in segments if not needs_model(s.source_text)]
    for segment in trivial:
        store.record_translation(
            segment.id,
            target_text=segment.source_text,
            model_name=provider.model,
            status=SegmentStatus.SKIPPED,
        )
        if progress is not None:
            progress(segment.id)
    pending = [s for s in segments if needs_model(s.source_text)]

    if not pending:
        return {**empty_result, "queued": len(segments), "skipped": len(trivial)}

    if model is None:
        model = build_model(provider, api_key)
    agent = Agent(
        model,
        model_settings=model_settings(provider),
        system_prompt=preset.system_prompt + (f"\n\n{style_guide}" if style_guide else ""),
        retries=2,
    )

    all_terms = glossary_terms or store.terms(enabled_only=True)
    batches = build_batches(
        pending,
        max_chars=batch_chars or MAX_BATCH_CHARS,
        max_segments=batch_segments or MAX_BATCH_SEGMENTS,
    )
    positions = _previous_index(pending)

    done: list[str] = []
    failed: list[tuple[str, str]] = []
    total_tokens = 0
    total_cost = 0.0
    total_requests = 0
    paused = False

    queue_index = 0
    queue_lock = asyncio.Lock()

    async def next_batch() -> list[Segment] | None:
        nonlocal queue_index, paused
        async with queue_lock:
            if should_pause is not None and should_pause():
                paused = True
                return None
            if queue_index >= len(batches):
                return None
            batch = batches[queue_index]
            queue_index += 1
            return batch

    def commit(segment: Segment, ts: TranslatedSegment) -> None:
        nonlocal total_tokens, total_cost, total_requests
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
        done.append(segment.id)
        if progress is not None:
            progress(segment.id)

    async def run_group(group: list[Segment]) -> None:
        """Translate a group, halving it on failure until segments stand alone.

        Splitting (rather than failing the whole group) is what keeps batching
        safe: a response the parser cannot use costs a retry of ~20 segments,
        never twenty lost translations.
        """
        nonlocal total_requests
        if not group:
            return

        previous = _previous_for(group[0], pending, positions)
        try:
            results = await translate_batch(
                agent, group, glossary_terms=all_terms, previous=previous
            )
            total_requests += max((r.requests for r in results), default=1)
        except Exception as exc:  # noqa: BLE001 - retryable network/model errors
            total_requests += 1
            if len(group) == 1:
                failed.append((group[0].id, str(exc)))
                store.record_failure(group[0].id, str(exc))
                return
            log.warning("batch of %d failed (%s); splitting", len(group), exc)
            half = len(group) // 2
            await run_group(group[:half])
            await run_group(group[half:])
            return

        by_id = {ts.segment_id: ts for ts in results}
        for segment in group:
            ts = by_id.get(segment.id)
            if ts is not None:
                commit(segment, ts)

        missing = [s for s in group if s.id not in by_id]
        if not missing:
            return
        if len(missing) == len(group):
            # Nothing usable came back at all: halve rather than re-sending the
            # identical prompt, which would just fail the same way.
            if len(group) == 1:
                reason = "model returned no usable output for this segment"
                failed.append((group[0].id, reason))
                store.record_failure(group[0].id, reason)
                return
            half = len(group) // 2
            await run_group(group[:half])
            await run_group(group[half:])
            return
        # Strictly smaller than `group`, so this terminates.
        for retry in build_batches(
            missing,
            max_chars=batch_chars or MAX_BATCH_CHARS,
            max_segments=batch_segments or MAX_BATCH_SEGMENTS,
        ):
            await run_group(retry)

    async def worker() -> None:
        while True:
            batch = await next_batch()
            if batch is None:
                return
            await run_group(batch)

    worker_count = max(1, concurrency or provider.concurrency)
    await asyncio.gather(*(worker() for _ in range(worker_count)))
    return {
        "queued": len(segments),
        "segments": len(done) + len(trivial),
        "skipped": len(trivial),
        "requests": total_requests,
        "tokens": total_tokens,
        "cost_usd": total_cost,
        "failed": failed,
        "paused": paused,
    }
