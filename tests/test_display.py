"""How a date and a reminder read (#304): ``rally.utils.display``.

The rule is decided once, in Python. The calendar's detail view and ``Delete
event`` dialog show labels the server computes on ``OccurrenceResponse``, and
the change notices build their ``When:`` line from the same helpers, so this
file pins the helpers themselves, the response's labels, and that the API sends
them. The notices' ``When:`` lines are pinned in ``tests/test_notifications.py``.
"""

import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from rally.calendars.occurrence import Occurrence
from rally.routers.events import _occurrence_response
from rally.utils.display import (
    AT_START_LABEL,
    display_date,
    display_date_range,
    display_dated_times,
    display_time,
    lead_time_label,
)

CHICAGO = ZoneInfo("America/Chicago")

# --- One date ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("iso", "label"),
    [
        ("2026-10-30", "Friday, Oct 30, 2026"),
        ("2026-11-01", "Sunday, Nov 1, 2026"),
        ("2026-10-31", "Saturday, Oct 31, 2026"),
        ("2026-11-05", "Thursday, Nov 5, 2026"),
        ("2026-08-14", "Friday, Aug 14, 2026"),
        ("2026-09-07", "Monday, Sep 7, 2026"),
        ("2027-01-02", "Saturday, Jan 2, 2027"),
        ("2028-02-29", "Tuesday, Feb 29, 2028"),
    ],
)
def test_a_date_reads_weekday_short_month_day_and_year(iso, label):
    assert display_date(iso) == label


def test_every_month_is_three_letters_and_september_is_sep():
    months = [display_date(f"2026-{month:02d}-01").split(", ")[1][:3] for month in range(1, 13)]
    assert months == [
        "Jan", "Feb", "Mar", "Apr", "May", "Jun",
        "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
    ]  # fmt: skip


def test_the_day_has_no_leading_zero():
    assert display_date("2026-11-01") == "Sunday, Nov 1, 2026"
    assert "01" not in display_date("2026-11-01")


# --- One date, with options (#311) --------------------------------------------------


def test_a_date_without_its_weekday():
    assert display_date("2026-11-14", weekday=False) == "Nov 14, 2026"


def test_a_date_with_its_month_spelled_out():
    assert display_date("2026-10-28", month="long") == "Wednesday, October 28, 2026"
    assert display_date("2026-10-05", month="long") == "Monday, October 5, 2026"
    months = [
        display_date(f"2026-{month:02d}-01", weekday=False, month="long", year="never")
        for month in range(1, 13)
    ]
    assert months == [
        "January 1", "February 1", "March 1", "April 1", "May 1", "June 1",
        "July 1", "August 1", "September 1", "October 1", "November 1", "December 1",
    ]  # fmt: skip


def test_a_date_without_its_year():
    assert display_date("2026-10-10", month="long", year="never") == "Saturday, October 10"


@pytest.mark.parametrize(
    ("iso", "label"),
    [("2026-09-30", "Sep 30"), ("2027-09-30", "Sep 30, 2027"), ("2025-12-31", "Dec 31, 2025")],
)
def test_the_year_unless_it_is_this_one(iso, label):
    today = datetime(2026, 10, 28).date()
    assert display_date(iso, weekday=False, year="unless_this_year", today=today) == label


def test_unless_this_year_needs_today_handed_in():
    with pytest.raises(ValueError):
        display_date("2026-09-30", year="unless_this_year")


# --- A time of day (#311) --------------------------------------------------------------


@pytest.mark.parametrize(
    ("hour", "minute", "label", "bare", "on_the_hour"),
    [
        (0, 0, "12:00 AM", "12:00", "12 AM"),
        (0, 5, "12:05 AM", "12:05", "12:05 AM"),
        (7, 5, "7:05 AM", "7:05", "7:05 AM"),
        (9, 0, "9:00 AM", "9:00", "9 AM"),
        (11, 59, "11:59 AM", "11:59", "11:59 AM"),
        (12, 0, "12:00 PM", "12:00", "12 PM"),
        (13, 30, "1:30 PM", "1:30", "1:30 PM"),
        (19, 0, "7:00 PM", "7:00", "7 PM"),
        (23, 45, "11:45 PM", "11:45", "11:45 PM"),
    ],
)
def test_a_time_of_day(hour, minute, label, bare, on_the_hour):
    moment = datetime(2026, 10, 28, hour, minute, tzinfo=CHICAGO)
    assert display_time(moment) == label
    assert display_time(moment, meridiem=False) == bare
    assert display_time(moment, minutes="unless_zero") == on_the_hour


