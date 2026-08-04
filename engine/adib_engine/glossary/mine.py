"""Statistical glossary candidate mining — no LLM involved.

Cheap, catches almost everything a human glossary editor would have flagged:
capitalized/long multi-word phrases, acronyms, code identifiers, and repeated
rare n-grams, each with where it first appears and a sample sentence. The LLM
adjudicator (agents/glossary.py) then decides keep/drop and supplies the target
translation and explanation. Keeping the two apart means re-running the miner
costs nothing and the adjudicator only ever sees plausible candidates.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

from adib_engine.models.document import DocTree, NodeKind
from adib_engine.models.glossary import GlossaryCandidate

#: A "word" usable as part of a term: Latin/Cyrillic letters + digits + internal
#: hyphen/apostrophe. Arabic-script words are candidates too, but we keep those
#: solely for unusual n-grams (see below) rather than every capitalized word.
_WORD = re.compile(r"[A-Za-z][A-Za-z0-9'’-]*")

#: Acronyms: 2+ uppercase Latin letters, sometimes dotted or slash-separated
#: ("IP", "TCP/IP", "A.I."). The group is anchored on letter-run boundaries so a
#: single letter like "T" is never a candidate and "IP" inside "TCP/IP" isn't
#: surfaced twice.
_ACRONYM = re.compile(
    r"(?<![A-Z])"           # not preceded by an uppercase letter (avoid mid-run)
    r"(?:[A-Z]{2,}(?:/[A-Z]+)+|[A-Z](?:\.[A-Z])+\.?|[A-Z]{2,})\b"
)

#: Code identifiers: camelCase, PascalCase, snake_case, or dotted qualifiers.
_CODE = re.compile(r"\b(?:[a-z]+[A-Z][a-zA-Z]*|[a-z]+(?:_[a-z]+)+|[A-Za-z_][\w]*\.[\w.]+)\b")

#: Rare n-gram length — any term shorter than this is filtered out.
_MIN_TERM_LEN = 3

#: A candidate must appear this many times to be considered at all.
_MIN_FREQ = 2


@dataclass
class _Occurrence:
    node_id: str
    sample: str


def _sentence_around(text: str, match: re.Match[str], radius: int = 44) -> str:
    start = max(0, match.start() - radius)
    end = min(len(text), match.end() + radius)
    return (text[start:end].strip() or text).replace("\n", " ")


#: Leading determiners stripped from a capitalized phrase so "the Transmission
#: Control Protocol" yields "Transmission Control Protocol".
_DETERMINERS = {"The", "A", "An", "This", "That", "These", "Those", "Our", "Your", "Their", "Its"}

#: Words that never carry terminological weight; an n-gram containing one at the
#: edges (or more than two total) is not a term candidate.
_STOPWORDS = {
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "with", "by",
    "at", "from", "as", "is", "are", "was", "were", "it", "its", "this", "that",
    "these", "those", "his", "her", "their", "our", "your", "we", "they", "be",
    "being", "been", "have", "has", "had", "which", "not", "but", "then", "when",
}


def _strip_determiner(phrase: str) -> str:
    words = phrase.split()
    while words and words[0] in _DETERMINERS:
        words = words[1:]
    return " ".join(words)


def _capitalized_multiword(tree: DocTree) -> dict[str, int]:
    """Long capitalized phrases: "Transmission Control Protocol", not "the ..."."""
    counts: Counter[str] = Counter()
    for node in tree.walk():
        if node.text is None:
            continue
        for m in re.finditer(r"\b(?:[A-Z][a-z]+\s+){1,}[A-Z][a-z]+\b", node.text):
            phrase = _strip_determiner(m.group(0))
            if phrase and len(phrase.split()) >= 2 and len(phrase) >= _MIN_TERM_LEN:
                counts[phrase] += 1
    return dict(counts)


def _acronyms(tree: DocTree) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for node in tree.walk():
        if node.text is None:
            continue
        for m in _ACRONYM.finditer(node.text):
            counts[m.group(0).rstrip(".")] += 1
    return dict(counts)


def _code_identifiers(tree: DocTree) -> dict[str, int]:
    """Identifiers inside code blocks (which normalize to lowercase) and inline code."""
    counts: Counter[str] = Counter()
    for node in tree.walk():
        if node.kind is NodeKind.CODE:
            for m in _CODE.finditer(node.text or ""):
                counts[m.group(0)] += 1
        elif node.kind in (NodeKind.PARAGRAPH, NodeKind.HEADING):
            # only unambiguous camel/snake/dotted forms, avoiding prose words
            for m in re.finditer(
                r"\b(?:[a-z]+[A-Z][a-zA-Z]*|[a-z]+(?:_[a-z]+){1,}|[A-Za-z_][\w]*\.[\w.]+)\b",
                node.text or "",
            ):
                counts[m.group(0)] += 1
    return dict(counts)


def _rare_ngrams(tree: DocTree, *, min_freq: int = _MIN_FREQ) -> dict[str, int]:
    """Repeated n-grams (2..4 words) that look technical.

    Conservative by design: skips n-grams with a stopword anywhere (so "the
    frame", "and the" never surface) and n-grams that are strict substrings of a
    longer capitalized candidate already caught by `_capitalized_multiword`.
    Keeps the candidate list small and high-precision for the adjudicator.
    """
    counts: Counter[str] = Counter()
    for node in tree.walk():
        words = _WORD.findall(node.text or "")
        for n in (2, 3, 4):
            for i in range(len(words) - n + 1):
                gram = " ".join(words[i : i + n]).strip()
                if len(gram) < _MIN_TERM_LEN + n - 1:
                    continue
                gram_words = gram.split()
                if any(w.lower() in _STOPWORDS for w in gram_words):
                    continue
                counts[gram] += 1
    return {gram: c for gram, c in counts.items() if c >= min_freq}


def _drop_subsumed(candidates: list[tuple[str, int, str]]) -> list[tuple[str, int, str]]:
    """Drop a candidate whose text is a substring of a longer one (by length desc).

    "Transmission Control Protocol" subsumes "Control Protocol"; keeping only the
    longest, most specific surface form avoids table-fulls of near-duplicates.
    """
    kept: list[tuple[str, int, str]] = []
    ordered = sorted(candidates, key=lambda t: (len(t[0]), t[1]), reverse=True)
    for term, count, reason in ordered:
        if any(term != long and term in long for long, _c, _r in ordered):
            continue
        kept.append((term, count, reason))
    return kept


def _occurrences(tree: DocTree, term: str) -> list[_Occurrence]:
    occ: list[_Occurrence] = []
    needle = re.compile(re.escape(term), re.IGNORECASE)
    for node in tree.walk():
        if node.text is None:
            continue
        m = needle.search(node.text)
        if m:
            occ.append(_Occurrence(node_id=node.id, sample=_sentence_around(node.text, m)))
            if len(occ) >= 5:
                break
    return occ


def mine_candidates(
    tree: DocTree,
    *,
    min_freq: int = _MIN_FREQ,
    max_candidates: int = 400,
) -> list[GlossaryCandidate]:
    """Mine candidate glossary terms from the tree, highest-frequency first.

    Combines all four heuristics, deduping on source text and requiring a
    minimum frequency. The `reason` on each candidate tells the UI and the
    adjudicator why it was surfaced.
    """
    raw: dict[str, tuple[int, str]] = {}

    def add(term: str, count: int, reason: str) -> None:
        if not term or len(term) < _MIN_TERM_LEN:
            return
        prev_count, _prev_reason = raw.get(term, (0, ""))
        # Ties keep the strongest reason (later heuristics are weaker); a higher
        # count wins outright.
        if count > prev_count or term not in raw:
            raw[term] = (count, reason)

    for term, count in _capitalized_multiword(tree).items():
        add(term, count, "capitalized_phrase")
    for term, count in _acronyms(tree).items():
        add(term, count, "acronym")
    for term, count in _code_identifiers(tree).items():
        add(term, count, "code_span")
    for term, count in _rare_ngrams(tree).items():
        add(term, count, "rare_ngram")

    # Drop strict substrings of a longer, more specific surface form.
    deduped = _drop_subsumed([(t, c, r) for t, (c, r) in raw.items()])
    raw = {t: (c, r) for t, c, r in deduped}

    candidates: list[GlossaryCandidate] = []
    for term, (count, reason) in sorted(raw.items(), key=lambda kv: (-kv[1][0], kv[0])):
        if count < min_freq:
            continue
        occs = _occurrences(tree, term)
        if not occs:
            continue
        candidates.append(
            GlossaryCandidate(
                source=term,
                frequency=count,
                first_seen_node=occs[0].node_id,
                reason=reason,
                sample=occs[0].sample,
            )
        )
        if len(candidates) >= max_candidates:
            break

    return candidates
