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
    BATCH_CLOSE,
    BATCH_OPEN,
    PLACEHOLDER_CLOSE,
    PLACEHOLDER_OPEN,
    build_batches,
    needs_model,
    parse_batch_output,
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
# Batching: packing, parsing, and the savings that justify both.
# ---------------------------------------------------------------------------


def _seg(i: int, text: str, path: list[str] | None = None) -> Segment:
    return Segment(
        id=f"seg-{i}", node_id=f"n{i}", ordinal=i, kind="paragraph",
        source_text=text, heading_path=path or [],
    )


def test_build_batches_packs_up_to_the_character_budget():
    segments = [_seg(i, "x" * 100) for i in range(10)]
    batches = build_batches(segments, max_chars=250, max_segments=99)
    assert [len(b) for b in batches] == [2, 2, 2, 2, 2]


def test_build_batches_honours_the_segment_count_cap():
    segments = [_seg(i, "tiny") for i in range(10)]
    batches = build_batches(segments, max_chars=10_000, max_segments=4)
    assert [len(b) for b in batches] == [4, 4, 2]


def test_build_batches_gives_an_oversized_segment_its_own_batch():
    segments = [_seg(0, "short"), _seg(1, "x" * 9000), _seg(2, "short")]
    batches = build_batches(segments, max_chars=1000, max_segments=99)
    assert [len(b) for b in batches] == [1, 1, 1]
    # Reading order is never disturbed by the packing.
    assert [s.id for b in batches for s in b] == ["seg-0", "seg-1", "seg-2"]


def test_build_batches_preserves_every_segment_exactly_once():
    segments = [_seg(i, "x" * (i * 37 % 400 + 1)) for i in range(50)]
    batches = build_batches(segments, max_chars=500, max_segments=7)
    assert [s.id for b in batches for s in b] == [s.id for s in segments]


def test_parse_batch_output_reads_markers_on_their_own_line():
    text = f"{BATCH_OPEN}#1{BATCH_CLOSE}\nیک\n{BATCH_OPEN}#2{BATCH_CLOSE}\nدو"
    assert parse_batch_output(text, 2) == {1: "یک", 2: "دو"}


def test_parse_batch_output_tolerates_translation_on_the_marker_line():
    text = f"{BATCH_OPEN}#1{BATCH_CLOSE} یک\n{BATCH_OPEN}#2{BATCH_CLOSE} دو"
    assert parse_batch_output(text, 2) == {1: "یک", 2: "دو"}


def test_parse_batch_output_reports_only_the_blocks_that_came_back():
    """A short response is not a parse failure — the caller retries the rest."""
    text = f"{BATCH_OPEN}#1{BATCH_CLOSE}\nیک\n{BATCH_OPEN}#3{BATCH_CLOSE}\nسه"
    assert parse_batch_output(text, 3) == {1: "یک", 3: "سه"}


def test_parse_batch_output_ignores_out_of_range_and_duplicate_markers():
    text = (
        f"{BATCH_OPEN}#1{BATCH_CLOSE}\nیک\n"
        f"{BATCH_OPEN}#1{BATCH_CLOSE}\nدوباره\n"
        f"{BATCH_OPEN}#9{BATCH_CLOSE}\nخارج"
    )
    assert parse_batch_output(text, 2) == {1: "یک"}


def test_parse_batch_output_tolerates_ascii_ified_markers():
    # Some models echo the marker back as ASCII `[[#N]]` instead of the exact
    # ⟦#N⟧ glyphs; that must still be treated as a delimiter, not leak into
    # a segment's translated body.
    text = "[[#1]]\nیک\n[[#2]]\nدو"
    assert parse_batch_output(text, 2) == {1: "یک", 2: "دو"}


def test_needs_model_is_false_for_segments_with_no_letters():
    assert not needs_model("3.")
    assert not needs_model("—")
    assert not needs_model("• ")
    assert needs_model("Chapter 3")
    assert needs_model("فصل")


def test_translate_book_sends_one_request_for_a_whole_batch(store):
    """The point of the exercise: N short segments cost one call, not N."""
    store.sync_segments(simple_book())
    total = len(store.segments())
    calls = {"n": 0}

    def counting_fn(messages: list[ModelMessage], info) -> ModelResponse:
        calls["n"] += 1
        return _echo_batch()(messages, info)

    result = asyncio.run(
        translate_book(store, PROVIDER, _preset(), model=FunctionModel(counting_fn), concurrency=1)
    )
    assert calls["n"] == 1
    assert result["segments"] == total
    assert store.pending_segments() == []


def test_translate_book_amortizes_the_prompt_preamble_across_the_batch(store):
    """The system prompt and instructions are sent once per batch, not per
    segment — that ratio is the whole token saving."""
    store.sync_segments(simple_book())
    prompts: list[str] = []

    def capturing_fn(messages: list[ModelMessage], info) -> ModelResponse:
        prompts.append(messages[-1].parts[-1].content)
        return _echo_batch()(messages, info)

    asyncio.run(
        translate_book(store, PROVIDER, _preset(), model=FunctionModel(capturing_fn), concurrency=1)
    )
    assert len(prompts) == 1
    # One instruction preamble carrying every source block.
    assert prompts[0].count("Translate all") == 1
    assert len(_blocks_in(prompts[0])) == len(store.segments())


def test_translate_book_splits_batch_token_usage_across_its_segments(store):
    """Per-segment cost must still add up to what the batch actually spent."""
    store.sync_segments(simple_book())
    result = asyncio.run(
        translate_book(store, PROVIDER, _preset(), model=FunctionModel(_upper_fn), concurrency=1)
    )
    segments = store.segments()
    assert sum(s.prompt_tokens + s.completion_tokens for s in segments) == result["tokens"]
    assert sum(s.cost_usd for s in segments) == pytest.approx(result["cost_usd"])


