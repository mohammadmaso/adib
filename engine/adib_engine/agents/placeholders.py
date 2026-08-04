"""The sentinel tokens `translate.py` wraps protected glossary terms in.

Split out from `translate.py` so `adib_engine.qa` (which needs to detect a
placeholder that never got restored) can import just the two characters
without importing the translator module itself and creating a cycle.
"""

from __future__ import annotations

#: ≧/≨ are U+2267 and U+2268 — extremely unlikely in real book text, safe
#: against collisions.
PLACEHOLDER_OPEN = "≧"
PLACEHOLDER_CLOSE = "≨"
