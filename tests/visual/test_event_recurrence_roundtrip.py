"""The event modal must not rewrite a recurrence it was not asked to change.

The defect this guards against lives entirely in the browser: `formPayload` put
a freshly recompiled `RRULE` in every save, and `repeatFromRrule` decided which
choice to recompile from by matching a prefix. A rule richer than the six-value
`Repeats` control — including the `UNTIL` Rally writes itself on a
`This and following` split — was silently narrowed the next time anybody edited
the event, even to change only its location.

The API is not at fault and its own tests pass either way (see
`tests/test_events_api.py`), which is exactly why this has to run in a real
browser: the payload is the evidence, and only the page builds it.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

# Far enough ahead to stay in the future whenever this runs. The series is
# weekly on a Saturday, so the anchor is pinned to one.
_SEPTEMBER = date(date.today().year + 3, 9, 1)
FIRST = _SEPTEMBER + timedelta(days=(5 - _SEPTEMBER.weekday()) % 7)
SPLIT = FIRST + timedelta(weeks=11)  # the Saturday the coach moves practice
LAST_BEFORE_SPLIT = (SPLIT - timedelta(days=1)).isoformat()

WEEKLY_SATURDAY = "FREQ=WEEKLY;BYDAY=SA"


@pytest.fixture
def split_series(browser, live_server):
    """A weekly series truncated by a `following` split, plus a page on it.

    Yields ``(page, event_id, rrule_after_split)``. The split is made through
    the API because it is the *later*, ordinary edit that carries the defect.
    """
    context = browser.new_context(viewport={"width": 1280, "height": 900})
    page = context.new_page()
    try:
        api = page.request
        created = api.post(
            live_server + "/api/events",
            data={
                "title": "Soccer practice",
                "location": "North field",
                "start": f"{FIRST.isoformat()}T09:00",
                "end": f"{FIRST.isoformat()}T10:30",
                "rrule": WEEKLY_SATURDAY,
            },
        )
        assert created.ok, created.text()
        event_id = created.json()["id"]

        split = api.put(
            f"{live_server}/api/events/{event_id}",
            params={"scope": "following", "occurrence_date": SPLIT.isoformat()},
            data={"location": "South field"},
        )
        assert split.ok, split.text()

        truncated = api.get(f"{live_server}/api/events/{event_id}").json()
        assert "UNTIL=" in (truncated["rrule"] or ""), "precondition: the split must write UNTIL"
        assert truncated["series_end_date"] == LAST_BEFORE_SPLIT

        page.goto(live_server + "/calendar", wait_until="networkidle")
        yield page, event_id, truncated["rrule"]
    finally:
        page.close()
        context.close()


def _open_and_save(page, event_id, occurrence_date, *, mutate="", scope="all"):
    """Drive the real modal: open the event, apply `mutate`, save at `scope`.

    Returns the payload the page actually sent, captured off `fetch` rather than
    reconstructed — the point of the test is what leaves the browser.
    """
    return page.evaluate(
        """async ({ eventId, occurrenceDate, mutate, scope }) => {
            const sent = [];
            const realFetch = window.fetch;
            window.fetch = (url, options) => {
                if (options && (options.method === 'PUT' || options.method === 'POST')) {
                    sent.push({ url: String(url), body: JSON.parse(options.body) });
                }
                return realFetch(url, options);
            };
            try {
                await openEditEventModal({ event_id: eventId, occurrence_date: occurrenceDate });
                const repeat = document.getElementById('event-repeat');
                const shown = { value: repeat.value, label: repeat.selectedOptions[0].textContent };
                if (mutate) new Function(mutate)();
                document.querySelector(`[data-scope="${scope}"]`).click();
                await new Promise(r => setTimeout(r, 600));
                return { shown, sent };
            } finally {
                window.fetch = realFetch;
            }
        }""",
        {
            "eventId": event_id,
            "occurrenceDate": occurrence_date,
            "mutate": mutate,
            "scope": scope,
        },
    )


def test_editing_only_the_location_does_not_touch_the_recurrence(split_series):
    page, event_id, rrule_after_split = split_series

    result = _open_and_save(
        page,
        event_id,
        SPLIT.isoformat(),
        mutate="document.getElementById('event-location').value = 'West field';",
    )

    # The form has no control for a bound, so it must not speak about the rule.
    assert result["sent"], "the save never reached the network"
    body = result["sent"][-1]["body"]
    assert "rrule" not in body, f"the page rewrote the recurrence unasked: {body.get('rrule')!r}"

    after = page.request.get(f"{page.url.split('/calendar')[0]}/api/events/{event_id}").json()
    assert after["location"] == "West field"
    assert after["rrule"] == rrule_after_split
    assert after["series_end_date"] == LAST_BEFORE_SPLIT


def test_a_bounded_series_reads_back_as_its_plain_cadence(split_series):
    """`UNTIL` is a bound, not a cadence: the choice shown is still Weekly."""
    page, event_id, _ = split_series

    result = _open_and_save(page, event_id, SPLIT.isoformat())

    assert result["shown"]["value"] == "weekly"
    assert result["shown"]["label"] == "Weekly on this day"


def test_changing_the_cadence_carries_the_bound_forward(split_series):
    page, event_id, _ = split_series

    result = _open_and_save(
        page,
        event_id,
        SPLIT.isoformat(),
        mutate="document.getElementById('event-repeat').value = 'monthly';",
    )

    body = result["sent"][-1]["body"]
    assert "rrule" in body, "a deliberate cadence change must be sent"
    assert body["rrule"].startswith("FREQ=MONTHLY")
    # Switching weekly to monthly changes how often, not until when.
    assert "UNTIL=" in body["rrule"], f"the bound was dropped: {body['rrule']!r}"

    after = page.request.get(f"{page.url.split('/calendar')[0]}/api/events/{event_id}").json()
    assert after["series_end_date"] == LAST_BEFORE_SPLIT


def test_a_rule_the_form_cannot_express_is_named_and_preserved(browser, live_server):
    """Multi-day weekly is beyond the six choices, so it is held, not narrowed."""
    context = browser.new_context(viewport={"width": 1280, "height": 900})
    page = context.new_page()
    try:
        created = page.request.post(
            live_server + "/api/events",
            data={
                "title": "Band practice",
                "start": f"{FIRST.isoformat()}T16:00",
                "end": f"{FIRST.isoformat()}T17:00",
                "rrule": "FREQ=WEEKLY;BYDAY=TU,TH",
            },
        )
        assert created.ok, created.text()
        event_id = created.json()["id"]

        page.goto(live_server + "/calendar", wait_until="networkidle")
        result = _open_and_save(
            page,
            event_id,
            FIRST.isoformat(),
            mutate="document.getElementById('event-location').value = 'Music room';",
        )

        # Not "Weekly on this day" — that would drop Thursday on the next save.
        assert result["shown"]["value"] == "other"
        body = result["sent"][-1]["body"]
        assert "rrule" not in body, f"a rule the form cannot build was rewritten: {body!r}"

        after = page.request.get(f"{live_server}/api/events/{event_id}").json()
        assert after["rrule"] == "FREQ=WEEKLY;BYDAY=TU,TH"
        assert after["location"] == "Music room"
    finally:
        page.close()
        context.close()
