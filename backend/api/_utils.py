from __future__ import annotations

from datetime import UTC, datetime


def _ensure_utc(dt: datetime) -> datetime:
    """Attach UTC tz to naive datetimes coming back from SQLite.

    SQLite has no tz-aware datetime type — values stored via SQLAlchemy come
    back as naive `datetime`. Without an explicit offset, Pydantic serialises
    them as plain ISO strings which JavaScript interprets in *local* time,
    producing a phantom hours-ago drift for users outside UTC.
    """
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt
