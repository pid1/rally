"""How a note's date is labelled, on both Notes pages, in a real browser.

The rule: a date shows its year **only when that year is not the current one**.
`Monday, Dec 15, 2025` in an archive that has rolled over, `Monday, Sep 7` in
one that has not — the year is information when it distinguishes and noise when
it cannot.

This is asserted by calling each page's own ``formatDate()`` with synthetic
dates rather than by seeding rows, for two reasons. The year branch needs a
note from a previous year, and the API refuses to write one — correctly, since
past days are read-only, so there is no way to seed one through the front door.
And the seed is entirely current-year, which means that without this the branch
never executes in any test run at all.

Both pages carry their own copy of the rule (the Notes page layers `Today` and
`Tomorrow` on top), so both are checked, and they are checked against each
other: two implementations of one rule is exactly the shape that drifts.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

# `rally.cli.seed()` sets this, and both pages read it from /api/settings to
# decide what "today" is. The test has to agree or the Today/Tomorrow cases
# land on the wrong day near midnight.
SEEDED_TZ = ZoneInfo("America/Chicago")

PAGES = {"notes": "/notes", "notes-previous": "/notes/previous"}


def _today() -> date:
    """Today in the timezone the pages are configured with, not the runner's."""
    return datetime.now(SEEDED_TZ).date()


def _iso(d: date) -> str:
    return d.strftime("%Y-%m-%d")


def _expected(d: date, *, with_year: bool) -> str:
    """What `toLocaleDateString('en-US', …)` produces for these options."""
    day = str(d.day)  # no zero padding, matching `day: 'numeric'`
    base = f"{d.strftime('%A')}, {d.strftime('%b')} {day}"
    return f"{base}, {d.year}" if with_year else base


@pytest.fixture(scope="module", params=sorted(PAGES))
def formatter(request, browser, live_server):
    """A callable running the given page's own formatDate() in the browser."""
    page_name = request.param
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    page = context.new_page()
    try:
        page.goto(live_server + PAGES[page_name], wait_until="networkidle")
        # Keyed on the shared class, not an id: the Notes page uses
        # `#notes-list` (mirroring the Meal Planner's `#plans-list`) and the
        # archive uses `#list-container` (mirroring the other archive pages).
        # Both pages set localTimezone from /api/settings during init; formatDate
        # reads it, so waiting for the list is waiting for that to have happened.
        page.wait_for_selector(".list-container .editable-item")

        def call(date_str: str) -> str:
            return page.evaluate("(d) => formatDate(d)", date_str)

        yield call
    finally:
        page.close()
        context.close()


def test_a_date_in_the_current_year_shows_no_year(formatter):
    d = _today() - timedelta(days=3)
    if d.year != _today().year:
        pytest.skip("run is within three days of New Year; covered by the boundary test")
    assert formatter(_iso(d)) == _expected(d, with_year=False)


def test_a_date_in_a_previous_year_shows_its_year(formatter):
    """The branch the seed can never reach."""
    d = date(_today().year - 1, 12, 15)
    assert formatter(_iso(d)) == _expected(d, with_year=True)


def test_a_date_several_years_back_shows_its_year(formatter):
    d = date(_today().year - 2, 11, 28)
    assert formatter(_iso(d)) == _expected(d, with_year=True)


def test_a_date_in_a_future_year_shows_its_year(formatter):
    """Notes are planned forward, so the archive is not the only page that
    can hold a date outside this year."""
    d = date(_today().year + 1, 3, 4)
    assert formatter(_iso(d)) == _expected(d, with_year=True)


@pytest.mark.parametrize("offset", [-1, 1])
def test_the_year_boundary_falls_between_dec_31_and_jan_1(formatter, offset):
    """Dec 31 of last year carries a year; Jan 1 of this year does not."""
    year = _today().year
    if offset == -1:
        d = date(year - 1, 12, 31)
        assert formatter(_iso(d)) == _expected(d, with_year=True)
    else:
        d = date(year, 1, 1)
        assert formatter(_iso(d)) == _expected(d, with_year=False)


def test_the_weekday_is_the_real_one(formatter):
    """Guards the off-by-one a UTC parse would introduce.

    `new Date('2025-12-15')` is parsed as UTC and renders as the 14th anywhere
    west of Greenwich; the pages append `T00:00:00` to force a local parse.
    Checked across a year so a single lucky date cannot hide it.
    """
    year = _today().year - 1
    for month in range(1, 13):
        d = date(year, month, 15)
        assert formatter(_iso(d)).startswith(d.strftime("%A")), (
            f"{_iso(d)} labelled {formatter(_iso(d))!r}, expected a {d.strftime('%A')}"
        )


def test_both_pages_label_a_dated_note_identically(browser, live_server):
    """One rule, two implementations — they have to agree.

    A note crosses from /notes to /notes/previous at local midnight, and its
    heading should not change wording as it goes.
    """
    probe = date(_today().year - 1, 7, 4)
    labels = {}
    for name, path in PAGES.items():
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()
        try:
            page.goto(live_server + path, wait_until="networkidle")
            page.wait_for_selector(".list-container .editable-item")
            labels[name] = page.evaluate("(d) => formatDate(d)", _iso(probe))
        finally:
            page.close()
            context.close()

    assert labels["notes"] == labels["notes-previous"], f"the two pages disagree: {labels}"


def test_the_notes_page_names_today_and_tomorrow(browser, live_server):
    """Only the planning page does this; on the archive every date is past."""
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    page = context.new_page()
    try:
        page.goto(live_server + "/notes", wait_until="networkidle")
        page.wait_for_selector(".list-container .editable-item")
        today = _today()
        assert page.evaluate("(d) => formatDate(d)", _iso(today)) == "Today"
        assert page.evaluate("(d) => formatDate(d)", _iso(today + timedelta(days=1))) == "Tomorrow"
    finally:
        page.close()
        context.close()
