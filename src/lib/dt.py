"""Datetime helpers for normalizing timezone-aware values."""

from datetime import datetime, timezone


def to_utc(dt: datetime | None) -> datetime | None:
    """Return a timezone-aware UTC datetime.

    SQLite stores DateTime values as naive strings, so values loaded from the
    database may be offset-naive even when the column is defined with
    DateTime(timezone=True). This helper treats naive datetimes as UTC and
    converts aware datetimes to UTC, making them safe to compare with
    ``datetime.now(timezone.utc)``.
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)
