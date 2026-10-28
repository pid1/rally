"""static/dates.js in a real browser: every formatter it replaced (#305), as
`formatDate()` options, reading exactly as the page's own copy did.

Each row of the issue's formatter table is one case below, with the wording the
old helper wrote. `today` is always handed in, as the pages do, so these cases
do not depend on the day they run. The browser runs in a zone west of UTC
(America/Chicago) so a `YYYY-MM-DD` read through UTC would land a day early.
"""

from __future__ import annotations

import pytest

TODAY = "2026-10-28"

# (old helper, value, options, what it wrote)
FORMATTERS = [
    # static/packing_lists.js formatLongDate(): the year only when not this one
    (
        "packing formatLongDate",
        "2026-10-31",
        {"weekday": True, "month": "long", "year": "unlessThisYear", "today": TODAY},
        "Saturday, October 31",
    ),
    (
        "packing formatLongDate, next year",
        "2027-01-02",
        {"weekday": True, "month": "long", "year": "unlessThisYear", "today": TODAY},
        "Saturday, January 2, 2027",
    ),
    # static/packing_lists.js boxDateLabel()
    *[
        (
            "packing boxDateLabel",
            value,
            {
                "weekday": True,
                "month": "long",
                "year": "unlessThisYear",
                "relative": "upcoming",
                "today": TODAY,
            },
            expected,
        )
        for value, expected in [
            ("2026-10-28", "Today"),
            ("2026-10-29", "Tomorrow"),
            # The archive's yesterday keeps its date: never "Yesterday" here.
            ("2026-10-27", "Tuesday, October 27"),
            ("2026-10-31", "Saturday, October 31"),
        ]
    ],
    # static/packing_lists.js packLine(), after "Pack "
    (
        "packing packLine",
        "2026-09-30",
        {"weekday": True, "month": "short", "year": "never"},
        "Wednesday, Sep 30",
    ),
    # static/recurrence_form.js formatLongDate(value, withWeekday)
    (
        "recurrence read-back, weekday",
        "2027-01-01",
        {"weekday": True, "month": "long", "year": "always"},
        "Friday, January 1, 2027",
    ),
    (
        "recurrence read-back",
        "2027-01-01",
        {"weekday": False, "month": "long", "year": "always"},
        "January 1, 2027",
    ),
    # templates/notes.html and templates/meal_planner.html formatDate()
    *[
        (
            "notes / meal planner",
            value,
            {
                "weekday": True,
                "month": "short",
                "year": "unlessThisYear",
                "relative": "upcoming",
                "today": TODAY,
            },
            expected,
        )
        for value, expected in [
            ("2026-10-28", "Today"),
            ("2026-10-29", "Tomorrow"),
            ("2026-10-30", "Friday, Oct 30"),
            ("2026-09-04", "Friday, Sep 4"),
            ("2027-03-04", "Thursday, Mar 4, 2027"),
        ]
    ],
    # templates/notes_previous.html formatDate()
    (
        "previous notes",
        "2026-10-30",
        {"weekday": True, "month": "short", "year": "unlessThisYear", "today": TODAY},
        "Friday, Oct 30",
    ),
    (
        "previous notes, last year",
        "2025-12-15",
        {"weekday": True, "month": "short", "year": "unlessThisYear", "today": TODAY},
        "Monday, Dec 15, 2025",
    ),
    # templates/meal_planner_previous.html formatDate()
    (
        "previous meals",
        "2026-10-30",
        {"weekday": True, "month": "short", "year": "always"},
        "Friday, Oct 30, 2026",
    ),
    # formatShortDate() in templates/packing_lists.html and templates/todo.html
    ("short date", "2026-10-30", {"month": "short", "year": "always"}, "Oct 30, 2026"),
    # templates/todo_completed.html formatDayMonth(); templates/todo.html
    # formatDueDate() after "Due "
    (
        "day and month",
        "2026-10-30",
        {"weekday": True, "month": "short", "year": "never"},
        "Friday, Oct 30",
    ),
    # templates/todo.html formatLastCompleted()
    *[
        (
            "last completed",
            value,
            {
                "month": "short",
                "ordinal": True,
                "year": "always",
                "relative": "past",
                "today": TODAY,
            },
            expected,
        )
        for value, expected in [
            ("2026-10-28", "Today"),
            ("2026-10-27", "Yesterday"),
            # Tomorrow is not a completion: it keeps its date.
            ("2026-10-29", "Oct 29th, 2026"),
            ("2026-10-30", "Oct 30th, 2026"),
            ("2026-09-01", "Sep 1st, 2026"),
            ("2026-09-22", "Sep 22nd, 2026"),
            ("2026-09-13", "Sep 13th, 2026"),
        ]
    ],
]


