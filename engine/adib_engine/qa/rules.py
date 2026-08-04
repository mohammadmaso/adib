"""Deterministic consistency checks run against every finished translation.

These are cheap, rule-based sanity checks — not a substitute for human review,
but the kind of mechanical slip (a dropped number, a term rendered two
different ways, a placeholder that never got restored) that is tedious to
catch by eye across a whole book and trivial to catch by regex. A segment
that trips any of these gets `SegmentStatus.FLAGGED` instead of `TRANSLATED`,
so Gate 3's "Flagged" filter surfaces exactly the segments worth a second
look.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from adib_engine.agents.placeholders import PLACEHOLDER_CLOSE, PLACEHOLDER_OPEN
from adib_engine.models.glossary import GlossaryTerm, TermPolicy
from adib_engine.models.segment import QAFlag, QASeverity

#: Below this many characters, length-ratio and number checks are too noisy
#: to be useful (a two-word heading can legitimately have any ratio).
_MIN_CHECK_LEN = 12

_NUMBER_RE = re.compile(r"\d[\d.,:/-]*\d|\d+")
_PLACEHOLDER_RE = re.compile(f"{re.escape(PLACEHOLDER_OPEN)}\\d+{re.escape(PLACEHOLDER_CLOSE)}")


def check_empty_output(source_text: str, target_text: str | None) -> QAFlag | None:
    if source_text.strip() and not (target_text or "").strip():
        return QAFlag(
            rule="empty-output", severity=QASeverity.ERROR, message="Translation is empty."
        )
    return None


def check_unresolved_placeholders(target_text: str) -> list[QAFlag]:
    """Every `≧n≨` protected-term marker should have been restored to real
    text by `translate.restore_spans`. One surviving in the final output means
    restoration silently failed for that term."""
    leftovers = _PLACEHOLDER_RE.findall(target_text)
    if not leftovers:
        return []
    return [
        QAFlag(
            rule="unresolved-placeholder",
            severity=QASeverity.ERROR,
            message=f"{len(leftovers)} protected-term placeholder(s) never got restored.",
            detail={"placeholders": leftovers},
        )
    ]


def check_length_ratio(
    source_text: str,
    target_text: str,
    *,
    min_ratio: float = 0.25,
    max_ratio: float = 4.0,
) -> QAFlag | None:
    """Flags translations wildly shorter or longer than the source.

    The bounds are deliberately loose — language pairs vary a lot in
    characters-per-word — this is meant to catch truncation or runaway
    repetition, not to police style.
    """
    if len(source_text) < _MIN_CHECK_LEN:
        return None
    ratio = len(target_text) / len(source_text)
    if min_ratio <= ratio <= max_ratio:
        return None
    return QAFlag(
        rule="length-ratio",
        severity=QASeverity.WARNING,
        message=f"Translation length ratio ({ratio:.2f}) is outside the expected range.",
        detail={
            "ratio": round(ratio, 3),
            "source_len": len(source_text),
            "target_len": len(target_text),
        },
    )


def check_altered_numbers(source_text: str, target_text: str) -> list[QAFlag]:
    """Numbers and simple dates are usually meant to carry over verbatim.
    Digit-style conversion (Latin -> Persian/Arabic glyphs) happens later, at
    render time, so at this stage a missing number is a real signal, not a
    false positive from script differences."""
    if len(source_text) < _MIN_CHECK_LEN:
        return []
    source_numbers = {n.strip(".,:/-") for n in _NUMBER_RE.findall(source_text)}
    source_numbers.discard("")
    missing = sorted(n for n in source_numbers if n not in target_text)
    if not missing:
        return []
    return [
        QAFlag(
            rule="altered-number",
            severity=QASeverity.WARNING,
            message=f"{len(missing)} number(s) from the source don't appear in the translation.",
            detail={"missing": missing},
        )
    ]


def check_glossary_consistency(
    source_text: str, target_text: str, terms: Sequence[GlossaryTerm]
) -> list[QAFlag]:
    """A glossary term the analyst approved for translation should render the
    same way everywhere. If the source term is present but its approved
    target form is not, this segment likely drifted from the glossary."""
    flags: list[QAFlag] = []
    for term in terms:
        if term.policy != TermPolicy.TRANSLATE or not term.target or not term.enabled:
            continue
        if term.source.lower() not in source_text.lower():
            continue
        if term.target.lower() not in target_text.lower():
            flags.append(
                QAFlag(
                    rule="glossary-inconsistency",
                    severity=QASeverity.WARNING,
                    message=f"Glossary term '{term.source}' should render as '{term.target}'.",
                    detail={"source": term.source, "expected_target": term.target},
                )
            )
    return flags


def run_qa(
    source_text: str,
    target_text: str | None,
    *,
    glossary_terms: Sequence[GlossaryTerm] = (),
) -> list[QAFlag]:
    """Run every rule and return the flags, worst severity implied by callers
    checking `severity`. An empty-output segment skips the other checks —
    there's nothing left to say about a translation that doesn't exist."""
    empty = check_empty_output(source_text, target_text)
    if empty:
        return [empty]

    assert target_text is not None  # narrowed by check_empty_output above
    flags: list[QAFlag] = []
    flags.extend(check_unresolved_placeholders(target_text))
    ratio_flag = check_length_ratio(source_text, target_text)
    if ratio_flag:
        flags.append(ratio_flag)
    flags.extend(check_altered_numbers(source_text, target_text))
    flags.extend(check_glossary_consistency(source_text, target_text, glossary_terms))
    return flags
