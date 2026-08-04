"""Deterministic QA rules: each one is a pure function over plain strings, so
these are exercised directly rather than through a translation run."""

from __future__ import annotations

from adib_engine.agents.placeholders import PLACEHOLDER_CLOSE, PLACEHOLDER_OPEN
from adib_engine.models.glossary import GlossaryTerm, TermPolicy
from adib_engine.models.segment import QASeverity
from adib_engine.qa.rules import (
    check_altered_numbers,
    check_empty_output,
    check_glossary_consistency,
    check_length_ratio,
    check_unresolved_placeholders,
    run_qa,
)


def test_empty_output_flags_blank_translation():
    flag = check_empty_output("Some real source text.", "")
    assert flag is not None
    assert flag.rule == "empty-output"
    assert flag.severity == QASeverity.ERROR


def test_empty_output_ignores_blank_source():
    assert check_empty_output("", None) is None


def test_empty_output_passes_nonblank_translation():
    assert check_empty_output("Hello.", "Salaam.") is None


def test_unresolved_placeholder_detected():
    target = f"The term {PLACEHOLDER_OPEN}3{PLACEHOLDER_CLOSE} was never restored."
    flags = check_unresolved_placeholders(target)
    assert len(flags) == 1
    assert flags[0].rule == "unresolved-placeholder"
    assert flags[0].detail["placeholders"] == [f"{PLACEHOLDER_OPEN}3{PLACEHOLDER_CLOSE}"]


def test_unresolved_placeholder_clean_text_passes():
    assert check_unresolved_placeholders("A perfectly normal sentence.") == []


def test_length_ratio_flags_severe_truncation():
    source = "A" * 200
    target = "B" * 10
    flag = check_length_ratio(source, target)
    assert flag is not None
    assert flag.rule == "length-ratio"
    assert flag.severity == QASeverity.WARNING


def test_length_ratio_passes_reasonable_translation():
    source = "This is a normal-length sentence with real content in it."
    target = "این یک جمله با طول معمولی و محتوای واقعی است."
    assert check_length_ratio(source, target) is None


def test_length_ratio_skips_very_short_segments():
    assert check_length_ratio("Hi.", "") is None


def test_altered_numbers_flags_missing_number():
    source = "The meeting is on 2024-05-01 and costs 42 dollars, quite a lot indeed."
    target = "The meeting is next month and costs some dollars, quite a lot indeed."
    flags = check_altered_numbers(source, target)
    assert len(flags) == 1
    assert "42" in flags[0].detail["missing"]
    assert "2024-05-01" in flags[0].detail["missing"]


def test_altered_numbers_passes_when_numbers_survive():
    source = "Chapter 12 begins on page 340, a long chapter by any measure."
    target = "Chapter 12 begins on page 340, a long chapter by any measure (translated)."
    assert check_altered_numbers(source, target) == []


def test_glossary_consistency_flags_missing_target_form():
    terms = [
        GlossaryTerm(
            id="t1", source="backpropagation", target="پس‌انتشار", policy=TermPolicy.TRANSLATE
        )
    ]
    source = "This chapter explains backpropagation in detail for beginners."
    target = "این فصل به تفصیل موضوع دیگری را برای مبتدیان توضیح می‌دهد."
    flags = check_glossary_consistency(source, target, terms)
    assert len(flags) == 1
    assert flags[0].rule == "glossary-inconsistency"
    assert flags[0].detail["expected_target"] == "پس‌انتشار"


def test_glossary_consistency_passes_when_target_form_present():
    terms = [
        GlossaryTerm(
            id="t1", source="backpropagation", target="پس‌انتشار", policy=TermPolicy.TRANSLATE
        )
    ]
    source = "This chapter explains backpropagation in detail for beginners."
    target = "این فصل به تفصیل پس‌انتشار را برای مبتدیان توضیح می‌دهد."
    assert check_glossary_consistency(source, target, terms) == []


def test_glossary_consistency_ignores_keep_policy_terms():
    """`keep`-policy terms are placeholder-protected before translation even
    starts, so their presence is structurally guaranteed — checking them here
    would be redundant with `check_unresolved_placeholders`."""
    terms = [GlossaryTerm(id="t1", source="TCP/IP", target=None, policy=TermPolicy.KEEP)]
    source = "The protocol TCP/IP underlies most of the internet, a fact worth noting."
    target = "این پروتکل زیربنای بیشتر اینترنت است، نکته‌ای شایان توجه."
    assert check_glossary_consistency(source, target, terms) == []


def test_run_qa_short_circuits_on_empty_output():
    flags = run_qa("A real sentence with content.", "")
    assert len(flags) == 1
    assert flags[0].rule == "empty-output"


def test_run_qa_aggregates_multiple_rules():
    terms = [
        GlossaryTerm(id="t1", source="widget", target="ابزارک", policy=TermPolicy.TRANSLATE)
    ]
    source = "The widget costs 99 dollars and comes with a two-year warranty included."
    target = f"{PLACEHOLDER_OPEN}0{PLACEHOLDER_CLOSE} چیز دیگری."
    flags = run_qa(source, target, glossary_terms=terms)
    rules = {f.rule for f in flags}
    assert "unresolved-placeholder" in rules
    assert "altered-number" in rules
    assert "glossary-inconsistency" in rules


def test_run_qa_clean_translation_has_no_flags():
    source = "This is a clean, ordinary sentence without numbers or glossary terms."
    target = "این یک جمله تمیز و معمولی بدون عدد یا اصطلاح واژه‌نامه است."
    assert run_qa(source, target) == []
