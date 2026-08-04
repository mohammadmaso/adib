"""Glossary pipeline: statistical mining + LLM adjudication.

`mine` runs first (cheap, no LLM) over the whole book; `agents.glossary` then
decides keep/drop per candidate and supplies the target translations and
explanations that Gate 2 shows the user.
"""

from adib_engine.glossary.mine import (
    GlossaryCandidate,
    mine_candidates,
)

__all__ = ["GlossaryCandidate", "mine_candidates"]
