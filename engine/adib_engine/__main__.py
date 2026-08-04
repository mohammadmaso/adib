"""Engine entrypoint.

Run standalone for development::

    uv run adib-engine --port 8765

Or as a Tauri sidecar, where the shell passes ``--port 0`` and reads the actual
bound port back from the ready line on stdout.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import socket
import sys
from pathlib import Path

import uvicorn

from adib_engine.api.app import create_app
from adib_engine.settings import RuntimeSettings, configure

# The shell scans stdout for this prefix to learn the port. Do not change it
# without updating apps/desktop/src-tauri/src/sidecar.rs.
READY_PREFIX = "ADIB_ENGINE_READY "


def _default_data_dir() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Adib"
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or str(Path.home())
        return Path(base) / "Adib"
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "adib"


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="adib-engine")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=0, help="0 binds an ephemeral port")
    p.add_argument(
        "--auth-token",
        default=os.environ.get("ADIB_AUTH_TOKEN", ""),
        help="shared secret required on every non-public request; empty disables auth",
    )
    p.add_argument("--data-dir", type=Path, default=None)
    p.add_argument(
        "--heartbeat-timeout",
        type=float,
        default=0.0,
        help="exit if no /health ping arrives within this many seconds (0 disables)",
    )
    p.add_argument(
        "--typst-bin",
        type=Path,
        default=None,
        help="path to the typst binary; defaults to 'typst' on PATH",
    )
    p.add_argument(
        "--fonts-dir",
        type=Path,
        default=None,
        help="directory of bundled OFL fonts for the PDF renderer",
    )
    p.add_argument("--log-level", default="info")
    return p.parse_args(argv)


def _bind(host: str, port: int) -> socket.socket:
    """Bind up front so we can report the real port before uvicorn starts serving."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((host, port))
    sock.listen(128)
    return sock


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=args.log_level.upper(),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        stream=sys.stderr,
    )

    sock = _bind(args.host, args.port)
    bound_host, bound_port = sock.getsockname()[:2]

    settings = configure(
        RuntimeSettings(
            host=bound_host,
            port=bound_port,
            auth_token=args.auth_token,
            data_dir=args.data_dir or _default_data_dir(),
            heartbeat_timeout=args.heartbeat_timeout,
            typst_bin=args.typst_bin,
            bundled_fonts_dir=args.fonts_dir,
        )
    )

    print(
        READY_PREFIX
        + json.dumps({"host": bound_host, "port": bound_port, "pid": os.getpid()}),
        flush=True,
    )

    config = uvicorn.Config(
        create_app(),
        log_config=None,
        log_level=args.log_level,
        access_log=False,
    )
    server = uvicorn.Server(config)
    server.run(sockets=[sock])
    logging.info("engine stopped (data dir: %s)", settings.data_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