def test_translate_book_settles_untranslatable_segments_without_a_request(store):
    """A segment with no letters in it never reaches the model."""
    from adib_engine.models.document import NodeKind
    from tests.fixtures import node

    tree = simple_book()
    tree.nodes.append(node(NodeKind.PARAGRAPH, "42.", 99))
    store.sync_segments(tree)

    seen: list[str] = []

    def capturing_fn(messages: list[ModelMessage], info) -> ModelResponse:
        seen.append(messages[-1].parts[-1].content)
        return _echo_batch()(messages, info)

    result = asyncio.run(
        translate_book(store, PROVIDER, _preset(), model=FunctionModel(capturing_fn), concurrency=1)
    )
    assert result["skipped"] == 1
    assert all("42." not in prompt for prompt in seen)
    trivial = next(s for s in store.segments() if s.source_text == "42.")
    assert trivial.status == SegmentStatus.SKIPPED
    assert trivial.target_text == "42."
    assert trivial.cost_usd == 0.0


def test_translate_book_retries_only_the_blocks_a_batch_dropped(store):
    """A model that silently omits a block gets asked again for that block
    alone, rather than the batch being re-sent or the segment lost."""
    store.sync_segments(simple_book())
    dropped: dict[str, int] = {"n": 0}

    def forgetful_fn(messages: list[ModelMessage], info) -> ModelResponse:
        blocks = _blocks_in(messages[-1].parts[-1].content)
        # Lose the last block of the first (multi-block) response only.
        if len(blocks) > 1 and dropped["n"] == 0:
            dropped["n"] += 1
            blocks.pop(max(blocks))
        body = "\n".join(
            f"{BATCH_OPEN}#{i}{BATCH_CLOSE}\n[fa] {text}" for i, text in sorted(blocks.items())
        )
        return ModelResponse(parts=[TextPart(content=body)])

    result = asyncio.run(
        translate_book(store, PROVIDER, _preset(), model=FunctionModel(forgetful_fn), concurrency=1)
    )
    assert not result["failed"]
    assert store.pending_segments() == []


def test_translate_book_only_ids_leaves_other_pending_segments_alone(store):
    store.sync_segments(simple_book())
    all_ids = [s.id for s in store.segments()]
    target = all_ids[1]

    result = asyncio.run(
        translate_book(
            store, PROVIDER, _preset(), model=FunctionModel(_upper_fn), only_ids={target}
        )
    )
    assert result["queued"] == 1
    assert store.get_segment(target).target_text.startswith("[fa]")
    assert [s.id for s in store.pending_segments()] == [i for i in all_ids if i != target]


# ---------------------------------------------------------------------------
# translate_book: resumable orchestration against a real store.
# ---------------------------------------------------------------------------


def _blocks_in(prompt: str) -> dict[int, str]:
    """The numbered source blocks a batched prompt is carrying."""
    return parse_batch_output(prompt, count=10_000)


def _echo_batch(reply_for=lambda source: f"[fa] {source}"):
    """A model that answers a batched prompt in the batch protocol.

    Stands in for a well-behaved endpoint: every marker echoed exactly once, in
    order, with the block's translation under it.
    """

    def fn(messages: list[ModelMessage], info) -> ModelResponse:
        blocks = _blocks_in(messages[-1].parts[-1].content)
        body = "\n".join(
            f"{BATCH_OPEN}#{i}{BATCH_CLOSE}\n{reply_for(text)}"
            for i, text in sorted(blocks.items())
        )
        return ModelResponse(parts=[TextPart(content=body)])

    return fn


_upper_fn = _echo_batch()


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
        "skipped": 0,
        "requests": 0,
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
            batch_segments=1,  # one segment per request, so the pause lands early
            progress=completed.append,
            should_pause=lambda: len(completed) >= 1,
        )
    )

    assert result["paused"] is True
    assert 0 < result["segments"] < len(all_ids)
    assert store.pending_segments()  # something left for the next run


def test_translate_book_isolates_one_poisonous_segment_from_its_batch(store):
    """A segment the endpoint always chokes on must not take its batch with it.

    The batch is halved on failure until the bad segment stands alone, so it is
    the only one recorded as FAILED — everything packed alongside it still lands.
    """
    store.sync_segments(simple_book())
    all_ids = [s.id for s in store.segments()]
    # A body paragraph, not a heading: a heading also appears in every later
    # segment's "Section:" context line, which would poison unrelated batches.
    poison = "The stack is layered so each part can change independently."
    bad_id = next(s.id for s in store.segments() if s.source_text == poison)

    def flaky_fn(messages: list[ModelMessage], info) -> ModelResponse:
        prompt = messages[-1].parts[-1].content
        if poison in prompt:
            raise RuntimeError("simulated 500")
        return _echo_batch()(messages, info)

    result = asyncio.run(
        translate_book(store, PROVIDER, _preset(), model=FunctionModel(flaky_fn), concurrency=1)
    )
    assert len(result["failed"]) == 1
    failed_id = result["failed"][0][0]
    assert failed_id == bad_id
    failed_seg = store.get_segment(failed_id)
    assert failed_seg.status == SegmentStatus.FAILED
    assert failed_seg.error == "simulated 500"
    # Everything else survived the split-and-retry.
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

    leaky_fn = _echo_batch(
        lambda source: f"{PLACEHOLDER_OPEN}9{PLACEHOLDER_CLOSE} translated text"
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
