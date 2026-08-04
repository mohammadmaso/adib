from __future__ import annotations

from pathlib import Path

import pytest

from adib_engine.settings import RuntimeSettings, configure

TEST_TOKEN = "test-token"


@pytest.fixture
def settings(tmp_path: Path) -> RuntimeSettings:
    """Isolate each test in its own data dir so nothing touches the real app state."""
    return configure(
        RuntimeSettings(
            host="127.0.0.1",
            port=0,
            auth_token=TEST_TOKEN,
            data_dir=tmp_path / "adib",
            heartbeat_timeout=0.0,
        )
    )
