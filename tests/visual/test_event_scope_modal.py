"""A series asks which events a save or delete reaches after Save or Delete.

The edit modal used to put three scope buttons in place of Save, and swap them
for three more that deleted when Delete was pressed — after which there was no
way to save at all, and the only way out was a Cancel that closed the modal
and lost the edits. Now the edit modal always reads Save, Cancel, Delete, and
the question lives in a small modal over it (#263).

Everything here is the page's: the API already took a scope on PUT and DELETE,
so only a browser can tell the new flow from the old one.
"""

from __future__ import annotations

from datetime import date, timedelta
from uuid import uuid4

import pytest

# Far enough ahead to stay in the future whenever this runs, and pinned to a
# Tuesday so the weekly rule and the dates below agree.
_NOVEMBER = date(date.today().year + 3, 11, 1)
FIRST = _NOVEMBER + timedelta(days=(1 - _NOVEMBER.weekday()) % 7)
MIDDLE = FIRST + timedelta(weeks=2)


@pytest.fixture
def page(browser, live_server):
    context = browser.new_context(viewport={"width": 1280, "height": 900})
    page = context.new_page()
    try:
        page.goto(live_server + "/calendar", wait_until="networkidle")
        yield page
    finally:
        page.close()
        context.close()


def _create(page, *, rrule=None):
    """An event of its own, and the occurrence the page would hand the modal."""
    base = page.url.split("/calendar")[0]
    title = f"Swim lesson {uuid4().hex[:8]}"
    data = {
        "title": title,
        "start": f"{FIRST.isoformat()}T16:00",
        "end": f"{FIRST.isoformat()}T17:00",
    }
    if rrule:
        data["rrule"] = rrule
    created = page.request.post(base + "/api/events", data=data)
    assert created.ok, created.text()
    occurrence_day = MIDDLE if rrule else FIRST
    rows = page.request.get(
        base + "/api/events",
        params={"start": FIRST.isoformat(), "end": (FIRST + timedelta(weeks=6)).isoformat()},
    ).json()["occurrences"]
    match = [
        o for o in rows if title in o["title"] and o["start_date"] == occurrence_day.isoformat()
    ]
    assert match, f"no occurrence on {occurrence_day}"
    return match[0]


# The page's own view of both modals, read the same way in every test.
_STATE = """() => {
    const shown = id => document.getElementById(id).style.display === 'flex';
    const visible = id => {
        const el = document.getElementById(id);
        return Boolean(el) && el.offsetParent !== null;
    };
    return {
        editOpen: shown('event-modal-overlay'),
        scopeOpen: shown('event-scope-modal-overlay'),
        title: document.getElementById('event-scope-modal-title').textContent,
        confirm: document.getElementById('btn-scope-confirm').textContent,
        confirmDisabled: document.getElementById('btn-scope-confirm').disabled,
        checked: [...document.querySelectorAll('input[name="event-scope"]:checked')].map(i => i.value),
        help: document.getElementById('event-scope-modal-help').textContent,
        eventTitle: document.getElementById('event-title').value,
        buttons: ['btn-save-event', 'btn-cancel-event', 'btn-delete-event'].filter(visible),
    };
}"""

# Records every write the page sends, and optionally fails them all.
_WATCH = """(fail) => {
    window.__sent = [];
    window.__alerts = [];
    window.alert = m => window.__alerts.push(m);
    const realFetch = window.__realFetch || window.fetch;
    window.__realFetch = realFetch;
    window.fetch = (url, options) => {
        const method = options && options.method;
        const write = ['PUT', 'POST', 'DELETE'].includes(method)
            && !String(url).includes('/describe-recurrence');
        if (!write) return realFetch(url, options);
        window.__sent.push({ method, url: String(url) });
        if (fail) return Promise.resolve(new Response(
            JSON.stringify({ detail: 'Server said no' }),
            { status: 500, headers: { 'Content-Type': 'application/json' } },
        ));
        return realFetch(url, options);
    };
}"""


def _open(page, occurrence):
    page.evaluate("async o => { await openEditEventModal(o); }", occurrence)


def _settle(page):
    page.wait_for_timeout(400)


def test_a_series_offers_save_cancel_and_delete(page):
    _open(page, _create(page, rrule="FREQ=WEEKLY;BYDAY=TU"))
    state = page.evaluate(_STATE)
    assert state["buttons"] == ["btn-save-event", "btn-cancel-event", "btn-delete-event"]
    assert state["scopeOpen"] is False


