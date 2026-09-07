"""Turn iCalendar components into ``Occurrence`` objects.

Shared by both external adapters: an ICS feed is expanded locally with
``recurring_ical_events``, while a CalDAV server expands recurrences itself and
hands back plain components. Both end up in ``component_to_occurrence``, so
there is one interpretation of DTSTART/DTEND/all-day/floating-time rather than
one per transport.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import recurring_ical_events
from icalendar import Calendar as ICalCalendar

from rally.calendars.declined import is_event_declined
from rally.calendars.occurrence import (
    Occurrence,
    all_day_bounds,
    to_utc,
)


def _is_all_day(value) -> bool:
    """A DTSTART that is a bare ``date`` is an all-day event.

    ``datetime`` subclasses ``date``, so the order of these checks is the whole
    point: ``isinstance(dt, date)`` alone is true for both.
    """
    return isinstance(value, date) and not isinstance(value, datetime)


def _bounds(component):
    """The raw DTSTART/DTEND pair, filling in a missing end.

    An event may carry DURATION instead of DTEND, or neither. The RFC's default
    for a missing end is DTSTART for timed events and one day for all-day ones,
    which is also what renders sensibly.
    """
    dtstart_prop = component.get("dtstart")
    if dtstart_prop is None:
        return None, None
    start = dtstart_prop.dt

    dtend_prop = component.get("dtend")
    if dtend_prop is not None:
        return start, dtend_prop.dt

    duration_prop = component.get("duration")
    if duration_prop is not None:
        try:
            return start, start + duration_prop.dt
        except TypeError:
            pass

    if _is_all_day(start):
        return start, start + timedelta(days=1)
    return start, start


def component_to_occurrence(
    component,
    *,
    local_tz: ZoneInfo,
    source: str,
    uid_fallback: str = "",
    calendar_id: int | None = None,
    calendar_label: str = "",
    member: str | None = None,
    member_color: str | None = None,
    rrule: str | None = None,
    recurring: bool | None = None,
) -> Occurrence | None:
    """Convert one VEVENT into an ``Occurrence``, or ``None`` if unusable.

    ``rrule`` is supplied by the caller rather than read off ``component``:
    ``recurring_ical_events`` strips ``RRULE`` from the components it expands,
    so by the time an occurrence exists the rule is only knowable from the
    source document. A caller that cannot know it passes nothing.

    ``recurring`` defaults to "there is a rule", which is right whenever the
    rule is knowable. CalDAV is the case where it is not: the server expands
    remotely, so an instance arrives with no ``RRULE`` but *is* part of a
    series, and that caller says so explicitly.
    """
    start_raw, end_raw = _bounds(component)
    if start_raw is None:
        return None

    all_day = _is_all_day(start_raw)
    if all_day:
        end_day = end_raw if _is_all_day(end_raw) else start_raw + timedelta(days=1)
        start, end, start_local_date, end_local_date = all_day_bounds(start_raw, end_day, local_tz)
    else:
        start = to_utc(start_raw, local_tz)
        end = to_utc(end_raw, local_tz) if isinstance(end_raw, datetime) else start
        if end < start:
            end = start
        start_local_date = start.astimezone(local_tz).date().isoformat()
        # An event ending exactly at local midnight belongs to the day it began,
        # not to the next one: a 10 PM–midnight event is a Friday event.
        local_end = end.astimezone(local_tz)
        if local_end.time() == local_end.min.time() and end > start:
            local_end -= timedelta(seconds=1)
        end_local_date = local_end.date().isoformat()

    uid = str(component.get("uid", "") or uid_fallback or "")
    summary = str(component.get("summary", "Untitled Event"))

    return Occurrence(
        uid=uid,
        source=source,
        title=summary,
        start=start,
        end=end,
        start_local_date=start_local_date,
        end_local_date=end_local_date,
        all_day=all_day,
        tzid=str(local_tz),
        description=str(component.get("description", "") or ""),
        location=str(component.get("location", "") or ""),
        calendar_id=calendar_id,
        calendar_label=calendar_label,
        member=member,
        attendees=(member,) if member else (),
        member_color=member_color,
        recurring=bool(rrule) if recurring is None else recurring,
        rrule=rrule,
        editable=False,
    )


def _rrules_by_uid(calendar) -> dict[str, str]:
    """Every recurring VEVENT's rule, keyed by UID, read before expansion.

    The expander drops ``RRULE`` from the occurrences it produces, so this is
    the only point at which a feed's rule is still visible. CalDAV has no
    equivalent — the server expands remotely and Rally never sees the original
    component — so occurrences from that transport carry no rule at all.
    """
    rules: dict[str, str] = {}
    for component in calendar.walk("VEVENT"):
        rule = component.get("rrule")
        uid = str(component.get("uid", "") or "")
        if rule is None or not uid or uid in rules:
            continue
        try:
            rules[uid] = rule.to_ical().decode()
        except Exception:  # a rule we cannot render is one we cannot describe
            continue
    return rules


def occurrences_from_ical_text(
    text: str,
    *,
    window_start: datetime,
    window_end: datetime,
    local_tz: ZoneInfo,
    owner_email: str | None = None,
    source: str = "ics",
    calendar_id: int | None = None,
    calendar_label: str = "",
    member: str | None = None,
    member_color: str | None = None,
) -> list[Occurrence]:
    """Parse an ICS document and expand its recurrences across the window."""
    calendar = ICalCalendar.from_ical(text)
    rules_by_uid = _rrules_by_uid(calendar)
    expanded = recurring_ical_events.of(calendar).between(window_start, window_end)

    occurrences: list[Occurrence] = []
    for component in expanded:
        if is_event_declined(component, owner_email):
            continue
        occurrence = component_to_occurrence(
            component,
            local_tz=local_tz,
            source=source,
            calendar_id=calendar_id,
            calendar_label=calendar_label,
            member=member,
            member_color=member_color,
            rrule=rules_by_uid.get(str(component.get("uid", "") or "")),
        )
        if occurrence is not None:
            occurrences.append(occurrence)
    return occurrences


def occurrences_from_components(
    components,
    *,
    window_start: datetime,
    window_end: datetime,
    local_tz: ZoneInfo,
    owner_email: str | None = None,
    source: str = "ics",
    calendar_id: int | None = None,
    calendar_label: str = "",
    member: str | None = None,
    member_color: str | None = None,
) -> list[Occurrence]:
    """Convert already-expanded components, filtering to the window ourselves.

    CalDAV servers expand recurrences on request, but they answer a *date*
    range on their own terms; re-filtering on the instants we asked for is
    cheap and keeps both transports honest about the same boundaries.
    """
    occurrences: list[Occurrence] = []
    for component in components:
        if is_event_declined(component, owner_email):
            continue
        occurrence = component_to_occurrence(
            component,
            local_tz=local_tz,
            source=source,
            calendar_id=calendar_id,
            calendar_label=calendar_label,
            member=member,
            member_color=member_color,
            # No rule survives a server-side expansion, but an instance of a
            # series still carries the RECURRENCE-ID it was expanded to. That is
            # enough to say *that* it repeats, which is what lets the detail view
            # explain the missing schedule instead of silently omitting it. A
            # server that omits the property degrades to "not recurring", which
            # is exactly today's behaviour.
            recurring=component.get("recurrence-id") is not None,
        )
        if occurrence is None:
            continue
        if occurrence.end <= window_start or occurrence.start >= window_end:
            continue
        occurrences.append(occurrence)
    return occurrences
