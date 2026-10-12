"""Packing lists on calendar events (#270), as the pages draw and send them.

The API's rules are pinned in ``tests/test_event_packing_lists.py``. What only a
browser can show is the page's side: picking a template adds it, `Put on`
appears and must be answered, the moves the page refuses or asks about, only
the Save button saves, and the Packing Lists page's ⚭, `For:` line, locked date
and bold-name confirmation.
"""

from __future__ import annotations

from datetime import date, timedelta
from uuid import uuid4

import pytest

# Far enough ahead to stay in the future whenever this runs.
DAY = date(date.today().year + 3, 6, 6)
NEXT = DAY + timedelta(days=7)


@pytest.fixture
def page(browser, live_server):
    context = browser.new_context(viewport={"width": 1280, "height": 900})
    page = context.new_page()
    page.on("dialog", lambda dialog: dialog.dismiss())
    try:
        page.goto(live_server + "/calendar", wait_until="networkidle")
        yield page
    finally:
        page.close()
        context.close()


def _base(page):
    return page.url.split("/calendar")[0].split("/packing-lists")[0]


def _template(page, name=None):
    name = name or f"Trip {uuid4().hex[:8]}"
    response = page.request.post(_base(page) + "/api/packing-list-templates", data={"name": name})
    assert response.ok, response.text()
    template = response.json()
    page.request.post(
        _base(page) + f"/api/packing-list-templates/{template['id']}/items",
        data={"name": "Sunscreen"},
    )
    return template


def _event(page, template_ids, *, start=DAY, end=None, title=None):
    title = title or f"Outing {uuid4().hex[:8]}"
    response = page.request.post(
        _base(page) + "/api/events",
        data={
            "title": title,
            "all_day": True,
            "start": start.isoformat(),
            "end": (end or start).isoformat(),
            "packing_list_template_ids": template_ids,
        },
    )
    assert response.ok, response.text()
    return response.json()


def _occurrence(page, event):
    rows = page.request.get(
        _base(page) + "/api/events",
        params={"start": DAY.isoformat(), "end": (NEXT + timedelta(days=7)).isoformat()},
    ).json()["occurrences"]
    return next(o for o in rows if o["event_id"] == event["id"])


def _days_for(page, template_id):
    rows = page.request.get(_base(page) + "/api/packing-list-days").json()
    return [d for d in rows if d["packing_list_template_id"] == template_id]


def _reload_templates(page):
    page.evaluate("async () => { await fetchPackingTemplates(); }")


def _watch_dialogs(page):
    """Answer every dialog with `answer` and keep its text."""
    page.evaluate(
        """() => {
            window.__alerts = [];
            window.__confirms = [];
            window.__answer = false;
            window.alert = m => window.__alerts.push(m);
            window.confirm = m => { window.__confirms.push(m); return window.__answer; };
        }"""
    )


def _pick(page, name):
    page.select_option("#event-packing-select", label=name)


def _picked(page):
    return page.evaluate(
        "() => [...document.querySelectorAll('#event-packing-list .manage-row-text')]"
        ".map(e => e.textContent.trim())"
    )


def _choose_calendar(page):
    value = page.evaluate(
        "() => [...document.querySelectorAll('#event-calendar option')].find(o => o.value).value"
    )
    page.select_option("#event-calendar", value=value)


# --- The event modal -------------------------------------------------------------


def test_picking_a_template_adds_it(page):
    template = _template(page)
    _reload_templates(page)
    page.evaluate("d => openAddEventModal(d)", DAY.isoformat())

    _pick(page, template["name"])

    assert _picked(page) == [template["name"]]
    assert page.input_value("#event-packing-select") == ""
    labels = page.evaluate(
        "() => [...document.querySelectorAll('#event-packing-select option')].map(o => o.textContent)"
    )
    assert template["name"] not in labels
    assert page.locator("#btn-event-packing-add").count() == 0
    # The rows sit on a sunken ground, apart from the label above them.
    background = page.evaluate(
        "() => getComputedStyle(document.querySelector('#event-packing-list .manage-row'))"
        ".backgroundColor"
    )
    assert background == "rgb(245, 245, 245)"


def test_put_on_shows_for_a_multi_day_event_and_must_be_answered(page):
    template = _template(page)
    _reload_templates(page)
    page.evaluate("d => openAddEventModal(d)", DAY.isoformat())
    page.check("#event-all-day")
    page.fill("#event-title", f"Three-day trip {uuid4().hex[:8]}")
    _choose_calendar(page)
    _pick(page, template["name"])
    assert not page.is_visible("#event-packing-span-group")

    page.fill("#event-end-date", (DAY + timedelta(days=2)).isoformat())
    page.dispatch_event("#event-end-date", "change")
    assert page.is_visible("#event-packing-span-group")
    assert page.evaluate(
        "() => [...document.querySelectorAll('input[name=\"event-packing-span\"]')]"
        ".every(r => !r.checked)"
    )

    _watch_dialogs(page)
    page.click("#btn-save-event")
    assert page.evaluate("window.__alerts") == ["Choose which days the packing lists go on"]
    assert _days_for(page, template["id"]) == []

    page.check('input[name="event-packing-span"][value="every"]')
    page.click("#btn-save-event")
    page.wait_for_timeout(500)
    dates = sorted(d["date"] for d in _days_for(page, template["id"]))
    assert dates == [(DAY + timedelta(days=n)).isoformat() for n in range(3)]


