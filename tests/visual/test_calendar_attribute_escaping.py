"""An event title with a quote stays one attribute value on the calendar (#343).

The month grid, the time grid and its all-day band put an event's title in a
`title="…"` attribute. Titles can come from external ICS and CalDAV feeds, so a
`"` in one must not end the attribute: before #343 the rest of the title became
attributes of its own, event handlers included. Only a browser parses the
markup, so only a browser can show it.
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
        page.wait_for_function("typeof load === 'function'")
        yield page
    finally:
        page.close()
        context.close()


def _title() -> str:
    return f'Pick up "Coach" Sam\'s kit" onmouseover="window.__escaped = 1 {uuid4().hex[:8]}'


def _event(page, title, start, end, **payload):
    response = page.request.post(
        page.url.split("/calendar")[0] + "/api/events",
        data={"title": title, "start": start, "end": end, **payload},
    )
    assert response.ok, response.text()


def _show(page, calendar_range, day):
    page.evaluate(
        """async ([calendarRange, iso]) => {
            mode = 'calendar';
            range = calendarRange;
            anchor = calendarRange === 'month' ? startOfMonth(parseIsoDate(iso)) : parseIsoDate(iso);
            await load();
        }""",
        [calendar_range, day.isoformat()],
    )
    page.wait_for_timeout(300)


def _attributes(page, selector, needle):
    """Every matching element whose text holds `needle`: its title and its attribute names."""
    return page.evaluate(
        """([selector, needle]) => [...document.querySelectorAll(selector)]
            .filter(el => el.textContent.includes(needle))
            .map(el => ({ title: el.getAttribute('title'), names: el.getAttributeNames() }))""",
        [selector, needle],
    )


def _assert_whole(found, expected_title):
    assert found, "the event was not drawn"
    for element in found:
        assert element["title"] == expected_title
        assert "onmouseover" not in element["names"], element["names"]


def test_a_quoted_title_stays_whole_in_the_month_grid(page):
    day = date(YEAR, 3, 10)
    title = _title()
    _event(page, title, f"{day}T15:00", f"{day}T16:00")

    _show(page, "month", day)
    _assert_whole(_attributes(page, ".calendar-event", title), title)


def test_a_quoted_title_stays_whole_in_the_time_grid(page):
    day = date(YEAR, 3, 11)
    title = _title()
    _event(page, title, f"{day}T15:00", f"{day}T16:00")

    _show(page, "week", day)
    found = _attributes(page, ".calendar-timegrid-event", title)
    assert found, "the event was not drawn"
    for element in found:
        # The time grid's title leads with the time: "3:00 PM · <title>".
        assert element["title"].endswith(f" · {title}"), element["title"]
        assert "onmouseover" not in element["names"], element["names"]


def test_a_quoted_title_stays_whole_in_the_all_day_band(page):
    day = date(YEAR, 3, 12)
    title = _title()
    _event(page, title, day.isoformat(), (day + timedelta(days=1)).isoformat(), all_day=True)

    _show(page, "week", day)
    _assert_whole(_attributes(page, ".calendar-timegrid-allday-event", title), title)
