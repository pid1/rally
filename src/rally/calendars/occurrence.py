"""The one event shape every calendar source produces and every consumer reads.

Before this module, calendar events traveled between the ICS reader, the
CalDAV reader and the generator as a bare dict whose ``time`` was already
*formatted for display* (``"7:00 PM CDT"``). That shape made four bugs
unavoidable rather than merely possible:

- ordering was done on the formatted string, so ``"9:00 AM"`` sorted after
  ``"10:00 AM"`` and after ``"1:00 PM"``;
- an all-day event took the same formatting branch (a ``date`` also has
  ``strftime``) and reached the LLM as a midnight appointment;
- there was no end time at all, so nothing could span midnight;
- and dedupe keyed on ``(date, title)``, which silently dropped the second of
  two same-named events on one day.

``Occurrence`` carries real instants, an explicit ``all_day`` flag and an
inclusive local end date, so each of those is a matter of reading the right
field instead of re-deriving it from a string.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

# Sources, in the order the merge layer prefers them when the same event
# arrives from two places. Native wins because it is the only editable one.
SOURCE_NATIVE = "native"
SOURCE_ICS = "ics"
SOURCE_CALDAV_GOOGLE = "caldav_google"
SOURCE_CALDAV_APPLE = "caldav_apple"

_SOURCE_RANK = {SOURCE_NATIVE: 0}


@dataclass(frozen=True, slots=True)
class Occurrence:
    """A single dated instance of an event, from any source.

    One occurrence of a recurring series is one ``Occurrence``; the series it
    came from is identified by ``uid`` plus ``occurrence_date``.

    ``start`` and ``end`` are always timezone-aware UTC instants and ``end`` is
    **exclusive**, matching ICS. ``start_local_date`` and ``end_local_date``
    are the human-facing dates in the family's configured zone, and
    ``end_local_date`` is **inclusive** — a one-day event has both equal. Those
    two representations of "when it ends" disagree by a day on purpose; see
    ``docs`` in the issue. Anything that renders a date reads the local fields
    and never converts, which is what keeps an all-day event off the wrong day.
    """

    uid: str
    source: str
    title: str
    start: datetime
    end: datetime
    start_local_date: str
    end_local_date: str
    all_day: bool = False
    tzid: str = "UTC"
    description: str = ""
    location: str = ""
    calendar_id: int | None = None
    calendar_label: str = ""
    member: str | None = None
    # Everyone this occurrence belongs to: the union of the member names whose
    # calendars carried it, plus the explicit attendees of a native event.
    attendees: tuple[str, ...] = ()
    # Native events only. ``occurrence_date`` is the local date of the
    # *original* occurrence in the series, which is the identity an override
    # is keyed on — a moved occurrence keeps the date it was moved from.
    event_id: int | None = None
    occurrence_date: str | None = None
    recurring: bool = False
    editable: bool = False
    notify_minutes_before: int | None = None
    member_color: str | None = None

    @property
    def dedupe_key(self) -> tuple:
        """Identity for cross-calendar dedupe.

        UID first: the same event on two family members' feeds carries one UID,
        and that is the only signal that survives a round trip through Google
        or Apple. Falling back to the instants plus the title keeps events that
        genuinely came from two unrelated calendars from colliding — and, since
        the key carries ``start``, two same-named events on one day at
        different times stay two events.
        """
        if self.uid:
            return ("uid", self.uid.strip().lower(), self.start, self.end)
        return ("fuzzy", self.title.strip().lower(), self.start, self.end)

    @property
    def sort_key(self) -> tuple:
        """Chronological, with all-day events first within their day."""
        return (self.start, 0 if self.all_day else 1, self.title.strip().lower())

    def local_start(self, tz: ZoneInfo) -> datetime:
        """The start instant in the family's zone."""
        return self.start.astimezone(tz)

    def local_end(self, tz: ZoneInfo) -> datetime:
        return self.end.astimezone(tz)

    def time_label(self, tz: ZoneInfo) -> str:
        """Display time, or ``"All day"``.

        An all-day event has no time of day, so formatting one is always a bug
        — this is the single place that decision is made.
        """
        if self.all_day:
            return "All day"
        return self.local_start(tz).strftime("%I:%M %p").lstrip("0")

    def spans_days(self) -> bool:
        return self.start_local_date != self.end_local_date


