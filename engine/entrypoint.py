"""PyInstaller's entry script.

A plain script rather than pointing Analysis at the installed `adib-engine`
console-script shim in `.venv/bin/` — that shim's shebang and path are tied to
this machine's venv layout, which is exactly what freezing is supposed to
leave behind.
"""

import os
import sys

# Must be set before pydantic is imported (transitively, via fastapi below).
# Pydantic's plugin system (here triggered by `logfire`, a pydantic-ai
# dependency) calls `inspect.getsource()` at class-creation time to patch
# validators — which has no `.py` source to read inside a frozen bundle and
# raises `OSError: could not get source code`. We don't use any pydantic
# plugin, so disabling the mechanism entirely is correct, not a workaround.
os.environ.setdefault("PYDANTIC_DISABLE_PLUGINS", "1")

from adib_engine.__main__ import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
