"""How a date, a time of day and a reminder lead time read to a person.

One rule, decided here and nowhere else. The calendar's detail view and
``Delete event`` dialog show labels the server sends (``OccurrenceResponse``),
and a Pushover change notice builds its ``When:`` line from the same helpers
(``notifications.when_label``), so the three places cannot drift apart.

A date reads ``Friday, Oct 30, 2026``: the full weekday, a three-letter month,
the day without a leading zero, and always the year. When two dates are shown
together the year is stated once if both share it and on both ends if they do
not — ``Friday, Oct 30 – Sunday, Nov 1, 2026`` but ``Thursday, Dec 31, 2026 –
Saturday, Jan 2, 2027``.

``display_date`` takes options for the places that read a date more briefly
or more fully, the same options the browser's ``formatDate()`` takes (#305):
``Due Sep 30`` on a task push, ``Wednesday, October 28, 2026`` on the
Dashboard. A time of day reads ``4:00 PM`` (``display_time``).

Every date here is a local ISO date (``YYYY-MM-DD``), never an instant. An
occurrence's dates are its own ``start_local_date`` / ``end_local_date``;
re-deriving one from a UTC instant is what puts an all-day event on the day
before.

The names are spelled out rather than taken from ``strftime``: ``%A`` and
``%b`` follow the process locale, and a label that changes with the
container's ``LANG`` is not one rule. ``%p`` and ``%I`` likewise: the
meridiem is spelled out and the hour has no leading zero to trim.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
MONTHS_LONG = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)  # fmt: skip

RANGE_SEPARATOR = " – "

MINUTES_PER_HOUR = 60
MINUTES_PER_DAY = 24 * MINUTES_PER_HOUR

# What a reminder set to fire exactly at the start says. "0 minutes before" is
# arithmetic rather than an answer, and "at the start" reads as a time.
AT_START_LABEL = "When it starts"


def _parse(iso: str) -> date:
    return date.fromisoformat(iso)


def _without_year(day: date) -> str:
    return f"{WEEKDAYS[day.weekday()]}, {MONTHS[day.month - 1]} {day.day}"


def _with_year(day: date) -> str:
    return f"{_without_year(day)}, {day.year}"


def display_date(
    iso: str,
    *,
    weekday: bool = True,
    month: Literal["short", "long"] = "short",
    year: Literal["always", "unless_this_year", "never"] = "always",
    today: date | None = None,
) -> str:
    """``2026-10-30`` -> ``Friday, Oct 30, 2026``, or a part of it.

    ``weekday=False`` drops the weekday (``Oct 30, 2026``), ``month="long"``
    spells the month out (``Friday, October 30, 2026``), and ``year`` is
    ``"always"``, ``"never"`` or ``"unless_this_year"`` — which needs ``today``
    handed in, because this reads no clock: whose today it is (the Settings
    zone, a test's frozen one) is the caller's to say.
    """
    day = _parse(iso)
    if year == "unless_this_year":
        if today is None:
            raise ValueError('year="unless_this_year" needs today')
        show_year = day.year != today.year
    else:
        show_year = year == "always"
    names = MONTHS_LONG if month == "long" else MONTHS
    label = f"{names[day.month - 1]} {day.day}"
    if weekday:
        label = f"{WEEKDAYS[day.weekday()]}, {label}"
    return f"{label}, {day.year}" if show_year else label


def display_time(
    moment: datetime,
    *,
    meridiem: bool = True,
    minutes: Literal["always", "unless_zero"] = "always",
) -> str:
    """A time of day as ``4:00 PM``: 12-hour, no leading zero.

    ``moment`` is already in the zone to show; nothing here converts.
    ``meridiem=False`` leaves ``AM`` / ``PM`` off, for the first end of a range
    that states it once (``5:30 to 6:30 PM``), and ``minutes="unless_zero"``
    drops ``:00`` on the hour (``7 PM``), as the sports listings read.
    """
    hour = moment.hour % 12 or 12
    label = (
        str(hour)
        if minutes == "unless_zero" and moment.minute == 0
        else f"{hour}:{moment.minute:02d}"
    )
    if meridiem:
        label += " AM" if is_morning(moment) else " PM"
    return label


def is_morning(moment: datetime) -> bool:
    """Whether a time reads ``AM``: midnight through 11:59."""
    return moment.hour < 12


def display_date_ends(start_iso: str, end_iso: str) -> tuple[str, str]:
    """Both ends of a two-date label, each ready to have a time put after it.

    The year goes on the end only when both dates share it, and on both when
    they do not. Split out from ``display_date_range`` for a timed event that
    crosses midnight, whose times sit beside their own dates:
    ``Friday, Oct 30 10:30 PM – Saturday, Oct 31, 2026 6:15 AM``.
    """
    start, end = _parse(start_iso), _parse(end_iso)
    if start.year == end.year:
        return _without_year(start), _with_year(end)
    return _with_year(start), _with_year(end)


def display_date_range(start_iso: str, end_iso: str) -> str:
    """Two inclusive local dates as one label.

    ``Friday, Oct 30 – Sunday, Nov 1, 2026``; across a year boundary
    ``Thursday, Dec 31, 2026 – Saturday, Jan 2, 2027``. The same date twice is
    one date, not a range.
    """
    if start_iso == end_iso:
        return display_date(start_iso)
    left, right = display_date_ends(start_iso, end_iso)
    return f"{left}{RANGE_SEPARATOR}{right}"


def display_dated_times(start_iso: str, start_time: str, end_iso: str, end_time: str) -> str:
    """A timed span crossing midnight, each time beside its own date.

    ``Friday, Oct 30 10:30 PM – Saturday, Oct 31, 2026 6:15 AM``. A date range
    followed by ``10:30 PM to 6:15 AM`` would not say which end is which.
    """
    left, right = display_date_ends(start_iso, end_iso)
    return f"{left} {start_time}{RANGE_SEPARATOR}{right} {end_time}"


def _count(number: int, unit: str) -> str:
    return f"{number} {unit}" if number == 1 else f"{number} {unit}s"


def lead_time_label(minutes: int) -> str:
    """A reminder's lead time in days, hours and minutes.

    Each nonzero part is stated, largest first, singular or plural as needed:
    ``60`` -> ``1 hour before``, ``90`` -> ``1 hour 30 minutes before``,
    ``3075`` -> ``2 days 3 hours 15 minutes before``. Any whole number is
    worded, not only the values the edit form's select offers, because the API
    accepts any.

    ``0`` reads ``When it starts`` (``AT_START_LABEL``): that is when the push
    goes out. The API does not refuse a negative number either. One is worded
    as the offset it is, ``-60`` -> ``1 hour after``, rather than raising, so a
    single odd row cannot fail the calendar's response.
    """
    if minutes == 0:
        return AT_START_LABEL
    direction = "before" if minutes > 0 else "after"
    remaining = abs(minutes)
    days, remaining = divmod(remaining, MINUTES_PER_DAY)
    hours, remaining = divmod(remaining, MINUTES_PER_HOUR)
    parts = [
        _count(value, unit)
        for value, unit in ((days, "day"), (hours, "hour"), (remaining, "minute"))
        if value
    ]
    return f"{' '.join(parts)} {direction}"
