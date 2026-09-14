"""The `Whose calendar` picker is a page behaviour, so a page has to test it.

Everything worth getting wrong here happens in the browser: the control is
built from two fetches and grouped by owner, it blocks Save on **two** separate
paths, and picking a calendar reaches sideways into the attendee checkboxes.
The API sees only a `calendar_id` that is already correct, so its own tests
cannot tell a working picker from one that never renders.

Same reasoning as `test_occurrence_edit_keeps_its_date.py` and
`test_event_recurrence_roundtrip.py`: the payload is the evidence and only the
page builds it.
"""

from __future__ import annotations

from datetime import date, timedelta
from uuid import uuid4

import pytest

_OCTOBER = date(date.today().year + 3, 10, 1)
FIRST = _OCTOBER + timedelta(days=(1 - _OCTOBER.weekday()) % 7)  # a Tuesday


@pytest.fixture
def calendar_page(browser, live_server):
    """The calendar page, plus the family and native calendars it was seeded with."""
    context = browser.new_context(viewport={"width": 1280, "height": 900})
    page = context.new_page()
    try:
        members = page.request.get(live_server + "/api/family").json()
        calendars = [
            c
            for c in page.request.get(live_server + "/api/calendars").json()
            if c["cal_type"] == "native"
        ]
        assert len(members) > 1, "precondition: the seed needs several family members"
        assert len(calendars) > 1, "precondition: the seed needs several native calendars"
        page.goto(live_server + "/calendar", wait_until="networkidle")
        yield page, members, calendars
    finally:
        page.close()
        context.close()


def test_the_picker_groups_calendars_under_their_owner(calendar_page):
    page, members, calendars = calendar_page

    shape = page.evaluate(
        """() => {
            openAddEventModal('2030-01-01');
            const select = document.getElementById('event-calendar');
            return {
                value: select.value,
                required: select.required,
                placeholder: select.options[0] && select.options[0].textContent,
                groups: [...select.querySelectorAll('optgroup')].map(g => ({
                    owner: g.label,
                    options: [...g.children].map(o => Number(o.value)),
                })),
            };
        }"""
    )

    # Opens unselected: landing on somebody's calendar by position is the bug
    # this control exists to remove.
    assert shape["value"] == ""
    assert shape["required"] is True
    assert "Choose a calendar" in shape["placeholder"]

    # Grouped by owner, in the name order /api/family returns — the same order
    # the filter chips and the legend use, so the three lists agree.
    owners_shown = [g["owner"] for g in shape["groups"]]
    expected = [
        m["name"] for m in members if any(c["family_member_id"] == m["id"] for c in calendars)
    ]
    assert owners_shown == expected

    # Every option sits under the member who actually owns that calendar.
    by_id = {c["id"]: c["family_member_id"] for c in calendars}
    name_of = {m["id"]: m["name"] for m in members}
    for group in shape["groups"]:
        for calendar_id in group["options"]:
            assert name_of[by_id[calendar_id]] == group["owner"]


def test_save_is_blocked_until_a_calendar_is_chosen(calendar_page):
    """The plain Save path: the button submits the form, so `required` applies."""
    page, _members, _calendars = calendar_page

    state = page.evaluate(
        """() => {
            openAddEventModal('2030-01-01');
            document.getElementById('event-title').value = 'Unassigned';
            const form = document.getElementById('event-form');
            const blocking = [...form.elements].find(el => !el.checkValidity());
            return { valid: form.checkValidity(), blocking: blocking && blocking.id };
        }"""
    )

    assert state["valid"] is False
    assert state["blocking"] == "event-calendar"


def test_the_scope_buttons_are_blocked_too(calendar_page):
    """They call saveEvent() from a click handler and never submit the form, so
    native validation never runs for them — the check has to be in saveEvent."""
    page, _members, calendars = calendar_page
    title = f"Soccer practice {uuid4().hex[:8]}"

    created = page.request.post(
        page.url.split("/calendar")[0] + "/api/events",
        data={
            "title": title,
            "start": f"{FIRST.isoformat()}T17:30",
            "end": f"{FIRST.isoformat()}T18:30",
            "rrule": "FREQ=WEEKLY;BYDAY=TU",
            "calendar_id": calendars[0]["id"],
        },
    )
    assert created.ok, created.text()

    result = page.evaluate(
        """async ({ eventId, occurrenceDate, windowStart, windowEnd }) => {
            const alerts = [];
            const realAlert = window.alert;
            const realFetch = window.fetch;
            const sent = [];
            window.alert = (m) => alerts.push(m);
            window.fetch = (url, options) => {
                if (options && options.method === 'PUT') sent.push(String(url));
                return realFetch(url, options);
            };
            try {
                const page = await (await realFetch(
                    `/api/events?start=${windowStart}&end=${windowEnd}`
                )).json();
                const occurrence = page.occurrences.find(
                    o => o.event_id === eventId && o.start_date === occurrenceDate
                );
                if (!occurrence) throw new Error('no occurrence to edit');
                await openEditEventModal(occurrence);
                document.getElementById('event-calendar').value = '';
                document.querySelector('[data-scope="following"]').click();
                await new Promise(r => setTimeout(r, 400));
                return { alerts, sent };
            } finally {
                window.alert = realAlert;
                window.fetch = realFetch;
            }
        }""",
        {
            "eventId": created.json()["id"],
            "occurrenceDate": FIRST.isoformat(),
            "windowStart": FIRST.isoformat(),
            "windowEnd": (FIRST + timedelta(weeks=4)).isoformat(),
        },
    )

    assert result["alerts"], "the scope path saved without a calendar"
    assert "calendar" in result["alerts"][0].lower()
    assert result["sent"] == [], f"a save reached the network anyway: {result['sent']}"


def test_picking_a_calendar_checks_its_owner_and_never_unchecks(calendar_page):
    page, _members, calendars = calendar_page
    first, second = calendars[0], calendars[1]

    result = page.evaluate(
        """({ first, second }) => {
            openAddEventModal('2030-01-01');
            const select = document.getElementById('event-calendar');
            const checked = () => [...document.querySelectorAll('#event-attendees input:checked')]
                .map(i => Number(i.value));

            const before = checked();
            select.value = String(first.id);
            select.dispatchEvent(new Event('change'));
            const afterFirst = checked();

            select.value = String(second.id);
            select.dispatchEvent(new Event('change'));
            const afterSecond = checked();

            // A deliberate uncheck must stick: the auto-check is a convenience,
            // not a lock.
            document.getElementById(`event-attendee-${first.owner}`).checked = false;
            select.dispatchEvent(new Event('change'));
            return { before, afterFirst, afterSecond, afterUncheck: checked() };
        }""",
        {
            "first": {"id": first["id"], "owner": first["family_member_id"]},
            "second": {"id": second["id"], "owner": second["family_member_id"]},
        },
    )

    assert result["before"] == []
    assert result["afterFirst"] == [first["family_member_id"]]
    # The previous owner stays checked: moving an event is not a statement that
    # they stopped caring about it, and dropping a recipient silently stops a
    # reminder.
    assert sorted(result["afterSecond"]) == sorted(
        {first["family_member_id"], second["family_member_id"]}
    )
    assert first["family_member_id"] not in result["afterUncheck"]
    assert second["family_member_id"] in result["afterUncheck"]