def test_a_time_converts_nothing():
    """The moment is already in the zone to show: 21:00 UTC reads 9:00 PM."""
    assert display_time(datetime(2026, 10, 28, 21, 0, tzinfo=UTC)) == "9:00 PM"


# --- Two dates ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("start", "end", "label"),
    [
        # One year: stated once, at the end.
        ("2026-10-30", "2026-11-01", "Friday, Oct 30 – Sunday, Nov 1, 2026"),
        ("2026-08-14", "2026-08-16", "Friday, Aug 14 – Sunday, Aug 16, 2026"),
        # Across a year boundary: stated on both ends.
        ("2026-12-31", "2027-01-02", "Thursday, Dec 31, 2026 – Saturday, Jan 2, 2027"),
        ("2026-12-31", "2027-01-01", "Thursday, Dec 31, 2026 – Friday, Jan 1, 2027"),
        # The same date twice is one date.
        ("2026-10-30", "2026-10-30", "Friday, Oct 30, 2026"),
    ],
)
def test_a_date_range_states_the_year_once_or_on_both_ends(start, end, label):
    assert display_date_range(start, end) == label


@pytest.mark.parametrize(
    ("start", "start_time", "end", "end_time", "label"),
    [
        (
            "2026-10-30",
            "10:30 PM",
            "2026-10-31",
            "6:15 AM",
            "Friday, Oct 30 10:30 PM – Saturday, Oct 31, 2026 6:15 AM",
        ),
        (
            "2026-12-31",
            "10:00 PM",
            "2027-01-01",
            "1:00 AM",
            "Thursday, Dec 31, 2026 10:00 PM – Friday, Jan 1, 2027 1:00 AM",
        ),
    ],
)
def test_a_span_crossing_midnight_puts_each_time_beside_its_date(
    start, start_time, end, end_time, label
):
    assert display_dated_times(start, start_time, end, end_time) == label


# --- Reminder lead time ------------------------------------------------------------


@pytest.mark.parametrize(
    ("minutes", "label"),
    [
        # Every example in the issue.
        (10, "10 minutes before"),
        (60, "1 hour before"),
        (90, "1 hour 30 minutes before"),
        (120, "2 hours before"),
        (1440, "1 day before"),
        (1500, "1 day 1 hour before"),
        (3075, "2 days 3 hours 15 minutes before"),
        # The rest of the select.
        (30, "30 minutes before"),
        # Singulars and skipped zero parts.
        (1, "1 minute before"),
        (61, "1 hour 1 minute before"),
        (1441, "1 day 1 minute before"),
        (2880, "2 days before"),
        (1530, "1 day 1 hour 30 minutes before"),
        (2 * 1440 + 2 * 60 + 2, "2 days 2 hours 2 minutes before"),
        # Large values keep counting in days rather than switching unit.
        (14 * 1440, "14 days before"),
        (400 * 1440 + 59, "400 days 59 minutes before"),
    ],
)
def test_a_lead_time_reads_in_days_hours_and_minutes(minutes, label):
    assert lead_time_label(minutes) == label


def test_a_zero_lead_time_says_it_goes_out_when_the_event_starts():
    """'0 minutes before' is arithmetic, not an answer; and nothing empty."""
    assert lead_time_label(0) == AT_START_LABEL == "When it starts"


@pytest.mark.parametrize(
    ("minutes", "label"),
    [(-60, "1 hour after"), (-1, "1 minute after"), (-1500, "1 day 1 hour after")],
)
def test_a_negative_lead_time_is_worded_rather_than_raising(minutes, label):
    """The API accepts any integer; one odd row must not fail the calendar."""
    assert lead_time_label(minutes) == label


