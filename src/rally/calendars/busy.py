"""Free/busy without the calendar: the computation behind the busy-only tools.

The busy-only MCP tools (``rally.routers.busy``) are built for an agent that
is not trusted: a phone assistant anyone can call and try to talk round. What
it is allowed to learn is decided here, and the answer is deliberately thin:

- **Merged blocks only.** Every occurrence, from every calendar Rally knows
  (native events with their recurrences expanded and overrides applied, and
  every external feed and CalDAV calendar from the sync cache), is reduced to
  a start and an end. Those are rounded *outward* to 15-minute boundaries and
  merged with anything they touch or overlap, so no title, description,
  location, attendee, uid, calendar name or count survives, and neither does
  the exact minute something starts or where one event ends and the next
  begins.
- **No network.** External calendars are read from ``calendar_cache`` only.
  The agent cannot make Rally fetch anything.
- **Fail closed.** An external calendar the sync has never stored is unknown
  time, and unknown time is not free time: the whole answer is refused
  (``AvailabilityUnavailable``) rather than reported free. A feed whose last
  sync failed still counts with its previous occurrences, as on the calendar
  page.

Member attribution is the calendar's own rule: an occurrence belongs to its
``attendees`` (explicit attendees of a native event, else the owner of the
calendar it sits on, unioned across feeds that carry the same event). An
occurrence that names nobody (a calendar with no owner) belongs to everyone,
so it is busy under every member filter. All-day events are busy for the
whole local day.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from rally.calendars.native import collect_native
from rally.calendars.occurrence import Occurrence, resolve_local
from rally.models import Calendar

GRANULARITY = timedelta(minutes=15)
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


class AvailabilityUnavailable(Exception):
    """Some calendar could not be read, so free time cannot be vouched for."""


@dataclass(frozen=True, slots=True)
class Block:
    start: datetime  # aware, UTC, on a 15-minute boundary
    end: datetime  # exclusive


def floor_slot(value: datetime) -> datetime:
    return _EPOCH + ((value - _EPOCH) // GRANULARITY) * GRANULARITY


def ceil_slot(value: datetime) -> datetime:
    return _EPOCH - ((_EPOCH - value) // GRANULARITY) * GRANULARITY


def _occurrences(
    db: Session, *, window_start: datetime, window_end: datetime, local_tz: ZoneInfo
) -> list[Occurrence]:
    from rally.calendars.cache import read_cached

    calendars = db.query(Calendar).all()
    native_ids = [c.id for c in calendars if (c.cal_type or "ics") == "native"]
    found: list[Occurrence] = []
    if native_ids:
        found.extend(
            collect_native(
                db,
                window_start=window_start,
                window_end=window_end,
                local_tz=local_tz,
                calendar_ids=native_ids,
            )
        )
    if any((c.cal_type or "ics") != "native" for c in calendars):
        cached, _failing, uncached = read_cached(
            db, window_start=window_start, window_end=window_end
        )
        if uncached:
            raise AvailabilityUnavailable
        found.extend(cached)
    return found


def _belongs_to(occurrence: Occurrence, members: set[str] | None) -> bool:
    if members is None:
        return True
    names = {n.strip().lower() for n in occurrence.attendees if n}
    if not names and occurrence.member:
        names = {occurrence.member.strip().lower()}
    # Nobody named: it is on a calendar that belongs to no one, so it blocks
    # everyone rather than no one.
    return not names or bool(names & members)


def merge_blocks(
    intervals: list[tuple[datetime, datetime]], *, window_start: datetime, window_end: datetime
) -> list[Block]:
    """Round outward to 15 minutes, clip to the (rounded) window, and merge."""
    lo, hi = floor_slot(window_start), ceil_slot(window_end)
    rounded = []
    for start, end in intervals:
        start, end = max(floor_slot(start), lo), min(ceil_slot(end), hi)
        if end > start:
            rounded.append((start, end))
    rounded.sort()
    merged: list[list[datetime]] = []
    for start, end in rounded:
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [Block(start, end) for start, end in merged]


def busy_blocks(
    db: Session,
    *,
    window_start: datetime,
    window_end: datetime,
    local_tz: ZoneInfo,
    members: set[str] | None = None,
) -> list[Block]:
    """Merged, rounded busy time in the window, for ``members`` (lowercased names) or all."""
    lo, hi = floor_slot(window_start), ceil_slot(window_end)
    occurrences = _occurrences(db, window_start=lo, window_end=hi, local_tz=local_tz)
    intervals = [
        (o.start, o.end)
        for o in occurrences
        if _belongs_to(o, members) and o.end > lo and o.start < hi
    ]
    return merge_blocks(intervals, window_start=lo, window_end=hi)


def free_blocks(busy: list[Block], *, window_start: datetime, window_end: datetime) -> list[Block]:
    """The complement of ``busy`` inside the window, on the same boundaries."""
    cursor, hi = ceil_slot(window_start), floor_slot(window_end)
    free = []
    for block in busy:
        if block.start > cursor:
            free.append(Block(cursor, min(block.start, hi)))
        cursor = max(cursor, block.end)
    if cursor < hi:
        free.append(Block(cursor, hi))
    return [b for b in free if b.end > b.start]


@dataclass(frozen=True)
class SlotConstraints:
    earliest: time = time(8, 0)  # local time of day a slot may start
    latest: time = time(18, 0)  # local time of day a slot must end by
    weekdays_only: bool = False
    buffer_minutes: int = 0  # kept clear before and after every busy block


def free_slots(
    busy: list[Block],
    *,
    window_start: datetime,
    window_end: datetime,
    duration: timedelta,
    local_tz: ZoneInfo,
    now: datetime,
    constraints: SlotConstraints,
    max_results: int,
) -> list[Block]:
    """The earliest non-overlapping slots of ``duration`` that fit the constraints.

    Slots start on 15-minute boundaries and run back to back inside a free
    gap ("9:00, 10:00 or 11:00"), never in the past, and never closer to a
    busy block than the buffer.
    """
    pad = timedelta(minutes=constraints.buffer_minutes)
    padded = merge_blocks(
        [(b.start - pad, b.end + pad) for b in busy],
        window_start=window_start - pad,
        window_end=window_end + pad,
    )
    step = ceil_slot(_EPOCH + duration) - _EPOCH
    lower = max(ceil_slot(window_start), ceil_slot(now))
    upper = floor_slot(window_end)
    slots: list[Block] = []
    day = window_start.astimezone(local_tz).date()
    last_day = window_end.astimezone(local_tz).date()
    while day <= last_day and len(slots) < max_results:
        if not (constraints.weekdays_only and day.weekday() >= 5):
            day_lo = ceil_slot(max(lower, _local(day, constraints.earliest, local_tz)))
            day_hi = floor_slot(min(upper, _local(day, constraints.latest, local_tz)))
            if day_hi > day_lo:
                for gap in free_blocks(padded, window_start=day_lo, window_end=day_hi):
                    start = gap.start
                    while start + duration <= gap.end and len(slots) < max_results:
                        slots.append(Block(start, start + duration))
                        start += step
        day += timedelta(days=1)
    return slots


def _local(day: date, at: time, tz: ZoneInfo) -> datetime:
    return resolve_local(datetime.combine(day, at), tz).astimezone(UTC)
