"""Turn what a form submits into what the ``events`` table stores.

The API accepts **local** wall times and a zone name, never a UTC instant: the
browser should not be doing timezone arithmetic, and a client that guesses
wrong produces an event that is silently an hour out. Every conversion from
"what the family typed" to "what is stored" happens here, once.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from rally.calendars.occurrence import all_day_bounds, resolve_local


class EventTimeError(ValueError):
    """A start/end pair that cannot be interpreted."""


def _parse_local(value: str, *, all_day: bool) -> date | datetime:
    text = (value or "").strip()
    if not text:
        raise EventTimeError("Start and end are required")
    if all_day:
        try:
            return date.fromisoformat(text[:10])
        except ValueError as exc:
            raise EventTimeError(f"Invalid date {value!r}") from exc
    try:
        # An HTML datetime-local field submits "YYYY-MM-DDTHH:MM"; seconds and
        # a trailing "Z" both turn up from scripted clients.
        return datetime.fromisoformat(text.replace("Z", ""))
    except ValueError as exc:
        raise EventTimeError(f"Invalid date/time {value!r}") from exc


def resolve_event_times(
    *,
    start: str,
    end: str | None,
    all_day: bool,
    tzid: str,
) -> dict:
    """Normalize a submitted start/end pair into stored columns.

    Returns the four values the row carries: the exclusive UTC instants and the
    inclusive local dates. For an all-day event the submitted ``end`` is the
    **inclusive** last day, because that is what the form means; the exclusive
    form only exists inside the instants.
    """
    try:
        tz = ZoneInfo(tzid)
    except Exception as exc:
        raise EventTimeError(f"Unknown timezone {tzid!r}") from exc

    if all_day:
        start_day = _parse_local(start, all_day=True)
        end_day = _parse_local(end, all_day=True) if end else start_day
        if end_day < start_day:
            raise EventTimeError("End date is before the start date")
        start_utc, end_utc, start_date, end_date = all_day_bounds(
            start_day, end_day + timedelta(days=1), tz
        )
        return {
            "all_day": True,
            "start_utc": start_utc.replace(tzinfo=None),
            "end_utc": end_utc.replace(tzinfo=None),
            "start_date": start_date,
            "end_date": end_date,
            "tzid": tzid,
        }

    start_local = _parse_local(start, all_day=False)
    end_local = _parse_local(end, all_day=False) if end else start_local + timedelta(hours=1)
    if end_local < start_local:
        raise EventTimeError("End time is before the start time")

    start_utc = resolve_local(start_local, tz)
    end_utc = resolve_local(end_local, tz)

    local_end = end_utc.astimezone(tz)
    # An event ending exactly at local midnight belongs to the day it started.
    if local_end.time() == local_end.min.time() and end_utc > start_utc:
        local_end -= timedelta(seconds=1)

    return {
        "all_day": False,
        "start_utc": start_utc.astimezone(ZoneInfo("UTC")).replace(tzinfo=None),
        "end_utc": end_utc.astimezone(ZoneInfo("UTC")).replace(tzinfo=None),
        "start_date": start_utc.astimezone(tz).date().isoformat(),
        "end_date": local_end.date().isoformat(),
        "tzid": tzid,
    }


def local_form_values(event, tz: ZoneInfo) -> tuple[str, str]:
    """The inverse: stored columns back into what the edit form displays."""
    if event.all_day:
        return event.start_date, event.end_date
    from datetime import UTC

    start = event.start_utc.replace(tzinfo=UTC).astimezone(tz)
    end = event.end_utc.replace(tzinfo=UTC).astimezone(tz)
    return start.strftime("%Y-%m-%dT%H:%M"), end.strftime("%Y-%m-%dT%H:%M")