@pytest.mark.parametrize("minutes", range(0, 3 * 1440 + 1, 7))
def test_any_lead_time_round_trips_through_its_words(minutes):
    """Whatever number the formatter is handed, the words add back up to it."""
    label = lead_time_label(minutes)
    if minutes == 0:
        assert label == AT_START_LABEL
        return
    assert label.endswith(" before")
    words = label.removesuffix(" before").split()
    units = {"day": 1440, "days": 1440, "hour": 60, "hours": 60, "minute": 1, "minutes": 1}
    total = sum(int(words[i]) * units[words[i + 1]] for i in range(0, len(words), 2))
    assert total == minutes
    for i in range(0, len(words), 2):
        assert (int(words[i]) == 1) == (not words[i + 1].endswith("s"))


# --- The labels on OccurrenceResponse ----------------------------------------------


def _response(*, start, end, start_date, end_date, all_day=False, notify=None):
    occurrence = Occurrence(
        uid="u",
        source="native",
        title="Soccer practice",
        start=start,
        end=end,
        start_local_date=start_date,
        end_local_date=end_date,
        all_day=all_day,
        notify_minutes_before=notify,
    )
    return _occurrence_response(occurrence, CHICAGO)


def _local(*args):
    return datetime(*args, tzinfo=CHICAGO).astimezone(UTC)


def test_a_timed_event_within_one_day():
    response = _response(
        start=_local(2026, 10, 30, 16, 0),
        end=_local(2026, 10, 30, 16, 30),
        start_date="2026-10-30",
        end_date="2026-10-30",
        notify=60,
    )
    assert response.when_display == "Friday, Oct 30, 2026 · 4:00 PM – 4:30 PM"
    assert response.reminder_display == "1 hour before"
    assert response.start_date_display == "Friday, Oct 30, 2026"


def test_an_all_day_event_on_one_day():
    start = datetime(2026, 10, 31, 5, tzinfo=UTC)
    response = _response(
        start=start,
        end=start + timedelta(days=1),
        start_date="2026-10-31",
        end_date="2026-10-31",
        all_day=True,
    )
    assert response.when_display == "Saturday, Oct 31, 2026 · All day"


def test_an_all_day_event_across_several_days():
    start = datetime(2026, 10, 30, 5, tzinfo=UTC)
    response = _response(
        start=start,
        end=start + timedelta(days=3),
        start_date="2026-10-30",
        end_date="2026-11-01",
        all_day=True,
    )
    assert response.when_display == "Friday, Oct 30 – Sunday, Nov 1, 2026 · All day"


def test_an_all_day_event_across_the_new_year():
    start = datetime(2026, 12, 31, 6, tzinfo=UTC)
    response = _response(
        start=start,
        end=start + timedelta(days=3),
        start_date="2026-12-31",
        end_date="2027-01-02",
        all_day=True,
    )
    assert response.when_display == "Thursday, Dec 31, 2026 – Saturday, Jan 2, 2027 · All day"


def test_a_timed_event_crossing_midnight_dates_each_end_and_names_no_zone():
    response = _response(
        start=_local(2026, 10, 30, 22, 30),
        end=_local(2026, 10, 31, 6, 15),
        start_date="2026-10-30",
        end_date="2026-10-31",
    )
    assert response.when_display == "Friday, Oct 30 10:30 PM – Saturday, Oct 31, 2026 6:15 AM"
    assert "CDT" not in response.when_display


def test_dates_come_from_the_local_dates_never_the_instants():
    """An all-day event's UTC start is the evening before in Chicago's terms
    only if somebody converts it. Nothing here does: the label follows the
    local dates even when they disagree with the instant on purpose."""
    response = _response(
        start=datetime(2026, 10, 30, 0, tzinfo=UTC),  # Thursday evening in Chicago
        end=datetime(2026, 10, 31, 0, tzinfo=UTC),
        start_date="2026-10-30",
        end_date="2026-10-30",
        all_day=True,
    )
    assert response.when_display == "Friday, Oct 30, 2026 · All day"
    assert response.start_date_display == "Friday, Oct 30, 2026"


def test_no_reminder_has_no_label_and_a_zero_one_does():
    timed = {
        "start": _local(2026, 10, 30, 16, 0),
        "end": _local(2026, 10, 30, 16, 30),
        "start_date": "2026-10-30",
        "end_date": "2026-10-30",
    }
    assert _response(**timed).reminder_display == ""
    assert _response(**timed, notify=0).reminder_display == "When it starts"
    assert _response(**timed, notify=3075).reminder_display == "2 days 3 hours 15 minutes before"


