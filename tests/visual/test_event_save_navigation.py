"""A save that adds or moves an event lands on it (#348).

Everything here is the page's: which slice is shown, where a time grid opens,
the member filter and the announcement. The API only ever sees the event, so
its own tests cannot tell a page that lands on the event from one that reloads
the slice it was already on.

Dates sit years ahead so they are never in the slice the page opens on, and
titles carry a unique suffix because the live server keeps one database for the
whole session.
"""

from __future__ import annotations

from datetime import date, timedelta
from uuid import uuid4

import pytest

YEAR = date.today().year + 3
# A Tuesday in December, and the Wednesday after it.
_DECEMBER = date(YEAR, 12, 1)
TUESDAY = _DECEMBER + timedelta(days=(1 - _DECEMBER.weekday()) % 7 + 7)
WEDNESDAY = TUESDAY + timedelta(days=1)


def _spoken(day: date) -> str:
    """How `formatDate()` words a date in another year, as the page asks for it."""
    return f"{day:%A}, {day:%B} {day.day}, {day.year}"


def _sunday_before(day: date) -> date:
    return day - timedelta(days=(day.weekday() + 1) % 7)


@pytest.fixture
def calendar_page(browser, live_server):
    """The calendar on a computer, plus a native calendar and a member who does not own it."""
    context = browser.new_context(viewport={"width": 1280, "height": 900})
    page = context.new_page()
    try:
        calendars = [
            c
            for c in page.request.get(live_server + "/api/calendars").json()
            if c["cal_type"] == "native"
        ]
        members = page.request.get(live_server + "/api/family").json()
        calendar = calendars[0]
        other = next(m for m in members if m["id"] != calendar["family_member_id"])
        page.goto(live_server + "/calendar", wait_until="networkidle")
        yield page, calendar, other
    finally:
        page.close()
        context.close()


def _set_view(page, view: str, range_: str) -> None:
    page.select_option("#view-select", view)
    page.select_option("#range-select", range_)
    page.wait_for_load_state("networkidle")


def _add(page, calendar, title: str, start: str, end: str) -> str:
    """Add an event through the real modal and return what was announced."""
    page.evaluate(
        """({ calendarId, title, start, end }) => {
            document.getElementById('live-status').textContent = '';
            openAddEventModal();
            document.getElementById('event-title').value = title;
            document.getElementById('event-calendar').value = String(calendarId);
            document.getElementById('event-start-time').value = start;
            document.getElementById('event-end-time').value = end;
            document.getElementById('btn-save-event').click();
        }""",
        {"calendarId": calendar["id"], "title": title, "start": start, "end": end},
    )
    page.wait_for_function("() => document.getElementById('live-status').textContent")
    page.wait_for_load_state("networkidle")
    return page.text_content("#live-status")


def _edit(page, title: str, mutate: str) -> None:
    """Open the one occurrence of `title` on screen, change it and save."""
    page.evaluate(
        """async ({ title, mutate }) => {
            document.getElementById('live-status').textContent = '';
            await openEditEventModal(occurrences.find(o => o.title === title));
            new Function(mutate)();
            document.getElementById('btn-save-event').click();
        }""",
        {"title": title, "mutate": mutate},
    )
    page.wait_for_timeout(600)
    page.wait_for_load_state("networkidle")


def _shown(page, title: str) -> list[str]:
    return page.evaluate(
        "(title) => occurrences.filter(o => o.title === title).map(o => o.start_date)", title
    )


def test_adding_off_screen_goes_to_its_month_and_says_so(calendar_page):
    page, calendar, _other = calendar_page
    title = f"Dentist {uuid4().hex[:8]}"

    spoken = _add(page, calendar, title, f"{TUESDAY}T10:00", f"{TUESDAY}T11:00")

    assert page.evaluate("[mode, range, isoDate(anchor)]") == ["calendar", "month", f"{YEAR}-12-01"]
    assert _shown(page, title) == [TUESDAY.isoformat()]
    assert spoken == f"Added {title} on {_spoken(TUESDAY)}."


def test_a_member_filter_that_would_hide_it_is_cleared(calendar_page):
    page, calendar, other = calendar_page
    title = f"Dentist {uuid4().hex[:8]}"
    page.evaluate(
        "async (id) => { selectedMembers.add(id); renderMemberChips(); await load(); }", other["id"]
    )

    spoken = _add(page, calendar, title, f"{TUESDAY}T10:00", f"{TUESDAY}T11:00")

    assert page.evaluate("selectedMembers.size") == 0
    assert _shown(page, title) == [TUESDAY.isoformat()]
    assert spoken == f"Added {title} on {_spoken(TUESDAY)}. Filters cleared."


def test_a_time_grid_opens_the_hour_before_the_event(calendar_page):
    page, calendar, _other = calendar_page
    _set_view(page, "calendar", "week")
    title = f"Swim lesson {uuid4().hex[:8]}"

    _add(page, calendar, title, f"{TUESDAY}T17:30", f"{TUESDAY}T18:30")

    assert page.evaluate("isoDate(anchor)") == _sunday_before(TUESDAY).isoformat()
    # 5:30 PM opens at 4 PM: the opening scroll's rule, applied to the event.
    assert page.get_attribute(".calendar-timegrid-scroll", "data-scroll-minutes") == "960"


def test_moving_within_the_slice_stays_and_names_the_time(calendar_page):
    page, calendar, _other = calendar_page
    title = f"Dentist {uuid4().hex[:8]}"
    _add(page, calendar, title, f"{TUESDAY}T10:00", f"{TUESDAY}T11:00")

    _edit(
        page,
        title,
        f"document.getElementById('event-start-time').value = '{WEDNESDAY}T14:00';"
        f"document.getElementById('event-end-time').value = '{WEDNESDAY}T15:00';",
    )

    assert page.evaluate("isoDate(anchor)") == f"{YEAR}-12-01"
    assert _shown(page, title) == [WEDNESDAY.isoformat()]
    assert page.text_content("#live-status") == (
        f"Moved {title} to {_spoken(WEDNESDAY)} at 2:00 PM."
    )


def test_an_edit_that_leaves_the_start_alone_says_nothing(calendar_page):
    page, calendar, _other = calendar_page
    title = f"Dentist {uuid4().hex[:8]}"
    _add(page, calendar, title, f"{TUESDAY}T10:00", f"{TUESDAY}T11:00")

    _edit(page, title, "document.getElementById('event-location').value = 'Main St';")

    assert page.evaluate("isoDate(anchor)") == f"{YEAR}-12-01"
    assert page.text_content("#live-status") == ""


def test_a_day_the_month_grid_borrows_still_goes_to_its_month(calendar_page):
    page, calendar, _other = calendar_page
    title = f"Dentist {uuid4().hex[:8]}"
    _add(page, calendar, title, f"{TUESDAY}T10:00", f"{TUESDAY}T11:00")
    # January 1 sits in December's 42-day grid, but December is the month shown.
    new_year = date(YEAR + 1, 1, 1)

    _edit(
        page,
        title,
        f"document.getElementById('event-start-time').value = '{new_year}T10:00';"
        f"document.getElementById('event-end-time').value = '{new_year}T11:00';",
    )

    assert page.evaluate("isoDate(anchor)") == new_year.isoformat()
    assert _shown(page, title) == [new_year.isoformat()]
