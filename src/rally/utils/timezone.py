"""Timezone utilities for Rally.

Provides timezone-aware datetime functions to ensure consistent behavior
regardless of server timezone configuration.
"""

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo


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


def today_local(tz_name: str = "UTC") -> date:
    """Get today's date in the user's configured timezone.

    Args:
        tz_name: IANA timezone name (e.g. "America/Chicago"). Defaults to UTC.

    Returns:
        date: Today's date in the specified timezone.
    """
    return now_utc().astimezone(ZoneInfo(tz_name)).date()


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
