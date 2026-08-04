from __future__ import annotations

from adib_engine.models.document import NodeKind
from adib_engine.segmentation import (
    apply_translations,
    build_segments,
    make_segment_id,
    markdown_to_table,
    split_long_text,
    table_to_markdown,
)
from tests.fixtures import simple_book


def test_build_segments_skips_code_and_covers_prose():
    specs = build_segments(simple_book())
    kinds = {s.kind for s in specs}

    assert NodeKind.CODE not in kinds, "code must never be sent to a translator"
    assert NodeKind.HEADING in kinds
    assert NodeKind.PARAGRAPH in kinds
    assert NodeKind.TABLE in kinds


def test_figure_caption_and_alt_become_their_own_segments():
    specs = build_segments(simple_book())
    texts = {s.source_text for s in specs}

    assert "A packet in flight" in texts
    assert "Diagram" in texts


def test_segments_carry_heading_path_for_context():
    specs = build_segments(simple_book())
    para = next(s for s in specs if s.source_text.startswith("The stack is layered"))

    # Nested under "Introduction" > "Layers": the translator needs both.
    assert para.heading_path == ["Introduction", "Layers"]


def test_ordinals_follow_reading_order():
    specs = build_segments(simple_book())
    assert [s.ordinal for s in specs] == sorted(s.ordinal for s in specs)


def test_segment_ids_are_stable_across_rebuilds():
    # The basis of resumability: same input, same ids, translations survive.
    assert [s.id for s in build_segments(simple_book())] == [
        s.id for s in build_segments(simple_book())
    ]


def test_changed_source_text_yields_a_different_id():
    # A stale translation must not be kept against edited source text.
    assert make_segment_id("n1", "hello", 0) != make_segment_id("n1", "hello there", 0)


def test_table_round_trips_through_markdown():
    tree = simple_book()
    table_node = next(n for n in tree.walk() if n.kind is NodeKind.TABLE)
    assert table_node.table is not None

    markdown = table_to_markdown(table_node.table)
    assert "| Layer | Purpose |" in markdown

    restored = markdown_to_table(markdown, table_node.table)
    assert restored.rows[1][0].text == "Transport"
    # Structure comes from the template, so header flags survive the round trip.
    assert restored.rows[0][0].is_header is True


def test_markdown_to_table_keeps_layout_when_model_drops_cells():
    tree = simple_book()
    table_node = next(n for n in tree.walk() if n.kind is NodeKind.TABLE)
    assert table_node.table is not None

    # A model that returns a truncated table must not shrink the real one.
    restored = markdown_to_table("| لایه | هدف |", table_node.table)
    assert len(restored.rows) == 3
    assert restored.rows[0][0].text == "لایه"
    assert restored.rows[2][0].text == "Network"  # untouched, not dropped


def test_split_long_text_breaks_on_sentences_under_limit():
    text = " ".join(f"This is sentence number {i}." for i in range(200))
    pieces = split_long_text(text, limit=200)

    assert len(pieces) > 1
    assert all(len(p) <= 200 for p in pieces)
    # No content may be lost in the split.
    assert "".join(pieces).replace(" ", "") == text.replace(" ", "")


def test_single_oversized_sentence_is_not_cut():
    monster = "x" * 5000
    assert split_long_text(monster, limit=100) == [monster]


def test_apply_translations_substitutes_text_and_captions():
    tree = simple_book()
    specs = build_segments(tree)
    translations = {s.id: f"FA:{s.source_text}" for s in specs}

    out = apply_translations(tree, translations)

    heading = next(n for n in out.walk() if n.kind is NodeKind.HEADING)
    assert heading.text == "FA:Introduction"

    figure = next(n for n in out.walk() if n.kind is NodeKind.FIGURE)
    assert figure.assets[0].caption == "FA:A packet in flight"

    code = next(n for n in out.walk() if n.kind is NodeKind.CODE)
    assert code.text == "print('hello')", "code must survive untouched"


def test_apply_translations_leaves_untranslated_nodes_alone():
    # A partially translated book must still render for preview.
    tree = simple_book()
    out = apply_translations(tree, {})

    assert out.model_dump() == tree.model_dump()


def test_apply_translations_rejoins_split_paragraphs_in_order():
    from adib_engine.models.document import DocTree
    from adib_engine.models.document import NodeKind as NK
    from tests.fixtures import node

    long_text = " ".join(f"Sentence {i}." for i in range(400))
    tree = DocTree(nodes=[node(NK.PARAGRAPH, long_text, 0)])

    specs = build_segments(tree)
    assert len(specs) > 1, "fixture must actually split"

    out = apply_translations(tree, {s.id: f"[{s.part}]" for s in specs})
    assert out.nodes[0].text == " ".join(f"[{i}]" for i in range(len(specs)))
