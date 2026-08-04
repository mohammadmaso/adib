"""Glossary pipeline tests: statistical mining + LLM adjudication with a model seam.

The adjudicator is tested with a deterministic `FunctionModel` (no network), so
the `model=` seam is essential and exercised; production uses the real bound
model via `build_model`.
"""

from __future__ import annotations

import asyncio

from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from adib_engine.agents.glossary import (
    GlossaryDecision,
    adjudicate_glossary,
    decisions_to_terms,
)
from adib_engine.glossary.mine import mine_candidates
from adib_engine.models.document import (
    BookMeta,
    DocNode,
    DocTree,
    NodeKind,
    make_node_id,
)
from adib_engine.models.glossary import TermPolicy
from adib_engine.models.project import ProviderSettings

PROVIDER = ProviderSettings(
    base_url="http://test", model="test", price_per_mtok_in=1.0, price_per_mtok_out=1.0
)


def _para(text: str, ordinal: int) -> DocNode:
    return DocNode(
        id=make_node_id(NodeKind.PARAGRAPH, text, ordinal), kind=NodeKind.PARAGRAPH, text=text
    )


def _code(text: str, ordinal: int) -> DocNode:
    return DocNode(
        id=make_node_id(NodeKind.CODE, text, ordinal),
        kind=NodeKind.CODE,
        text=text,
        attrs={"language": "python"},
    )


def _tech_tree() -> DocTree:
    return DocTree(
        meta=BookMeta(title="Networking", source_lang="en"),
        nodes=[
            _para(
                "The Transmission Control Protocol is the core of TCP/IP networking. "
                "TCP/IP relies on the Transmission Control Protocol to deliver segments "
                "reliably. Engineers study the Transmission Control Protocol deeply.",
                0,
            ),
            _para(
                "Deep neural networks achieved 98% accuracy. "
                "Their deep neural networks converged.",
                1,
            ),
            _code("def parse_packet(data: bytes):\n    return Packet.from_bytes(data)", 2),
            _para("The router forwards the frame over the wire with low latency.", 3),
        ],
    )


def _verdict_fn() -> FunctionModel:
    def fn(messages: list[ModelMessage], info) -> ModelResponse:
        return ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name="final_result",
                    args={
                        "decisions": [
                            {"index": 0, "kept": True, "target": "پروتکل کنترل انتقال",
                             "policy": "translate_paren", "explanation": "اصطلاح شبکه."},
                            {"index": 1, "kept": False},
                            {"index": 2, "kept": True, "target": "شبکه عصبی عمیق",
                             "explanation": "روش یادگیری ماشین."},
                        ],
                        "rationale": "keep the protocol term and the ML term; drop prose.",
                    },
                    tool_call_id="tc1",
                )
            ]
        )

    return FunctionModel(fn)


def test_mine_candidates_catches_technical_phrases():
    cands = mine_candidates(_tech_tree(), min_freq=2)
    sources = {c.source for c in cands}
    assert "Transmission Control Protocol" in sources
    assert "TCP/IP" in sources
    # "the router", "the frame", "over the wire" are stopword-heavy prose, not terms.
    assert not any("the frame" in s or "router forwards" in s for s in sources)
    # Every candidate has a first-seen node and a sample.
    for c in cands:
        assert c.first_seen_node
        assert c.sample


def test_mine_candidates_requires_min_frequency():
    cands = mine_candidates(_tech_tree(), min_freq=10)
    assert not cands  # nothing appears ten times in this tiny tree


def test_adjudicate_glossary_keeps_and_drops(tmp_path):
    cands = mine_candidates(_tech_tree(), min_freq=2)
    result = asyncio.run(
        adjudicate_glossary(
            cands, PROVIDER, target_lang="fa", default_policy=TermPolicy.TRANSLATE,
            model=_verdict_fn(),
        )
    )
    verdict = result.output
    kept = {d.index for d in verdict.decisions if d.kept}
    assert 0 in kept
    assert 1 not in kept
    assert 2 in kept
    assert result.prompt_tokens > 0
    assert result.completion_tokens > 0


def test_decisions_to_terms_maps_indexes_and_defaults():
    cands = mine_candidates(_tech_tree(), min_freq=2)
    decisions = [
        GlossaryDecision(index=0, kept=True, target="پروتکل کنترل انتقال"),
        # kept but no target -> falls back to the default policy.
        GlossaryDecision(index=1, kept=True),
        GlossaryDecision(index=2, kept=False),
    ]
    terms = decisions_to_terms(cands, decisions, default_policy=TermPolicy.FOOTNOTE)
    by_src = {t.source: t for t in terms}
    assert "Transmission Control Protocol" in by_src
    tcp = by_src["Transmission Control Protocol"]
    assert tcp.target == "پروتکل کنترل انتقال"
    # The kept-but-untargeted term falls back to source as target placeholder.
    kept_without_target = [t for t in terms if t.target is None]
    assert kept_without_target
    assert all(t.policy is TermPolicy.FOOTNOTE for t in kept_without_target)
    assert all(t.origin == "llm" for t in terms)


def test_candidates_from_a_noisy_tree_stay_small():
    """The subsumption+stopword filtering keeps a chatty book from exploding."""
    cands = mine_candidates(_tech_tree(), min_freq=2)
    assert len(cands) <= 12
