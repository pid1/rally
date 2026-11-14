"""An event's reminders: what they are, when each is due, and where they live (#336).

An event has up to ``MAX_REMINDERS`` reminders. A timed event's reminder is a
number of minutes before its start; an all-day event's is a number of days
before its first day, at a time of day — counting minutes back from an all-day
event's midnight start fired them at midnight. Each is at most
``MAX_LEAD_DAYS`` ahead, which is also how far the due-reminder scan expands.

The series' reminders are ``event_reminders`` rows with no ``occurrence_date``.
An occurrence whose override has ``own_reminders`` reads only the rows on its
own original date instead: a whole list of its own, which a later change to
the series' reminders does not reach, the way an override's moved time stays
moved.

Every label is worded by ``rally.utils.display``; nothing here formats a date
or a time itself.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from rally.models import EventOverride, EventReminder
from rally.utils.display import MINUTES_PER_DAY, lead_days_label, lead_time_label

MAX_REMINDERS = 5
MAX_LEAD_DAYS = 35
MAX_LEAD_MINUTES = MAX_LEAD_DAYS * MINUTES_PER_DAY


class ReminderError(ValueError):
    """A reminder list the API refuses, with words a person can act on."""


@dataclass(frozen=True)
class Reminder:
    """One reminder, timed (``minutes_before``) or all-day (``days_before`` at ``at_time``)."""

    minutes_before: int | None = None
    days_before: int | None = None
    at_time: str | None = None  # HH:MM, local

    @property
    def all_day(self) -> bool:
        return self.days_before is not None

    @property
    def key(self) -> str:
        """Its identity on a list and in the send-once record: ``m60``, ``d1@09:00``."""
        if self.all_day:
            return f"d{self.days_before}@{self.at_time}"
        return f"m{self.minutes_before}"

    @property
    def label(self) -> str:
        if self.all_day:
            return lead_days_label(self.days_before, _clock(self.at_time))
        return lead_time_label(self.minutes_before)

    @property
    def sort_key(self) -> tuple:
        """Shortest first: fewer minutes, or fewer days and then the earlier time."""
        if self.all_day:
            return (1, self.days_before, self.at_time)
        return (0, self.minutes_before, "")

    def fits(self, all_day: bool) -> bool:
        """Whether this reminder means anything on an event of that kind."""
        return self.all_day == bool(all_day)

    def due_at(self, occurrence, tz: ZoneInfo) -> datetime | None:
        """When this reminder goes out for an occurrence, as a UTC instant.

        A timed reminder is subtracted from the *resolved occurrence*, never the
        series start: a fixed offset from DTSTART is an hour wrong for half the
        year on any weekly event. An all-day reminder is a wall-clock time on a
        local date, resolved in the install's zone, so it does not drift across
        a DST change. ``None`` when the reminder does not fit the occurrence's
        kind, which only happens to a row left from before the event changed
        between timed and all-day.
        """
        if not self.fits(occurrence.all_day):
            return None
        if self.all_day:
            day = date.fromisoformat(occurrence.start_local_date) - timedelta(days=self.days_before)
            return datetime.combine(day, _clock(self.at_time), tzinfo=tz).astimezone(UTC)
        return occurrence.start.astimezone(UTC) - timedelta(minutes=self.minutes_before)

    def as_dict(self) -> dict:
        return {
            "minutes_before": self.minutes_before,
            "days_before": self.days_before,
            "at_time": self.at_time,
            "key": self.key,
            "label": self.label,
        }


def _clock(value: str) -> time:
    return time.fromisoformat(value)


def _from_row(row: EventReminder) -> Reminder:
    return Reminder(
        minutes_before=row.minutes_before, days_before=row.days_before, at_time=row.at_time
    )


def ordered(reminders: Iterable[Reminder]) -> list[Reminder]:
    return sorted(reminders, key=lambda reminder: reminder.sort_key)


# --- Validation ------------------------------------------------------------


def validated(raw: Sequence, *, all_day: bool) -> list[Reminder]:
    """The reminders a write sends, checked against the event's kind.

    ``raw`` items have ``minutes_before``, ``days_before`` and ``at_time``
    attributes (the request schema). Raises ``ReminderError`` for a sixth
    reminder, a length outside ``0`` to ``MAX_LEAD_DAYS`` days, a duplicate, a
    timed reminder on an all-day event or the other way round, or a malformed
    time of day.
    """
    reminders: list[Reminder] = []
    for item in raw:
        minutes, days, at = item.minutes_before, item.days_before, item.at_time
        if all_day:
            if minutes is not None or days is None or not at:
                raise ReminderError(
                    "An all-day event's reminder is a number of days before, at a time of day."
                )
            try:
                at = _clock(at).strftime("%H:%M")
            except ValueError as exc:
                raise ReminderError("A reminder's time of day must be HH:MM.") from exc
            if not 0 <= days <= MAX_LEAD_DAYS:
                raise ReminderError(
                    f"A reminder can be at most {MAX_LEAD_DAYS} days before the event."
                )
            reminder = Reminder(days_before=days, at_time=at)
        else:
            if minutes is None or days is not None or at is not None:
                raise ReminderError("A timed event's reminder is a number of minutes before it.")
            if not 0 <= minutes <= MAX_LEAD_MINUTES:
                raise ReminderError(
                    f"A reminder can be at most {MAX_LEAD_DAYS} days before the event."
                )
            reminder = Reminder(minutes_before=minutes)
        if any(existing.key == reminder.key for existing in reminders):
            raise ReminderError("That reminder is already on the list.")
        reminders.append(reminder)
    if len(reminders) > MAX_REMINDERS:
        raise ReminderError(f"An event can have up to {MAX_REMINDERS} reminders.")
    return ordered(reminders)


# --- Reading ---------------------------------------------------------------


def series_reminders(db: Session, event_id: int) -> list[Reminder]:
    rows = (
        db.query(EventReminder)
        .filter(EventReminder.event_id == event_id, EventReminder.occurrence_date.is_(None))
        .all()
    )
    return ordered(_from_row(row) for row in rows)


def for_occurrences(
    db: Session, event_ids: Iterable[int]
) -> tuple[dict[int, list[Reminder]], dict[tuple[int, str], list[Reminder]], set[tuple[int, str]]]:
    """Everything needed to say which reminders each occurrence of these events uses.

    Returns the series' lists by event, each occurrence's own list by
    ``(event_id, original date)``, and the occurrences that have one. One pass
    for a whole page or a whole scan, rather than a query per occurrence.
    """
    ids = list(set(event_ids))
    series: dict[int, list[Reminder]] = {event_id: [] for event_id in ids}
    own: dict[tuple[int, str], list[Reminder]] = {}
    if not ids:
        return series, own, set()

    for row in db.query(EventReminder).filter(EventReminder.event_id.in_(ids)).all():
        if row.occurrence_date is None:
            series[row.event_id].append(_from_row(row))
        else:
            own.setdefault((row.event_id, row.occurrence_date), []).append(_from_row(row))

    owning = {
        (override.event_id, override.occurrence_date)
        for override in db.query(EventOverride)
        .filter(EventOverride.event_id.in_(ids), EventOverride.own_reminders.is_(True))
        .all()
    }
    return (
        {event_id: ordered(found) for event_id, found in series.items()},
        {pair: ordered(found) for pair, found in own.items()},
        owning,
    )


def resolve(
    series: dict[int, list[Reminder]],
    own: dict[tuple[int, str], list[Reminder]],
    owning: set[tuple[int, str]],
    event_id: int,
    occurrence_date: str | None,
    *,
    all_day: bool,
) -> tuple[list[Reminder], bool]:
    """One occurrence's reminders and whether they are its own, from ``for_occurrences``.

    A reminder that does not fit the occurrence's kind is left out: it can
    only be a row from before the event changed between timed and all-day.
    """
    pair = (event_id, occurrence_date or "")
    is_own = pair in owning
    found = own.get(pair, []) if is_own else series.get(event_id, [])
    return [reminder for reminder in found if reminder.fits(all_day)], is_own


def event_ids_with_reminders(db: Session) -> list[int]:
    return [row[0] for row in db.query(EventReminder.event_id).distinct().all()]


# --- Writing ---------------------------------------------------------------


def set_reminders(
    db: Session, event_id: int, occurrence_date: str | None, reminders: Sequence[Reminder]
) -> None:
    """Replace the series' list (``occurrence_date`` None) or one occurrence's."""
    query = db.query(EventReminder).filter(EventReminder.event_id == event_id)
    query = (
        query.filter(EventReminder.occurrence_date.is_(None))
        if occurrence_date is None
        else query.filter(EventReminder.occurrence_date == occurrence_date)
    )
    query.delete(synchronize_session=False)
    for reminder in reminders:
        db.add(
            EventReminder(
                event_id=event_id,
                occurrence_date=occurrence_date,
                minutes_before=reminder.minutes_before,
                days_before=reminder.days_before,
                at_time=reminder.at_time,
            )
        )


def split(db: Session, event_id: int, tail_id: int, split_date: str) -> None:
    """Give a ``following`` split's tail the series' reminders, and the
    occurrences on or after the split their own lists, as their overrides move."""
    set_reminders(db, tail_id, None, series_reminders(db, event_id))
    db.query(EventReminder).filter(
        EventReminder.event_id == event_id,
        EventReminder.occurrence_date.isnot(None),
        EventReminder.occurrence_date >= split_date,
    ).update({EventReminder.event_id: tail_id}, synchronize_session=False)


def delete(db: Session, event_id: int, *, from_date: str | None = None) -> None:
    """Delete an event's reminder rows: all of them, or the occurrences' own
    lists on or after ``from_date`` (a ``following`` delete)."""
    query = db.query(EventReminder).filter(EventReminder.event_id == event_id)
    if from_date is not None:
        query = query.filter(
            EventReminder.occurrence_date.isnot(None), EventReminder.occurrence_date >= from_date
        )
    query.delete(synchronize_session=False)
