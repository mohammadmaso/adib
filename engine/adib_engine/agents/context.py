"""Sampling helpers: pick a stratified slice of the tree for the analyst.

The analyst does not need the whole book — a front-matter excerpt plus the first,
middle, and last page of several chapters is enough to detect language, tone, and
register, and it keeps the analysis call cheap. Sample size is bounded in
tokens by a cheap whitespace-split estimate rather than a real tokenizer, so the
caller keeps one knob (`max_chars`).
"""

from __future__ import annotations

from collections.abc import Iterator

from adib_engine.models.document import DocNode, DocTree, NodeKind


def stratified_sample(tree: DocTree, *, max_chars: int = 12_000) -> str:
    """A book-ish excerpt for the analyst: front matter + spread-out prose.

    Walks top-level blocks in reading order. It aims to be representative
    (opening, a middle chapter, the tail) without ever exceeding `max_chars`,
    favouring headings and paragraph starts so the sample reads like the book.
    """
    blocks = _top_level_text_blocks(tree)
    if not blocks:
        return _safe_truncate(tree.meta.title or "", max_chars)

    out: list[str] = []
    used: set[int] = set()
    budget = max_chars
    sep_len = len("\n\n")

    def push(index: int) -> None:
        nonlocal budget
        if index in used or budget <= 0:
            return
        # Account for the "\n\n" that will join this block to the previous one.
        cost = sep_len if out else 0
        if cost >= budget:
            return
        text = _safe_truncate(blocks[index][1], budget - cost)
        if text:
            out.append(text)
            used.add(index)
            budget -= cost + len(text)

    # Headings are cheap and carry the skeleton; paragraph starts carry voice.
    push(0)
    if len(blocks) > 1:
        push(len(blocks) - 1)
    # A couple from the middle—far enough apart to not mirror each other.
    mid = (len(blocks) - 1) // 2
    if 0 < mid < len(blocks) - 1:
        push(mid)
    if budget > 0:
        for i in range(len(blocks)):
            push(i)
            if budget <= 0:
                break
    return "\n\n".join(out)


def _top_level_text_blocks(tree: DocTree) -> list[tuple[NodeKind, str]]:
    blocks: list[tuple[NodeKind, str]] = []
    for node in tree.nodes:
        if node.kind is NodeKind.HEADING and node.level == 1:
            blocks.append((node.kind, (node.text or "").strip()))
        elif node.kind is NodeKind.PARAGRAPH:
            text = (node.text or "").strip()
            if text:
                blocks.append((node.kind, text))
    return blocks


def _safe_truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 3].rstrip() + "…"


def iter_nodes_for_translation(tree: DocTree) -> Iterator[DocNode]:
    """Skips code/equations/page-breaks; those are protected, never translated."""
    for node in tree.walk():
        if node.kind not in (NodeKind.CODE, NodeKind.EQUATION, NodeKind.PAGE_BREAK):
            yield node
