"""The calendar shows the server's date and reminder labels (#304).

The wording is pinned in ``tests/test_display.py``. What only a browser can show
is that the page puts the server's labels where a family reads them — the
detail view's `When` and `Reminder` rows and the `Delete event` dialog — and
formats no date of its own there. Expected strings come from the same helpers
the server uses, so this tests the wiring rather than the wording twice.
"""

from __future__ import annotations

from datetime import date, timedelta
from uuid import uuid4

import pytest

from rally.utils.display import display_date, display_date_range, display_dated_times

# Far enough ahead to stay in the future whenever this runs.
DAY = date(date.today().year + 3, 10, 15)


@pytest.fixture
def page(browser, live_server):
    context = browser.new_context(viewport={"width": 1280, "height": 900})
    page = context.new_page()
    try:
        page.goto(live_server + "/calendar", wait_until="networkidle")
        page.wait_for_function("typeof openDetail === 'function'")
        yield page
    finally:
        page.close()
        context.close()


def _base(page):
    return page.url.split("/calendar")[0]


def _event(page, **payload):
    payload.setdefault("title", f"Outing {uuid4().hex[:8]}")
    response = page.request.post(_base(page) + "/api/events", data=payload)
    assert response.ok, response.text()
    return response.json()


def _occurrence(page, event):
    rows = page.request.get(
        _base(page) + "/api/events",
        params={"start": DAY.isoformat(), "end": (DAY + timedelta(days=14)).isoformat()},
    ).json()["occurrences"]
    return next(o for o in rows if o["event_id"] == event["id"])


def _open(page, event):
    page.evaluate("o => openDetail(o)", _occurrence(page, event))
    page.wait_for_selector("#detail-value-when")


def test_a_timed_event_reads_its_date_and_reminder_in_words(page):
    event = _event(
        page,
        start=f"{DAY.isoformat()}T16:00",
        end=f"{DAY.isoformat()}T16:30",
        notify_minutes_before=60,
    )
    _open(page, event)
    assert page.inner_text("#detail-value-when") == (
        f"{display_date(DAY.isoformat())} · 4:00 PM – 4:30 PM"
    )
    assert page.inner_text("#detail-value-reminder") == "1 hour before"


def test_an_odd_reminder_is_still_worded(page):
    event = _event(
        page,
        start=f"{DAY.isoformat()}T09:00",
        end=f"{DAY.isoformat()}T10:00",
        notify_minutes_before=3075,
    )
    _open(page, event)
    assert page.inner_text("#detail-value-reminder") == "2 days 3 hours 15 minutes before"


def test_no_reminder_has_no_row(page):
    event = _event(page, start=f"{DAY.isoformat()}T09:00", end=f"{DAY.isoformat()}T10:00")
    _open(page, event)
    assert page.query_selector("#detail-row-reminder") is None


def test_an_all_day_event_across_several_days(page):
    last = DAY + timedelta(days=2)
    event = _event(page, all_day=True, start=DAY.isoformat(), end=last.isoformat())
    _open(page, event)
    assert page.inner_text("#detail-value-when") == (
        f"{display_date_range(DAY.isoformat(), last.isoformat())} · All day"
    )


def test_an_overnight_event_dates_each_end_and_names_no_zone(page):
    morning = DAY + timedelta(days=1)
    event = _event(page, start=f"{DAY.isoformat()}T22:30", end=f"{morning.isoformat()}T06:15")
    _open(page, event)
    when = page.inner_text("#detail-value-when")
    assert when == display_dated_times(DAY.isoformat(), "10:30 PM", morning.isoformat(), "6:15 AM")
    assert not any(zone in when for zone in ("CDT", "CST", "UTC"))


def test_the_delete_dialog_names_the_packing_list_day_in_the_same_words(page):
    template = page.request.post(
        _base(page) + "/api/packing-list-templates", data={"name": f"Bag {uuid4().hex[:8]}"}
    ).json()
    event = _event(
        page,
        all_day=True,
        start=DAY.isoformat(),
        end=DAY.isoformat(),
        packing_list_template_ids=[template["id"]],
    )
    page.evaluate("async o => { await openEditEventModal(o); }", _occurrence(page, event))
    page.click("#btn-delete-event")
    page.wait_for_selector("#dialog-modal-overlay", state="visible")
    message = page.inner_text("#dialog-modal-message")
    assert message.endswith(f"off {display_date(DAY.isoformat())} too.")
    page.click("#btn-dialog-modal-cancel")