def test_enter_in_a_field_does_not_save(page):
    page.evaluate("d => openAddEventModal(d)", DAY.isoformat())
    _choose_calendar(page)
    title = f"Not yet {uuid4().hex[:8]}"
    page.fill("#event-title", title)
    page.press("#event-title", "Enter")
    page.wait_for_timeout(300)
    assert page.evaluate("document.getElementById('event-modal-overlay').style.display") == "flex"
    rows = page.request.get(
        _base(page) + "/api/events", params={"start": DAY.isoformat(), "end": NEXT.isoformat()}
    ).json()["occurrences"]
    assert not [o for o in rows if o["title"] == title]


def test_moving_onto_a_day_already_there_asks_first(page):
    template = _template(page)
    event = _event(page, [template["id"]])
    page.request.post(
        _base(page) + "/api/packing-list-days",
        data={"packing_list_template_id": template["id"], "date": NEXT.isoformat()},
    )
    _reload_templates(page)
    page.evaluate("async o => { await openEditEventModal(o); }", _occurrence(page, event))
    _watch_dialogs(page)
    page.fill("#event-start-date", NEXT.isoformat())
    page.dispatch_event("#event-start-date", "change")

    page.click("#btn-save-event")
    page.wait_for_timeout(500)
    confirms = page.evaluate("window.__confirms")
    assert len(confirms) == 1
    assert confirms[0].startswith(f'"{template["name"]}" is already on ')
    assert page.evaluate("document.getElementById('event-modal-overlay').style.display") == "flex"
    assert sorted(d["date"] for d in _days_for(page, template["id"])) == [
        DAY.isoformat(),
        NEXT.isoformat(),
    ]

    page.evaluate("window.__answer = true")
    page.click("#btn-save-event")
    page.wait_for_timeout(500)
    days = _days_for(page, template["id"])
    assert [d["date"] for d in days] == [NEXT.isoformat()]
    assert [e["event_id"] for e in days[0]["events"]] == [event["id"]]


def test_custom_schedule_is_not_offered_on_a_new_event(page):
    page.evaluate("d => openAddEventModal(d)", DAY.isoformat())
    labels = page.evaluate(
        "() => [...document.querySelectorAll('#event-repeat option')].map(o => o.textContent)"
    )
    assert "Custom schedule" not in labels
    assert "Custom…" in labels


def test_the_detail_view_names_the_lists(page):
    template = _template(page)
    event = _event(page, [template["id"]])
    page.evaluate("o => openDetail(o)", _occurrence(page, event))
    link = page.locator("#detail-value-packing-lists a.inline-link")
    assert link.inner_text() == template["name"]
    assert link.get_attribute("href") == "/packing-lists"


# --- The Packing Lists page --------------------------------------------------------


def _card(page, template):
    return page.locator(f'[data-day-card]:has-text("{template["name"]}")').first


def test_an_event_linked_day_reads_as_one(page):
    template = _template(page)
    title = f"Nana's house overnight and beach day {uuid4().hex[:8]}"
    _event(page, [template["id"]], title=title)
    page.goto(_base(page) + "/packing-lists", wait_until="networkidle")
    card = _card(page, template)

    assert card.locator('.title-indicator[title="From a calendar event"]').inner_text() == "⚭"
    link = card.locator(".editable-item-meta a.inline-link")
    assert link.inner_text() == title
    assert link.get_attribute("href") == "/calendar"

    card.locator("[data-edit-day]").click()
    assert page.is_disabled("#day-edit-date")
    assert page.inner_text("#day-edit-date-note") == (
        f'Set by "{title}". Change the date on the calendar.'
    )
    page.click("#btn-cancel-day-edit")


def test_remove_from_day_asks_with_the_name_in_bold(page):
    template = _template(page)
    title = f"Nana's house overnight and beach day {uuid4().hex[:8]}"
    _event(page, [template["id"]], title=title)
    page.goto(_base(page) + "/packing-lists", wait_until="networkidle")
    card = _card(page, template)
    card.locator("details.disclosure > summary").first.click()
    card.locator("[data-remove-day]").click()

    assert page.inner_text("#confirm-modal-title").lower() == "remove from day"
    assert page.inner_text("#btn-confirm-modal-confirm") == "Remove"
    assert page.inner_text("#confirm-modal-message strong") == template["name"]
    assert page.inner_text("#confirm-modal-message").endswith(
        f'It also comes off "{title}" on that day.'
    )

    page.click("#btn-confirm-modal-cancel")
    assert not page.is_visible("#confirm-modal-overlay")
    assert len(_days_for(page, template["id"])) == 1

    card.locator("[data-remove-day]").click()
    page.click("#btn-confirm-modal-confirm")
    page.wait_for_timeout(500)
    assert _days_for(page, template["id"]) == []


def test_adding_a_list_already_on_that_day_is_refused_in_place(page):
    template = _template(page)
    page.request.post(
        _base(page) + "/api/packing-list-days",
        data={"packing_list_template_id": template["id"], "date": DAY.isoformat()},
    )
    page.goto(_base(page) + "/packing-lists", wait_until="networkidle")
    page.evaluate("() => openAddDayModal()")
    page.check('input[name="day-add-start"][value="template"]')
    page.select_option("#day-add-source", label=template["name"])
    page.check('input[name="day-add-mode"][value="sync"]')
    page.fill("#day-date", DAY.isoformat())
    page.dispatch_event("#day-date", "change")
    page.click('#day-add-modal-overlay button[type="submit"]')
    page.wait_for_timeout(400)

    message = page.locator("#day-add-message")
    assert message.is_visible()
    assert message.inner_text().startswith(f'"{template["name"]}" is already on ')
    assert page.is_visible("#day-add-modal-overlay")

    page.fill("#day-date", NEXT.isoformat())
    page.dispatch_event("#day-date", "change")
    assert not message.is_visible()
