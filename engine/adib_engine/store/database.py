"""SQLite connection setup for a single `.adib` project file."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import Engine, event
from sqlmodel import SQLModel, create_engine

# These imports look unused but are load-bearing: importing the modules that
# declare table=True classes is what registers them on SQLModel.metadata, which
# create_all() reads. Drop them and opening a project silently creates an empty
# database.
from adib_engine.models import glossary as _glossary  # noqa: F401
from adib_engine.models import project as _project  # noqa: F401
from adib_engine.models import segment as _segment  # noqa: F401


def _configure_sqlite(dbapi_connection, _record) -> None:
    cursor = dbapi_connection.cursor()
    # WAL lets the UI read progress while a translation run writes segments.
    cursor.execute("PRAGMA journal_mode=WAL")
    # NORMAL is the right tradeoff here: a crash can lose the last commit or two,
    # and losing one segment costs one cheap retranslation.
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    # Wait rather than raising SQLITE_BUSY when a read overlaps a write burst.
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()


def create_project_engine(path: Path) -> Engine:
    """Open (creating if needed) the SQLite database for one project."""
    path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(
        f"sqlite:///{path}",
        # The engine writes segments from many asyncio tasks on a thread pool;
        # SQLite objects would otherwise be pinned to their creating thread.
        connect_args={"check_same_thread": False},
    )
    event.listen(engine, "connect", _configure_sqlite)
    SQLModel.metadata.create_all(engine)
    return engine
