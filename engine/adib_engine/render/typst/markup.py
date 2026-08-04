"""DocNode inline text -> Typst markup.

DocNode.text carries a small, fixed inline markdown dialect (`*em*`, `**strong**`,
`` `code` ``, `[text](url)`), the same dialect the translator is instructed to
preserve. Typst's own markup mode uses the same `*`/`_`/`` ` `` characters but
with different meaning (`*` is strong, `_` is emphasis), so this is a translation
between two markup languages, not a strip-and-reformat.
"""

from __future__ import annotations

import re

#: Typst's reserved markup characters, escaped wherever they appear as literal
#: text rather than as one of the inline patterns below.
_RESERVED = set("*_`#@$<>[]\\")

_STRONG = re.compile(r"\*\*(.+?)\*\*")
_EM = re.compile(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)")
_CODE = re.compile(r"`([^`]+)`")
_LINK = re.compile(r"\[([^\]]*)\]\(([^)]+)\)")

#: Sentinel wrapper so already-converted spans survive later passes untouched.
_PLACEHOLDER = "\x00{}\x00"


def escape_typst(text: str) -> str:
    """Escape characters that are markup syntax in Typst but plain text here."""
    return "".join(f"\\{c}" if c in _RESERVED else c for c in text)


def inline_to_typst(text: str) -> str:
    """Convert the app's inline markdown dialect into Typst markup.

    Processes code spans and links first (their contents must not be re-escaped
    or matched by the emphasis patterns), then strong/emphasis, escaping
    everything else exactly once.
    """
    if not text:
        return ""

    tokens: list[str] = []

    def stash(s: str) -> str:
        tokens.append(s)
        return _PLACEHOLDER.format(len(tokens) - 1)

    # Code spans: content is verbatim, wrapped in Typst's own backtick syntax.
    text = _CODE.sub(lambda m: stash(f"`{m.group(1)}`"), text)
    # Links: label escaped as text, URL passed through raw to #link().
    text = _LINK.sub(
        lambda m: stash(f'#link("{m.group(2)}")[{escape_typst(m.group(1))}]'), text
    )
    # Strong before emphasis, since ** would otherwise be seen as two *.
    text = _STRONG.sub(lambda m: stash(f"*{escape_typst(m.group(1))}*"), text)
    text = _EM.sub(lambda m: stash(f"_{escape_typst(m.group(1))}_"), text)

    text = escape_typst(text)

    for i, token in enumerate(tokens):
        text = text.replace(_PLACEHOLDER.format(i), token)
    return text


__all__ = ["escape_typst", "inline_to_typst"]