def test_the_labels_are_serialized():
    response = _response(
        start=_local(2026, 10, 30, 16, 0),
        end=_local(2026, 10, 30, 16, 30),
        start_date="2026-10-30",
        end_date="2026-10-30",
        notify=90,
    )
    dumped = response.model_dump()
    assert dumped["when_display"] == response.when_display
    assert dumped["start_date_display"] == "Friday, Oct 30, 2026"
    assert dumped["reminder_display"] == "1 hour 30 minutes before"


# --- Through the API ---------------------------------------------------------------


@pytest.fixture
def chicago(local_timezone):
    local_timezone("America/Chicago")


def _occurrence(client, title):
    response = client.get("/api/events", params={"start": "2026-10-01", "end": "2027-02-01"})
    assert response.status_code == 200, response.text
    return next(o for o in response.json()["occurrences"] if o["title"] == title)


@pytest.mark.usefixtures("chicago")
def test_the_api_sends_the_labels_the_page_shows(client):
    created = client.post(
        "/api/events",
        json={
            "title": "Soccer practice",
            "start": "2026-10-30T16:00",
            "end": "2026-10-30T16:30",
            "notify_minutes_before": 60,
        },
    )
    assert created.status_code == 201, created.text
    occurrence = _occurrence(client, "Soccer practice")
    assert occurrence["when_display"] == "Friday, Oct 30, 2026 · 4:00 PM – 4:30 PM"
    assert occurrence["reminder_display"] == "1 hour before"
    assert occurrence["start_date_display"] == "Friday, Oct 30, 2026"


@pytest.mark.usefixtures("chicago")
def test_the_api_labels_an_overnight_and_an_all_day_event(client):
    for payload in (
        {"title": "Campout", "start": "2026-10-30T22:30", "end": "2026-10-31T06:15"},
        {"title": "New Year trip", "all_day": True, "start": "2026-12-31", "end": "2027-01-02"},
    ):
        assert client.post("/api/events", json=payload).status_code == 201
    assert _occurrence(client, "Campout")["when_display"] == (
        "Friday, Oct 30 10:30 PM – Saturday, Oct 31, 2026 6:15 AM"
    )
    assert _occurrence(client, "New Year trip")["when_display"] == (
        "Thursday, Dec 31, 2026 – Saturday, Jan 2, 2027 · All day"
    )


# --- Nothing else words a date or a time (#311) ---------------------------------------

SRC = Path(__file__).resolve().parent.parent / "src" / "rally"

# What may still word a date or a time with ``strftime`` outside display.py, as
# the test reads it. The daily summary's prompt is its own issue (changing it
# changes the eval), and the Dashboard's fallback timestamp is replaced in the
# browser on load.
STRFTIME_ALLOWED = {
    "generator/generate.py": None,  # every call: the summary prompt
    "packing_lists.py": [
        "day.strftime('%A')",  # _long_date(), for summary_text()'s headings
        "day.strftime('%B')",
    ],
    "routers/dashboard.py": ['timestamp_utc_dt.strftime("%Y-%m-%d %I:%M %p")'],
}


def test_no_module_words_a_date_with_strftime_or_the_calendar_module():
    """``%a``, ``%A``, ``%b``, ``%B``, ``%I`` and ``%p`` follow the process
    locale (or need a leading zero trimmed); ``calendar.month_abbr`` and
    ``day_name`` likewise. Rally's words come from ``rally.utils.display``."""
    call = re.compile(r"[\w.()]*\.strftime\(\s*(['\"])[^'\"]*%-?[aAbBIp][^'\"]*\1\s*\)")
    names = re.compile(r"\b(?:month_abbr|month_name|day_abbr|day_name)\[")
    offenders = {}
    for path in sorted(SRC.rglob("*.py")):
        relative = path.relative_to(SRC).as_posix()
        if relative == "utils/display.py":
            continue
        allowed = STRFTIME_ALLOWED.get(relative, [])
        if allowed is None:
            continue
        source = path.read_text()
        found = [m.group(0) for m in call.finditer(source)] + names.findall(source)
        left = [snippet for snippet in found if snippet not in allowed]
        if left:
            offenders[relative] = left
    assert not offenders, f"word dates and times with rally.utils.display: {offenders}"