def test_save_asks_with_nothing_chosen_and_waits_for_a_choice(page):
    _open(page, _create(page, rrule="FREQ=WEEKLY;BYDAY=TU"))
    page.evaluate(_WATCH, False)
    page.click("#btn-save-event")

    opened = page.evaluate(_STATE)
    assert opened["editOpen"] and opened["scopeOpen"]
    assert opened["title"] == "Save repeating event"
    assert opened["confirm"] == "Save"
    assert opened["checked"] == []
    assert opened["confirmDisabled"] is True
    assert page.evaluate("window.__sent") == [], "Save saved before a scope was chosen"

    page.check('input[name="event-scope"][value="this"]')
    assert page.evaluate(_STATE)["confirmDisabled"] is False

    # Reopening starts over: a choice is never carried from last time.
    page.click("#btn-scope-cancel")
    page.click("#btn-save-event")
    again = page.evaluate(_STATE)
    assert again["checked"] == []
    assert again["confirmDisabled"] is True


def test_cancel_goes_back_to_the_edit_modal_with_the_edits(page):
    _open(page, _create(page, rrule="FREQ=WEEKLY;BYDAY=TU"))
    page.evaluate(_WATCH, False)
    page.fill("#event-title", "Swim lesson — moved pool")
    page.click("#btn-delete-event")
    page.check('input[name="event-scope"][value="all"]')
    page.click("#btn-scope-cancel")
    _settle(page)

    state = page.evaluate(_STATE)
    assert state["editOpen"] is True
    assert state["scopeOpen"] is False
    assert state["eventTitle"] == "Swim lesson — moved pool"
    # And Save is still there to use, which the old delete prompt took away.
    assert "btn-save-event" in state["buttons"]
    assert page.evaluate("window.__sent") == []


def test_saving_one_occurrence_sends_that_scope_and_closes_both(page):
    occurrence = _create(page, rrule="FREQ=WEEKLY;BYDAY=TU")
    _open(page, occurrence)
    page.evaluate(_WATCH, False)
    page.fill("#event-location", "North pool")
    page.click("#btn-save-event")
    page.check('input[name="event-scope"][value="this"]')
    page.click("#btn-scope-confirm")
    _settle(page)

    sent = page.evaluate("window.__sent")
    assert [s["method"] for s in sent] == ["PUT"]
    assert "scope=this" in sent[0]["url"]
    assert f"occurrence_date={MIDDLE.isoformat()}" in sent[0]["url"]
    state = page.evaluate(_STATE)
    assert state["editOpen"] is False and state["scopeOpen"] is False


def test_delete_asks_in_delete_words_and_sends_the_chosen_scope(page):
    _open(page, _create(page, rrule="FREQ=WEEKLY;BYDAY=TU"))
    page.evaluate(_WATCH, False)
    page.click("#btn-delete-event")

    state = page.evaluate(_STATE)
    assert state["title"] == "Delete repeating event"
    assert state["confirm"] == "Delete"
    assert state["confirmDisabled"] is True
    assert MIDDLE.isoformat() in state["help"]
    assert "cannot be undone" in state["help"]

    page.check('input[name="event-scope"][value="following"]')
    page.click("#btn-scope-confirm")
    _settle(page)

    sent = page.evaluate("window.__sent")
    assert [s["method"] for s in sent] == ["DELETE"]
    assert "scope=following" in sent[0]["url"]
    assert f"occurrence_date={MIDDLE.isoformat()}" in sent[0]["url"]


def test_a_failed_save_leaves_the_edit_modal_open_with_the_edits(page):
    _open(page, _create(page, rrule="FREQ=WEEKLY;BYDAY=TU"))
    page.evaluate(_WATCH, True)
    page.fill("#event-title", "Swim lesson — will fail")
    page.click("#btn-save-event")
    page.check('input[name="event-scope"][value="all"]')
    page.click("#btn-scope-confirm")
    _settle(page)

    state = page.evaluate(_STATE)
    assert state["scopeOpen"] is False
    assert state["editOpen"] is True
    assert state["eventTitle"] == "Swim lesson — will fail"
    assert page.evaluate("window.__alerts") == ["Server said no"]


def test_an_event_that_does_not_repeat_never_asks(page):
    _open(page, _create(page))
    page.evaluate(_WATCH, False)
    page.fill("#event-location", "Rec center")
    page.click("#btn-save-event")
    _settle(page)

    sent = page.evaluate("window.__sent")
    assert [s["method"] for s in sent] == ["PUT"]
    assert "scope=all" in sent[0]["url"]
    assert page.evaluate(_STATE)["scopeOpen"] is False


def test_deleting_an_event_that_does_not_repeat_still_uses_confirm(page):
    _open(page, _create(page))
    page.evaluate(_WATCH, False)
    asked = []

    def answer(dialog):
        asked.append(dialog.message)
        dialog.dismiss()

    page.on("dialog", answer)
    page.click("#btn-delete-event")
    _settle(page)

    assert asked == ["Delete this event?"]
    assert page.evaluate(_STATE)["scopeOpen"] is False
    assert page.evaluate("window.__sent") == []
