"""Translator agent tests: placeholder protection, glossary scoping, and the
resumable per-segment orchestration in `translate_book`.

`translate_book` is exercised against a real `ProjectStore` (SQLite in tmp_path)
with a `FunctionModel` standing in for the live endpoint, so resumption after a
simulated crash is a real assertion about `pending_segments()`, not a mock.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart
from pydantic_ai.models.function import FunctionModel

from adib_engine.agents.translate import (
    PLACEHOLDER_CLOSE,
    PLACEHOLDER_OPEN,
    protect_spans,
    restore_spans,
    translate_book,
    translate_segment,
)
from adib_engine.models.glossary import GlossaryTerm, TermPolicy
from adib_engine.models.preset import Preset
from adib_engine.models.project import ProviderSettings
from adib_engine.models.segment import Segment, SegmentStatus
from adib_engine.store.project_store import create_project
from tests.fixtures import simple_book

PROVIDER = ProviderSettings(
    base_url="http://test", model="test", concurrency=2,
    price_per_mtok_in=2.0, price_per_mtok_out=6.0,
)


def _preset() -> Preset:
    return Preset(id="test", name="Test", system_prompt="Translate faithfully into Persian.")


@pytest.fixture
def store(tmp_path: Path):
    with create_project(
        tmp_path / "book.adib", name="Test Book", source_path="/tmp/book.pdf", target_lang="fa"
    ) as s:
        yield s


# ---------------------------------------------------------------------------
# Placeholder protection
# ---------------------------------------------------------------------------


def test_protect_spans_uses_longest_match_first():
    text = "TCP/IP relies on TCP for delivery."
    protected, mapping = protect_spans(text, ["TCP/IP", "TCP"])
    assert "TCP/IP" not in protected  # fully replaced, not left as a TCP+/IP remnant
    assert protected.count(PLACEHOLDER_OPEN) == 2
    restored = restore_spans(protected, mapping)
    assert restored == text


def test_protect_spans_reuses_token_for_repeated_term():
    text = "The API calls the API twice."
    protected, mapping = protect_spans(text, ["API"])
    # Two occurrences of the same term share one token.
    assert len(mapping) == 1
    assert protected.count(next(iter(mapping))) == 2


def test_protect_spans_ignores_empty_and_absent_terms():
    text = "Nothing special here."
    protected, mapping = protect_spans(text, ["", "NotPresent"])
    assert protected == text
    assert mapping == {}


def test_restore_spans_is_idempotent_on_untouched_text():
    text = "plain sentence"
    assert restore_spans(text, {}) == text


# ---------------------------------------------------------------------------
# translate_segment: protected terms must survive even if the model drops
# the wrapping punctuation, and glossary scoping only sends relevant terms.
# ---------------------------------------------------------------------------


def _echo_sentinel_fn(reply: str):
    def fn(messages: list[ModelMessage], info) -> ModelResponse:
        return ModelResponse(parts=[TextPart(content=reply)])

    return fn


def test_translate_segment_restores_protected_terms():
    agent = Agent(
        FunctionModel(_echo_sentinel_fn(f"این یک {PLACEHOLDER_OPEN}0{PLACEHOLDER_CLOSE} است.")),
        retries=0,
    )
    seg = Segment(
        id="seg-1", node_id="n1", ordinal=0, kind="paragraph",
        source_text="This is a TCP handshake.",
    )
    terms = [GlossaryTerm(source="TCP", target="تی‌سی‌پی", policy=TermPolicy.KEEP)]
    out = asyncio.run(
        translate_segment(agent, seg, provider=PROVIDER, glossary_terms=terms, previous=[])
    )
    assert "TCP" in out.target_text
    assert PLACEHOLDER_OPEN not in out.target_text


def test_translate_segment_restores_term_even_if_sentinel_survives_unwrapped():
    """If the model garbles the sentinel back verbatim, the term still wins."""
    reply = f"{PLACEHOLDER_OPEN}0{PLACEHOLDER_CLOSE}"
    agent = Agent(FunctionModel(_echo_sentinel_fn(reply)), retries=0)
    seg = Segment(id="seg-2", node_id="n2", ordinal=0, kind="paragraph", source_text="API")
    terms = [GlossaryTerm(source="API", target="ای‌پی‌آی", policy=TermPolicy.KEEP)]
    out = asyncio.run(
        translate_segment(agent, seg, provider=PROVIDER, glossary_terms=terms, previous=[])
    )
    assert out.target_text == "API"


def test_translate_segment_only_sends_glossary_terms_present_in_segment():
    captured: dict[str, str] = {}

    def fn(messages: list[ModelMessage], info) -> ModelResponse:
        captured["prompt"] = messages[-1].parts[-1].content
        return ModelResponse(parts=[TextPart(content="ok")])

    agent = Agent(FunctionModel(fn), retries=0)
    seg = Segment(
        id="seg-3", node_id="n3", ordinal=0, kind="paragraph",
        source_text="Only mentions TCP here.",
    )
    terms = [
        GlossaryTerm(source="TCP", target="تی‌سی‌پی", policy=TermPolicy.TRANSLATE),
        GlossaryTerm(source="Unrelated Term", target="X", policy=TermPolicy.TRANSLATE),
    ]
    asyncio.run(translate_segment(agent, seg, provider=PROVIDER, glossary_terms=terms, previous=[]))
    # The captured content is the last UserPromptPart's content string.
    assert "TCP" in captured["prompt"]
    assert "Unrelated Term" not in captured["prompt"]


def test_translate_segment_includes_heading_path_and_rolling_context():
    captured: dict[str, str] = {}

    def fn(messages: list[ModelMessage], info) -> ModelResponse:
        captured["prompt"] = messages[-1].parts[-1].content
        return ModelResponse(parts=[TextPart(content="ok")])

    agent = Agent(FunctionModel(fn), retries=0)
    seg = Segment(
        id="seg-4", node_id="n4", ordinal=0, kind="paragraph",
        source_text="More prose.", heading_path=["Chapter 1", "Networking"],
    )
    asyncio.run(
        translate_segment(
            agent, seg, provider=PROVIDER, glossary_terms=[],
            previous=[("Earlier source.", "متن قبلی.")],
        )
    )
    assert "Chapter 1 › Networking" in captured["prompt"]
    assert "Earlier source." in captured["prompt"]
    assert "متن قبلی." in captured["prompt"]


# ---------------------------------------------------------------------------
# translate_book: resumable orchestration against a real store.
# ---------------------------------------------------------------------------


def _upper_fn(messages: list[ModelMessage], info) -> ModelResponse:
    prompt = messages[-1].parts[-1].content
    # last line of the prompt is the source text (see _build_prompt).
    source = prompt.strip().splitlines()[-1]
    return ModelResponse(parts=[TextPart(content=f"[fa] {source}")])


def test_translate_book_translates_every_pending_segment_and_commits(store):
    store.sync_segments(simple_book())
    result = asyncio.run(
        translate_book(store, PROVIDER, _preset(), model=FunctionModel(_upper_fn))
    )
    assert result["segments"] == result["queued"] > 0
    assert not result["failed"]
    assert result["tokens"] > 0
    assert result["cost_usd"] > 0

    remaining = store.pending_segments()
    assert remaining == []
    translated = store.segments(status=SegmentStatus.TRANSLATED)
    assert all(seg.target_text and seg.target_text.startswith("[fa]") for seg in translated)


def test_translate_book_is_a_noop_when_nothing_pending(store):
    result = asyncio.run(translate_book(store, PROVIDER, _preset(), model=FunctionModel(_upper_fn)))
    assert result == {
        "queued": 0,
        "segments": 0,
        "tokens": 0,
        "cost_usd": 0.0,
        "failed": [],
        "paused": False,
    }


def test_translate_book_resumes_after_a_simulated_crash(store):
    """Translate the first half, simulate a crash, then finish the rest."""
    store.sync_segments(simple_book())
    all_ids = [s.id for s in store.segments()]
    assert len(all_ids) > 2

    half = len(all_ids) // 2
    # Simulate segments 0..half already translated in a prior (interrupted) run.
    for sid in all_ids[:half]:
        store.record_translation(sid, target_text="[fa] already done", model_name="test")

    result = asyncio.run(
        translate_book(store, PROVIDER, _preset(), model=FunctionModel(_upper_fn))
    )
    # Only the remaining segments were sent to the model.
    assert result["queued"] == len(all_ids) - half
    assert result["segments"] == len(all_ids) - half

    # Every segment is now translated, and the pre-existing ones were untouched.
    for sid in all_ids[:half]:
        seg = store.get_segment(sid)
        assert seg.target_text == "[fa] already done"
    for sid in all_ids[half:]:
        seg = store.get_segment(sid)
        assert seg.target_text and seg.target_text.startswith("[fa]")
    assert store.pending_segments() == []


def test_translate_book_skips_locked_segments(store):
    store.sync_segments(simple_book())
    all_ids = [s.id for s in store.segments()]
    locked_id = all_ids[0]
    from adib_engine.models.segment import SegmentUpdate

    store.update_segment(locked_id, SegmentUpdate(target_text="Human edit.", locked=True))

    asyncio.run(translate_book(store, PROVIDER, _preset(), model=FunctionModel(_upper_fn)))

    locked_seg = store.get_segment(locked_id)
    assert locked_seg.target_text == "Human edit."  # untouched by the run


def test_translate_book_stops_early_when_paused(store):
    """`should_pause` is polled between segments, not mid-flight: a pause
    request lets whatever's in progress finish, then stops picking up more,
    leaving the rest pending for a later resumed run."""
    store.sync_segments(simple_book())
    all_ids = [s.id for s in store.segments()]
    assert len(all_ids) > 2

    completed: list[str] = []

    result = asyncio.run(
        translate_book(
            store,
            PROVIDER,
            _preset(),
            model=FunctionModel(_upper_fn),
            concurrency=1,
            progress=completed.append,
            should_pause=lambda: len(completed) >= 1,
        )
    )

    assert result["paused"] is True
    assert 0 < result["segments"] < len(all_ids)
    assert store.pending_segments()  # something left for the next run


def test_translate_book_records_failure_without_aborting_the_batch(store):
    store.sync_segments(simple_book())
    all_ids = [s.id for s in store.segments()]
    calls = {"n": 0}

    def flaky_fn(messages: list[ModelMessage], info) -> ModelResponse:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("simulated 500")
        return ModelResponse(parts=[TextPart(content="[fa] ok")])

    result = asyncio.run(
        translate_book(store, PROVIDER, _preset(), model=FunctionModel(flaky_fn), concurrency=1)
    )
    assert len(result["failed"]) == 1
    failed_id = result["failed"][0][0]
    failed_seg = store.get_segment(failed_id)
    assert failed_seg.status == SegmentStatus.FAILED
    assert failed_seg.error == "simulated 500"
    # Everything else in the batch still completed.
    assert result["segments"] == len(all_ids) - 1


def test_translate_book_flags_segments_that_fail_qa(store):
    """A segment the QA pass objects to lands as FLAGGED, with the reason
    recorded on it, rather than a plain TRANSLATED that looks fine at a glance.

    Uses a stray placeholder sentinel in the model's output (rather than an
    empty response) to trip the QA pass without also tripping pydantic-ai's
    own output validation, which independently rejects empty content."""
    from adib_engine.agents.placeholders import PLACEHOLDER_CLOSE, PLACEHOLDER_OPEN

    store.sync_segments(simple_book())
    all_ids = [s.id for s in store.segments()]

    def leaky_fn(messages: list[ModelMessage], info) -> ModelResponse:
        return ModelResponse(
            parts=[TextPart(content=f"{PLACEHOLDER_OPEN}9{PLACEHOLDER_CLOSE} translated text")]
        )

    result = asyncio.run(
        translate_book(store, PROVIDER, _preset(), model=FunctionModel(leaky_fn), concurrency=1)
    )
    assert result["segments"] == len(all_ids)
    for sid in all_ids:
        seg = store.get_segment(sid)
        assert seg.status == SegmentStatus.FLAGGED
        assert seg.qa_flags
        assert any(f["rule"] == "unresolved-placeholder" for f in seg.qa_flags)
