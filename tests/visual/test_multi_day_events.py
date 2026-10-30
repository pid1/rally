"""How a timed event over several dates reads in Month and Agenda (#320).

Under a day it is one event on the day it starts; at a day or more it has a row
on every date. The rule and the labels live in the page, so only a browser can
show them.
"""

from __future__ import annotations

from datetime import date, timedelta
from uuid import uuid4

import pytest

# Far enough ahead to stay in the future whenever this runs.
YEAR = date.today().year + 3


@pytest.fixture
def page(browser, live_server):
    context = browser.new_context(viewport={"width": 1280, "height": 900})
    page = context.new_page()
    try:
        page.goto(live_server + "/calendar", wait_until="networkidle")
        page.wait_for_function("typeof agendaOccurrencesOn === 'function'")
        yield page
    finally:
        page.close()
        context.close()


def _event(page, start, end, **payload):
    title = payload.pop("title", f"Outing {uuid4().hex[:8]}")
    response = page.request.post(
        page.url.split("/calendar")[0] + "/api/events",
        data={"title": title, "start": start, "end": end, **payload},
    )
    assert response.ok, response.text()
    return title


def _show(page, view, day):
    page.evaluate(
        """async ([view, iso]) => {
            mode = view;
            range = 'month';
            anchor = startOfMonth(parseIsoDate(iso));
            await load();
        }""",
        [view, day.isoformat()],
    )
    page.wait_for_timeout(300)


def _month_rows(page, day, title):
    """The month cell's rows for one event, each as its time and title."""
    _show(page, "calendar", day)
    return page.evaluate(
        """([iso, title]) => {
            const cell = document.querySelector(`.calendar-day [data-agenda-date="${iso}"]`)
                .parentElement;
            return [...cell.querySelectorAll('.calendar-event')]
                .filter(row => row.textContent.includes(title))
                .map(row => row.querySelector('.calendar-event-time')?.textContent ?? '');
        }""",
        [day.isoformat(), title],
    )


def _agenda_rows(page, day, *titles):
    """These events' rows in a date's agenda, in order, as [time label, title].

    Only these events': the server is shared, and other tests' repeating events
    run across the same dates.
    """
    _show(page, "agenda", day)
    rows = page.evaluate(
        """(iso) => {
            const heading = formatDate(parseIsoDate(iso), {
                weekday: true, month: 'long', year: 'never',
            });
            const box = [...document.querySelectorAll('.agenda-day')].find(el =>
                el.querySelector('.agenda-heading').textContent.startsWith(heading));
            return [...box.querySelectorAll('.editable-item')].map(row => [
                row.querySelector('.agenda-time').textContent,
                row.querySelector('.editable-item-title').textContent.trim(),
            ]);
        }""",
        day.isoformat(),
    )
    return [row for row in rows if row[1] in titles]


def test_under_a_day_is_one_row_on_the_day_it_starts(page):
    friday, saturday = date(YEAR, 6, 15), date(YEAR, 6, 16)
    title = _event(page, f"{friday}T19:00", f"{saturday}T09:00")

    assert _month_rows(page, friday, title) == ["7p"]
    assert _month_rows(page, saturday, title) == []


def test_under_a_day_is_listed_on_both_dates_in_the_agenda(page):
    friday, saturday = date(YEAR, 6, 15), date(YEAR, 6, 16)
    title = _event(page, f"{friday}T19:00", f"{saturday}T09:00")

    assert _agenda_rows(page, friday, title) == [["7:00 PM", title]]
    assert _agenda_rows(page, saturday, title) == [["Until 9:00 AM", title]]


def test_a_day_or_more_has_a_row_on_every_date_with_the_time_on_the_first(page):
    first, middle, last = (date(YEAR, 7, day) for day in (10, 11, 12))
    title = _event(page, f"{first}T08:00", f"{last}T17:00")

    assert _month_rows(page, first, title) == ["8a"]
    assert _month_rows(page, middle, title) == [""]
    assert _month_rows(page, last, title) == [""]


def test_a_day_or_more_reads_its_start_all_day_and_until_in_the_agenda(page):
    first, middle, last = (date(YEAR, 7, day) for day in (20, 21, 22))
    title = _event(page, f"{first}T08:00", f"{last}T17:00")

    assert _agenda_rows(page, first, title) == [["8:00 AM", title]]
    assert _agenda_rows(page, middle, title) == [["All day", title]]
    assert _agenda_rows(page, last, title) == [["Until 5:00 PM", title]]


def test_exactly_a_day_counts_as_a_day(page):
    first, second = date(YEAR, 8, 10), date(YEAR, 8, 11)
    title = _event(page, f"{first}T07:00", f"{second}T07:00")

    assert _month_rows(page, second, title) == [""]


def test_a_clock_change_does_not_move_the_line(page):
    """7 PM to 7 PM over the spring change is a day, though only 23 hours pass."""
    march = date(YEAR, 3, 1)
    sunday = march + timedelta(days=(6 - march.weekday()) % 7 + 7)
    saturday = sunday - timedelta(days=1)
    title = _event(page, f"{saturday}T19:00", f"{sunday}T19:00")

    assert _month_rows(page, sunday, title) == [""]


def test_a_days_agenda_reads_all_day_first_then_by_time(page):
    first, last = date(YEAR, 9, 10), date(YEAR, 9, 12)
    middle = first + timedelta(days=1)
    until = _event(page, f"{middle}T20:00", f"{last}T09:00")
    between = _event(page, f"{first}T08:00", f"{last + timedelta(days=1)}T17:00")
    later = _event(page, f"{last}T10:00", f"{last}T11:00")
    earlier = _event(page, f"{last}T08:00", f"{last}T08:30")
    all_day = _event(page, last.isoformat(), last.isoformat(), all_day=True)

    rows = _agenda_rows(page, last, until, between, later, earlier, all_day)
    assert [row[1] for row in rows[:2]] == [between, all_day] or [row[1] for row in rows[:2]] == [
        all_day,
        between,
    ]
    assert rows[2:] == [
        ["8:00 AM", earlier],
        ["Until 9:00 AM", until],
        ["10:00 AM", later],
    ]
