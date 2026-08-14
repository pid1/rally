"""Expand Rally-owned events into occurrences.

Native events are expanded through the *same* library as external feeds: the
row is synthesized into an in-memory VEVENT and handed to
``recurring_ical_events``. That is deliberate. Recurrence has to expand in
local wall time so a 7:00 PM weekly event stays at 7:00 PM across a DST
transition, and writing a second expander would mean owning that problem twice
— once here and once for ICS, which is where DST bugs come from.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import recurring_ical_events
from icalendar import Calendar as ICalCalendar
from icalendar import Event as ICalEvent
from icalendar.prop import vRecur
from sqlalchemy.orm import Session

from rally.calendars.occurrence import (
    SOURCE_NATIVE,
    Occurrence,
    all_day_bounds,
)
from rally.models import Calendar, Event, EventAttendee, EventOverride, FamilyMember

# A month view of an unbounded daily rule is 31 rows. A malformed INTERVAL, or
# a rule imported from somewhere less careful, is not — and a page that hangs
# is worse than a series that renders short.
MAX_OCCURRENCES_PER_EVENT = 1000


class RecurrenceError(ValueError):
    """A recurrence rule that cannot be parsed."""


def validate_rrule(rrule: str | None) -> str | None:
    """Normalise and validate an RRULE body, or raise ``RecurrenceError``.

    Accepts the value with or without the ``RRULE:`` prefix, since both forms
    turn up in the wild and the difference is not worth surfacing to a caller.
    """
    if rrule is None:
        return None
    text = rrule.strip()
    if not text:
        return None
    if text.upper().startswith("RRULE:"):
        text = text[len("RRULE:") :]
    try:
        vRecur.from_ical(text)
    except Exception as exc:  # icalendar raises bare ValueError subclasses
        raise RecurrenceError(f"Invalid recurrence rule: {exc}") from exc
    return text


def series_end_date(rrule: str | None) -> str | None:
    """The last local date a rule can produce, when it says so.

    Only ``UNTIL`` is read. ``COUNT`` also bounds a series, but resolving it to
    a date means expanding the rule, and the value is a denormalized hint for
    querying rather than a source of truth — the expansion is authoritative.
    """
    if not rrule:
        return None
    for part in rrule.split(";"):
        name, _, value = part.partition("=")
        if name.strip().upper() != "UNTIL":
            continue
        raw = value.strip()
        try:
            if raw.endswith("Z"):
                return datetime.strptime(raw, "%Y%m%dT%H%M%SZ").date().isoformat()
            if "T" in raw:
                return datetime.strptime(raw, "%Y%m%dT%H%M%S").date().isoformat()
            return datetime.strptime(raw, "%Y%m%d").date().isoformat()
        except ValueError:
            return None
    return None


def _series_component(event: Event, tz: ZoneInfo) -> ICalEvent:
    """Synthesize the VEVENT the expander will walk.

    All-day events get bare ``date`` values, exactly as ICS requires, so the
    expander produces dates back and the all-day path stays all-day end to end.
    """
    component = ICalEvent()
    component.add("uid", event.uid)
    component.add("summary", event.title)

    if event.all_day:
        start = date.fromisoformat(event.start_date)
        # DTEND is exclusive in ICS; end_date is the inclusive human date.
        end = date.fromisoformat(event.end_date) + timedelta(days=1)
    else:
        start = event.start_utc.replace(tzinfo=UTC).astimezone(tz)
        end = event.end_utc.replace(tzinfo=UTC).astimezone(tz)

    component.add("dtstart", start)
    component.add("dtend", end)
    if event.rrule:
        component.add("rrule", vRecur.from_ical(event.rrule))
    return component


def _occurrence_local_date(component, tz: ZoneInfo) -> str:
    value = component.get("dtstart").dt
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.date().isoformat()
        return value.astimezone(tz).date().isoformat()
    return value.isoformat()


def _apply_override(
    occurrence: Occurrence, override: EventOverride, tz: ZoneInfo
) -> Occurrence | None:
    """Fold a single-occurrence override onto an expanded occurrence.

    Returns ``None`` when the occurrence was cancelled. Every field is
    "inherit unless set", so a title-only override does not disturb the times.
    """
    if override.cancelled:
        return None

    changes: dict = {}
    if override.title is not None:
        changes["title"] = override.title
    if override.description is not None:
        changes["description"] = override.description
    if override.location is not None:
        changes["location"] = override.location

    if override.start_utc is not None and override.end_utc is not None:
        all_day = occurrence.all_day if override.all_day is None else override.all_day
        start = override.start_utc.replace(tzinfo=UTC)
        end = override.end_utc.replace(tzinfo=UTC)
        changes["all_day"] = all_day
        changes["start"] = start
        changes["end"] = end
        changes["start_local_date"] = override.start_date or start.astimezone(tz).date().isoformat()
        changes["end_local_date"] = override.end_date or changes["start_local_date"]

    return occurrence if not changes else _replace(occurrence, changes)


def _replace(occurrence: Occurrence, changes: dict) -> Occurrence:
    from dataclasses import replace

    return replace(occurrence, **changes)


def expand_event(
    event: Event,
    *,
    overrides: list[EventOverride],
    window_start: datetime,
    window_end: datetime,
    local_tz: ZoneInfo,
    calendar_label: str = "",
    member: str | None = None,
    member_color: str | None = None,
    attendees: tuple[str, ...] = (),
) -> list[Occurrence]:
    """Every occurrence of one event inside the window, overrides applied."""
    tz = ZoneInfo(event.tzid) if event.tzid else local_tz

    calendar = ICalCalendar()
    calendar.add_component(_series_component(event, tz))

    # A moved occurrence may land inside the window while its original date sits
    # outside it, so the expansion window is widened by the largest move we can
    # see before being narrowed again below.
    pad = timedelta(days=1)
    for override in overrides:
        if override.start_utc is None:
            continue
        moved = override.start_utc.replace(tzinfo=UTC)
        original = datetime.fromisoformat(override.occurrence_date).replace(tzinfo=UTC)
        pad = max(pad, abs(moved - original) + timedelta(days=1))

    expanded = recurring_ical_events.of(calendar).between(window_start - pad, window_end + pad)

    override_by_date = {override.occurrence_date: override for override in overrides}
    occurrences: list[Occurrence] = []

    for index, component in enumerate(expanded):
        if index >= MAX_OCCURRENCES_PER_EVENT:
            print(
                f"  Warning: event {event.id} ({event.title!r}) hit the "
                f"{MAX_OCCURRENCES_PER_EVENT}-occurrence cap; truncating"
            )
            break

        occurrence_date = _occurrence_local_date(component, tz)

        if event.all_day:
            start_day = date.fromisoformat(occurrence_date)
            span = (date.fromisoformat(event.end_date) - date.fromisoformat(event.start_date)).days
            start, end, start_local, end_local = all_day_bounds(
                start_day, start_day + timedelta(days=span + 1), local_tz
            )
        else:
            raw_start = component.get("dtstart").dt
            raw_end = component.get("dtend").dt
            start = raw_start.astimezone(UTC)
            end = raw_end.astimezone(UTC)
            start_local = start.astimezone(local_tz).date().isoformat()
            local_end = end.astimezone(local_tz)
            if local_end.time() == local_end.min.time() and end > start:
                local_end -= timedelta(seconds=1)
            end_local = local_end.date().isoformat()

        occurrence = Occurrence(
            uid=event.uid,
            source=SOURCE_NATIVE,
            title=event.title,
            start=start,
            end=end,
            start_local_date=start_local,
            end_local_date=end_local,
            all_day=bool(event.all_day),
            tzid=event.tzid or str(local_tz),
            description=event.description or "",
            location=event.location or "",
            calendar_id=event.calendar_id,
            calendar_label=calendar_label,
            member=member,
            attendees=attendees or ((member,) if member else ()),
            member_color=member_color,
            event_id=event.id,
            occurrence_date=occurrence_date,
            recurring=bool(event.rrule),
            editable=True,
            notify_minutes_before=event.notify_minutes_before,
        )

        override = override_by_date.get(occurrence_date)
        if override is not None:
            adjusted = _apply_override(occurrence, override, local_tz)
            if adjusted is None:
                continue
            occurrence = adjusted

        if occurrence.end <= window_start or occurrence.start >= window_end:
            continue
        occurrences.append(occurrence)

    return occurrences


def collect_native(
    db: Session,
    *,
    window_start: datetime,
    window_end: datetime,
    local_tz: ZoneInfo,
    calendar_ids: list[int] | None = None,
) -> list[Occurrence]:
    """Expand every native event the given calendars hold."""
    query = db.query(Event)
    if calendar_ids is not None:
        if not calendar_ids:
            return []
        query = query.filter(Event.calendar_id.in_(calendar_ids))
    events = query.all()
    if not events:
        return []

    event_ids = [event.id for event in events]
    overrides_by_event: dict[int, list[EventOverride]] = {}
    for override in db.query(EventOverride).filter(EventOverride.event_id.in_(event_ids)).all():
        overrides_by_event.setdefault(override.event_id, []).append(override)

    members = {member.id: member for member in db.query(FamilyMember).all()}
    attendee_rows = db.query(EventAttendee).filter(EventAttendee.event_id.in_(event_ids)).all()
    attendees_by_event: dict[int, list[str]] = {}
    for row in attendee_rows:
        member = members.get(row.family_member_id)
        if member:
            attendees_by_event.setdefault(row.event_id, []).append(member.name)

    calendars = {
        calendar.id: calendar
        for calendar in db.query(Calendar).filter(Calendar.cal_type == "native").all()
    }

    occurrences: list[Occurrence] = []
    for event in events:
        calendar = calendars.get(event.calendar_id)
        owner = members.get(calendar.family_member_id) if calendar else None
        occurrences.extend(
            expand_event(
                event,
                overrides=overrides_by_event.get(event.id, []),
                window_start=window_start,
                window_end=window_end,
                local_tz=local_tz,
                calendar_label=calendar.label if calendar else "Rally",
                member=owner.name if owner else None,
                member_color=owner.color if owner else None,
                attendees=tuple(attendees_by_event.get(event.id, [])),
            )
        )
    return occurrences