def resolve_local(naive: datetime, tz: ZoneInfo) -> datetime:
    """Attach ``tz`` to a naive local wall time, resolving DST edge cases.

    Two local times a year are not ordinary:

    - **Nonexistent** (2:30 AM on the US spring-forward date). Shifted forward
      by the length of the gap, so 2:30 becomes 3:30 — what mainstream
      calendars do, and what keeps a 30-minute event 30 minutes long.
    - **Ambiguous** (1:30 AM on the fall-back date, which happens twice).
      Resolved to the first, pre-transition instant (``fold=0``).

    ``zoneinfo`` resolves both silently and without complaint, so doing nothing
    is also a policy — just an undocumented one that differs between the two
    cases. This makes it explicit and testable.
    """
    aware = naive.replace(tzinfo=tz, fold=0)
    # A local time is nonexistent exactly when a UTC round trip does not land
    # back on it: the offset used to leave differs from the offset in force on
    # arrival.
    if aware.astimezone(UTC).astimezone(tz).replace(tzinfo=None) != naive:
        gap = aware.utcoffset() - naive.replace(tzinfo=tz, fold=1).utcoffset()
        return (naive + abs(gap)).replace(tzinfo=tz, fold=0)
    return aware


def local_midnight_utc(day: date, tz: ZoneInfo) -> datetime:
    """The UTC instant of local midnight on ``day``.

    All-day events are anchored here rather than at UTC midnight: storing a
    birthday as ``00:00Z`` puts it on the previous day for every family west of
    Greenwich.
    """
    return resolve_local(datetime.combine(day, time.min), tz).astimezone(UTC)


def to_utc(value: datetime, tz: ZoneInfo) -> datetime:
    """Normalize a datetime to UTC, treating a naive one as local wall time.

    ICS calls a naive datetime a *floating* time — one that means the same
    clock reading wherever it is read. Rally has exactly one place, so the
    family's configured zone is the only sensible reading.
    """
    if value.tzinfo is None:
        return resolve_local(value, tz).astimezone(UTC)
    return value.astimezone(UTC)


def all_day_bounds(
    start_day: date, end_day_exclusive: date, tz: ZoneInfo
) -> tuple[datetime, datetime, str, str]:
    """Instants and local dates for an all-day span.

    ``end_day_exclusive`` follows ICS ``DTEND``: a one-day event on the 14th
    ends on the 15th. The returned local end date is the inclusive one, because
    that is what a human means and what the edit form shows. Converting between
    the two exactly once, here, is what keeps the classic off-by-one out of
    every other module.
    """
    if end_day_exclusive <= start_day:
        end_day_exclusive = start_day + timedelta(days=1)
    return (
        local_midnight_utc(start_day, tz),
        local_midnight_utc(end_day_exclusive, tz),
        start_day.isoformat(),
        (end_day_exclusive - timedelta(days=1)).isoformat(),
    )


def dates_covered(occurrence: Occurrence) -> list[str]:
    """Every local date this occurrence appears on, inclusive of both ends."""
    start = date.fromisoformat(occurrence.start_local_date)
    end = date.fromisoformat(occurrence.end_local_date)
    if end < start:
        end = start
    span = (end - start).days
    return [(start + timedelta(days=offset)).isoformat() for offset in range(span + 1)]


def source_rank(source: str) -> int:
    """Preference order for choosing a representative among duplicates."""
    return _SOURCE_RANK.get(source, 1)


@dataclass
class FetchResult:
    """Occurrences plus the sources that failed, so callers can degrade well.

    A feed being down must never fail a page or a summary — it must produce a
    shorter list and a note, which is only possible if the failure travels
    alongside the data instead of as an exception.
    """

    occurrences: list[Occurrence] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
