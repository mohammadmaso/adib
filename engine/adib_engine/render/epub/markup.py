"""DocNode inline text -> XHTML.

Same inline dialect as the Typst renderer (`*em*`, `**strong**`, `` `code` ``,
`[text](url)`), converted to the equivalent tags instead of Typst markup.
"""

from __future__ import annotations

import html
import re

_STRONG = re.compile(r"\*\*(.+?)\*\*")
_EM = re.compile(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)")
_CODE = re.compile(r"`([^`]+)`")
_LINK = re.compile(r"\[([^\]]*)\]\(([^)]+)\)")

_PLACEHOLDER = "\x00{}\x00"


def inline_to_xhtml(text: str) -> str:
    """Convert the app's inline markdown dialect into XHTML, escaping the rest."""
    if not text:
        return ""

    tokens: list[str] = []

    def stash(s: str) -> str:
        tokens.append(s)
        return _PLACEHOLDER.format(len(tokens) - 1)

    text = _CODE.sub(lambda m: stash(f"<code>{html.escape(m.group(1))}</code>"), text)
    text = _LINK.sub(
        lambda m: stash(f'<a href="{html.escape(m.group(2))}">{html.escape(m.group(1))}</a>'),
        text,
    )
    text = _STRONG.sub(lambda m: stash(f"<strong>{html.escape(m.group(1))}</strong>"), text)
    text = _EM.sub(lambda m: stash(f"<em>{html.escape(m.group(1))}</em>"), text)

    text = html.escape(text)

    for i, token in enumerate(tokens):
        text = text.replace(html.escape(_PLACEHOLDER.format(i)), token)
    return text


__all__ = ["inline_to_xhtml"]
