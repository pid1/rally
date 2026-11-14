"""The event form's `Reminders` field (#336).

The API's rules are pinned in ``tests/test_event_reminders.py``. What only a
browser can show is the form: that the add row goes at five, that a `Custom…`
row folds into the server's label when focus leaves it, that a title-only save
leaves a reminder the presets cannot name alone, and that `(changed)` marks an
occurrence with a list of its own.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from uuid import uuid4

import pytest

# Far enough ahead to stay in the future whenever this runs, on a Tuesday.
_NOVEMBER = date(date.today().year + 3, 11, 1)
FIRST = _NOVEMBER + timedelta(days=(1 - _NOVEMBER.weekday()) % 7)


@pytest.fixture
def page(browser, live_server):
    context = browser.new_context(viewport={"width": 1280, "height": 900})
    page = context.new_page()
    try:
        page.goto(live_server + "/calendar", wait_until="networkidle")
        page.wait_for_function("typeof openAddEventModal === 'function'")
        yield page
    finally:
        page.close()
        context.close()


def _base(page):
    return page.url.split("/calendar")[0]


def _occurrence(page, title, day=FIRST):
    rows = page.request.get(
        _base(page) + "/api/events",
        params={"start": FIRST.isoformat(), "end": (FIRST + timedelta(weeks=6)).isoformat()},
    ).json()["occurrences"]
    return next(o for o in rows if o["title"] == title and o["start_date"] == day.isoformat())


def _event(page, **payload):
    title = f"Dentist {uuid4().hex[:8]}"
    data = {
        "title": title,
        "start": f"{FIRST.isoformat()}T15:30",
        "end": f"{FIRST.isoformat()}T16:30",
        **payload,
    }
    response = page.request.post(_base(page) + "/api/events", data=data)
    assert response.ok, response.text()
    return title


def _open_edit(page, occurrence):
    page.evaluate("o => openEditEventModal(o)", occurrence)
    page.wait_for_selector("#event-modal-overlay.active, #event-modal-overlay[style*='flex']")
    page.wait_for_function("document.getElementById('event-title').value !== ''")


def _rows(page):
    return page.locator("#event-reminder-list .manage-row-text").all_inner_texts()


def _add_custom(page, amount, unit):
    page.select_option("#event-reminder-select", "custom")
    index = page.evaluate("remindersChosen.length - 1")
    page.fill(f"#event-reminder-amount-{index}", amount)
    page.select_option(f"#event-reminder-unit-{index}", unit)
    # Leaving the row is what folds it.
    page.click("#event-title")


def test_the_add_row_goes_at_five_and_comes_back(page):
    page.evaluate("openAddEventModal()")
    for value in ("m0", "m10", "m30", "m60", "m120"):
        page.select_option("#event-reminder-select", value)
    assert page.locator("#event-reminder-add-row").is_hidden()
    assert page.inner_text("#event-notify-help") == "Up to 5 reminders."

    page.locator("[data-remove-reminder]").first.click()
    assert page.locator("#event-reminder-add-row").is_visible()


def test_a_custom_reminder_folds_into_the_servers_label(page):
    page.evaluate("openAddEventModal()")
    _add_custom(page, "10", "days")
    page.wait_for_function("!document.querySelector('[data-reminder-row]')")
    assert _rows(page) == ["1 week 3 days before"]

    # An empty one goes quietly.
    page.select_option("#event-reminder-select", "custom")
    page.click("#event-title")
    page.wait_for_function("!document.querySelector('[data-reminder-row]')")
    assert _rows(page) == ["1 week 3 days before"]

    # A duplicate stays open, saying why.
    _add_custom(page, "240", "hours")
    page.wait_for_selector("#event-reminders-message:not([hidden])")
    assert page.inner_text("#event-reminders-message") == "That reminder is already on the list."
    assert page.locator("[data-reminder-row]").count() == 1


def test_an_all_day_custom_reminder_reads_with_its_time(page):
    page.evaluate("openAddEventModal()")
    page.check("#event-all-day")
    page.select_option("#event-reminder-select", "custom")
    page.fill("#event-reminder-amount-0", "2")
    page.fill("#event-reminder-time-0", "18:00")
    page.click("#event-title")
    page.wait_for_function("!document.querySelector('[data-reminder-row]')")
    assert _rows(page) == ["2 days before at 6:00 PM"]


def test_a_title_only_save_sends_no_reminders(page):
    """A 90-minute reminder is not a preset; it used to open as `No reminder`
    and be erased by the next save."""
    title = _event(page, reminders=[{"minutes_before": 90}])
    _open_edit(page, _occurrence(page, title))
    assert _rows(page) == ["1 hour 30 minutes before"]

    page.fill("#event-title", title + " (Emma)")
    with page.expect_request(lambda r: r.method == "PUT" and "/api/events/" in r.url) as sent:
        page.click("#btn-save-event")
    assert "reminders" not in json.loads(sent.value.post_data)


def test_changing_to_all_day_clears_them_and_says_so(page):
    page.evaluate("openAddEventModal()")
    page.select_option("#event-reminder-select", "m60")
    page.check("#event-all-day")
    assert _rows(page) == []
    assert page.inner_text("#event-reminders-message") == (
        "Reminders were cleared because the event changed between all-day and timed."
    )


def test_an_occurrence_with_its_own_list_is_marked_changed(page):
    title = _event(page, rrule="FREQ=WEEKLY;BYDAY=TU", reminders=[{"minutes_before": 60}])
    second = FIRST + timedelta(weeks=1)
    response = page.request.put(
        _base(page) + f"/api/events/{_occurrence(page, title)['event_id']}",
        params={"scope": "this", "occurrence_date": second.isoformat()},
        data={"reminders": [{"minutes_before": 10}]},
    )
    assert response.ok, response.text()

    own = _occurrence(page, title, second)
    page.evaluate("o => openDetail(o)", own)
    # The words as written; `.item-mark` draws them uppercase.
    assert page.text_content("#detail-value-reminders") == "10 minutes before (changed)"
    page.evaluate("hideModalOverlay('detail-modal-overlay')")

    _open_edit(page, own)
    assert page.locator("#event-reminders-changed").is_visible()
    page.evaluate("hideModalOverlay('event-modal-overlay')")
    _open_edit(page, _occurrence(page, title))
    assert page.locator("#event-reminders-changed").is_hidden()


def test_add_event_shows_no_change_history(page):
    page.evaluate("openAddEventModal()")
    assert page.locator("#event-history-link").is_hidden()