@pytest.fixture(scope="module")
def page(browser, live_server):
    context = browser.new_context(timezone_id="America/Chicago")
    page = context.new_page()
    try:
        # Any page: base.html loads dates.js on every one.
        page.goto(f"{live_server}/styleguide", wait_until="domcontentloaded")
        yield page
    finally:
        context.close()


@pytest.mark.parametrize(
    ("helper", "value", "options", "expected"),
    FORMATTERS,
    ids=[f"{row[0]}:{row[1]}" for row in FORMATTERS],
)
def test_format_date_writes_what_the_old_helper_wrote(page, helper, value, options, expected):
    got = page.evaluate("([value, options]) => formatDate(value, options)", [value, options])
    assert got == expected, f"{helper}: {value} read {got!r}"


def test_an_instant_is_read_in_the_devices_zone(page):
    """A completion arrives as an instant; 03:00 UTC is still the day before in
    Chicago, as `new Date(value)` read it before."""
    got = page.evaluate(
        """() => [
            formatDate(new Date('2026-10-30T03:00:00Z'), { weekday: true, month: 'short', year: 'never' }),
            formatDate(new Date('2026-10-28T03:00:00Z'), {
                month: 'short', ordinal: true, year: 'always', relative: 'past', today: '2026-10-28',
            }),
        ]"""
    )
    assert got == ["Thursday, Oct 29", "Yesterday"]


def test_stepping_crosses_daylight_saving_by_calendar_day(page):
    """Nov 1, 2026 is 25 hours long in Chicago; Mar 8, 2026 is 23."""
    got = page.evaluate(
        """() => [
            shiftIsoDate('2026-10-31', 1), shiftIsoDate('2026-11-01', 1), shiftIsoDate('2026-11-02', -2),
            shiftIsoDate('2026-03-07', 1), shiftIsoDate('2026-03-08', 1),
            isoDate(addDays(parseIsoDate('2026-10-31'), 7)),
            shiftIsoDate('2026-12-31', 1), shiftIsoDate('2028-02-28', 1),
        ]"""
    )
    assert got == [
        "2026-11-01",
        "2026-11-02",
        "2026-10-31",
        "2026-03-08",
        "2026-03-09",
        "2026-11-07",
        "2027-01-01",
        "2028-02-29",
    ]


def test_names_and_ordinals(page):
    got = page.evaluate(
        """() => [WEEKDAYS_SHORT.join(' '), MONTHS_SHORT[8], MONTHS[9], WEEKDAYS[5],
                  [1, 2, 3, 4, 11, 12, 13, 21, 22, 23, 101, 111].map(ordinal).join(' ')]"""
    )
    assert got == [
        "Sun Mon Tue Wed Thu Fri Sat",
        "Sep",
        "October",
        "Friday",
        "1st 2nd 3rd 4th 11th 12th 13th 21st 22nd 23rd 101st 111th",
    ]


def test_today_local_reads_the_zone_it_is_handed(page):
    got = page.evaluate(
        """() => [todayLocal('America/Chicago'),
                  new Intl.DateTimeFormat('en-CA', { timeZone: 'America/Chicago' }).format(new Date()),
                  todayLocal('Pacific/Kiritimati'),
                  new Intl.DateTimeFormat('en-CA', { timeZone: 'Pacific/Kiritimati' }).format(new Date())]"""
    )
    assert got[0] == got[1] and got[2] == got[3]


@pytest.mark.parametrize(
    "options",
    [
        {"relative": "upcoming"},
        {"year": "unlessThisYear"},
    ],
)
def test_format_date_never_falls_back_to_the_device_clock(page, options):
    """Whatever needs today must be handed it: there is no fallback."""
    error = page.evaluate(
        """(options) => { try { formatDate('2026-10-30', options); return null; }
                         catch (e) { return e.message; } }""",
        options,
    )
    assert error and "today" in error
