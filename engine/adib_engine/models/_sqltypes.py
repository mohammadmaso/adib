"""Custom SQLAlchemy column types.

SQLite has no timezone-aware datetime type: it stores whatever it is handed and
returns it naive. Mixing those naive values with aware ones (say, a timestamp
built from a file's mtime) raises `TypeError` on comparison, so every datetime
crossing this boundary is normalized to aware UTC in both directions.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime
from sqlalchemy.types import TypeDecorator


class UTCDateTime(TypeDecorator):
    """A datetime column that always reads back as timezone-aware UTC."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect) -> datetime | None:
        if value is None:
            return None
        # A naive value here is one we created ourselves; treat it as UTC rather
        # than guessing the local zone.
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    def process_result_value(self, value: datetime | None, dialect) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
