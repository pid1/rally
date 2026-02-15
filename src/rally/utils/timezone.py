"""Timezone utilities for Rally.

Provides timezone-aware datetime functions to ensure consistent behavior
regardless of server timezone configuration.
"""

from datetime import UTC, datetime


def now_utc() -> datetime:
    """Get current time as timezone-aware UTC datetime.

    Returns:
        datetime: Current time in UTC with timezone info.
    """
    return datetime.now(UTC)


def today_utc():
    """Get today's date in UTC.

    Returns:
        date: Today's date in UTC timezone.
    """
    return now_utc().date()


def ensure_utc(dt: datetime) -> datetime:
    """Ensure datetime is timezone-aware and in UTC.

    Args:
        dt: A datetime that may be naive or timezone-aware.

    Returns:
        datetime: Timezone-aware datetime in UTC.

    Note:
        Naive datetimes are assumed to be UTC.
    """
    if dt.tzinfo is None:
        # Naive datetime - assume UTC
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)
