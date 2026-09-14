"""Editing one occurrence must not move it, or duplicate the series around it.

The defect lived entirely in the browser. `openEditEventModal` filled the time
fields from the *series*, so opening any occurrence after the first showed the
wrong date, and `formPayload` put that date in every save whether or not the
user had touched it. `Only this event` wrote it onto the override and the
occurrence disappeared; `This and future` started the tail series there instead
of at the split, so the head and tail overlapped for every occurrence in
between.

The API is not at fault — hand it a payload with no `start` and all three
scopes are already correct, which is why `tests/test_events_api.py` passes
either way. The payload is the evidence and only the page builds it, so this
has to run in a real browser. Same reasoning as
`test_event_recurrence_roundtrip.py`, which guards the recurrence half of the
same `formPayload` habit.
"""

from __future__ import annotations

from datetime import date, timedelta
from uuid import uuid4

import pytest

# Far enough ahead to stay in the future whenever this runs, and pinned to a
# Tuesday so the weekly rule and the dates below agree.
_SEPTEMBER = date(date.today().year + 3, 9, 1)
FIRST = _SEPTEMBER + timedelta(days=(1 - _SEPTEMBER.weekday()) % 7)
MIDDLE = FIRST + timedelta(weeks=2)  # the occurrence that gets edited


@pytest.fixture
def weekly_series(browser, live_server):
    """A weekly series and a calendar page showing it.

    Yields ``(page, event_id, title)``. The title carries a unique suffix
    because the live server keeps one database for the whole module: a
    `following` split makes a *second* event, so a test cannot filter its own
    occurrences by id alone, and filtering by a shared title would sweep in
    every other test's series as well.
    """
    context = browser.new_context(viewport={"width": 1280, "height": 900})
    page = context.new_page()
    title = f"Soccer practice {uuid4().hex[:8]}"
    try:
        created = page.request.post(
            live_server + "/api/events",
            data={
                "title": title,
                "start": f"{FIRST.isoformat()}T17:30",
                "end": f"{FIRST.isoformat()}T18:30",
                "rrule": "FREQ=WEEKLY;BYDAY=TU",
            },
        )
        assert created.ok, created.text()
        page.goto(live_server + "/calendar", wait_until="networkidle")
        yield page, created.json()["id"], title
    finally:
        page.close()
        context.close()


def _series_rows(page, live_server_url, title):
    """Every occurrence belonging to this test's series, head and tail alike."""
    window_end = (FIRST + timedelta(weeks=8)).isoformat()
    rows = page.request.get(
        f"{live_server_url}/api/events",
        params={"start": FIRST.isoformat(), "end": window_end},
    ).json()["occurrences"]
    # Match on the unique suffix alone: a test that renames its series must
    # still find it, and the tail of a split carries the new title.
    return [o for o in rows if title[-8:] in o["title"]]


def _occurrence(page, live_server_url, title, occurrence_date):
    """The real occurrence object the page renders, as the modal receives it."""
    rows = _series_rows(page, live_server_url, title)
    match = [o for o in rows if o["start_date"] == occurrence_date]
    assert match, f"no occurrence on {occurrence_date}"
    return match[0], rows


def _rename(title):
    """A rename that keeps this series distinguishable from every other one."""
    return f"document.getElementById('event-title').value = 'Renamed {title[-8:]}';"


def _edit_and_save(page, occurrence, *, mutate, scope):
    """Drive the real modal on a real occurrence, capturing what it sends."""
    return page.evaluate(
        """async ({ occurrence, mutate, scope }) => {
            const sent = [];
            const realFetch = window.fetch;
            window.fetch = (url, options) => {
                const writing = options && (options.method === 'PUT' || options.method === 'POST');
                const isSave = writing && !String(url).includes('/describe-recurrence');
                if (isSave) sent.push({ url: String(url), body: JSON.parse(options.body) });
                return realFetch(url, options);
            };
            try {
                await openEditEventModal(occurrence);
                const shown = {
                    start: document.getElementById('event-start-time').value,
                    end: document.getElementById('event-end-time').value,
                };
                if (mutate) new Function(mutate)();
                document.querySelector(`[data-scope="${scope}"]`).click();
                await new Promise(r => setTimeout(r, 600));
                return { shown, sent };
            } finally {
                window.fetch = realFetch;
            }
        }""",
        {"occurrence": occurrence, "mutate": mutate, "scope": scope},
    )


def test_the_modal_opens_on_the_occurrence_that_was_clicked(weekly_series, live_server):
    page, _event_id, title = weekly_series
    occurrence, _ = _occurrence(page, live_server, title, MIDDLE.isoformat())

    result = _edit_and_save(page, occurrence, mutate=_rename(title), scope="this")

    # The form showed the occurrence's own date, not the series' first date.
    assert result["shown"]["start"] == f"{MIDDLE.isoformat()}T17:30"
    assert result["shown"]["start"] != f"{FIRST.isoformat()}T17:30"


def test_renaming_one_occurrence_sends_no_times_and_moves_nothing(weekly_series, live_server):
    page, _event_id, title = weekly_series
    occurrence, before = _occurrence(page, live_server, title, MIDDLE.isoformat())
    dates_before = sorted(o["start_date"] for o in before)

    result = _edit_and_save(page, occurrence, mutate=_rename(title), scope="this")

    assert result["sent"], "the save never reached the network"
    body = result["sent"][-1]["body"]
    # An untouched time says nothing about itself. Sending it is the whole bug.
    assert "start" not in body, f"the page sent a start it was not asked to change: {body!r}"
    assert "end" not in body

    rows = _series_rows(page, live_server, title)
    assert sorted(o["start_date"] for o in rows) == dates_before
    renamed = [o for o in rows if o["start_date"] == MIDDLE.isoformat()]
    assert [o["title"] for o in renamed] == [f"Renamed {title[-8:]}"]


def test_this_and_future_does_not_duplicate_the_earlier_occurrences(weekly_series, live_server):
    page, _event_id, title = weekly_series
    occurrence, before = _occurrence(page, live_server, title, MIDDLE.isoformat())
    dates_before = sorted(o["start_date"] for o in before)

    _edit_and_save(page, occurrence, mutate=_rename(title), scope="following")

    rows = _series_rows(page, live_server, title)
    dates_after = sorted(o["start_date"] for o in rows)

    # The split must land on MIDDLE, not on the series' start: starting the
    # tail at FIRST is what drew the first two Tuesdays twice.
    assert dates_after == dates_before, f"the series duplicated: {dates_after}"
    titles = {o["start_date"]: o["title"] for o in rows}
    assert titles[FIRST.isoformat()] == title
    assert titles[MIDDLE.isoformat()] == f"Renamed {title[-8:]}"


def test_a_deliberate_time_change_still_moves_the_occurrence(weekly_series, live_server):
    """The omission must not swallow an edit the user actually made."""
    page, _event_id, title = weekly_series
    occurrence, _ = _occurrence(page, live_server, title, MIDDLE.isoformat())
    moved_to = f"{MIDDLE.isoformat()}T19:00"

    result = _edit_and_save(
        page,
        occurrence,
        mutate=(
            f"document.getElementById('event-start-time').value = '{moved_to}';"
            "document.getElementById('event-start-time').dispatchEvent(new Event('change'));"
        ),
        scope="this",
    )

    body = result["sent"][-1]["body"]
    assert body.get("start") == moved_to, f"a real time change was dropped: {body!r}"

    rows = _series_rows(page, live_server, title)
    moved = [o for o in rows if o["start_date"] == MIDDLE.isoformat()]
    assert moved and moved[0]["time_label"] == "7:00 PM"
