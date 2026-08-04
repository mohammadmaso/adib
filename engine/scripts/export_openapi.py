"""Dump the engine's OpenAPI schema to a file.

The Pydantic models in adib_engine.models are the single source of truth for the
API contract; this is the first half of propagating them to the TypeScript client
(the second half is `openapi-typescript`, run by scripts/generate-types.sh).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from adib_engine.api.app import create_app


def main(argv: list[str]) -> int:
    out = Path(argv[1]) if len(argv) > 1 else Path("openapi.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(create_app().openapi(), indent=2) + "\n", encoding="utf-8")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
