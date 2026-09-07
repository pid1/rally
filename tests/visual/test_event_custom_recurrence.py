"""The custom recurrence controls, driven in a real browser.

Two things are being protected. First, that each control compiles to the RRULE
it claims to — the vocabulary is small but every entry has a way of being subtly
wrong, and `BYMONTHDAY=31` against `BYMONTHDAY=-1` is the example that matters.
Second, that a rule survives a round trip through the form: saved, reopened, and
read back into the same controls. A form that cannot reload its own output is
how a recurrence quietly narrows.

The compile step runs in the page rather than in Python on purpose. Rally has
exactly one RRULE builder and it is JavaScript; a Python reimplementation here
would test the reimplementation.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

# A Tuesday, far enough ahead to stay in the future whenever this runs.
_SEPTEMBER = date(date.today().year + 3, 9, 1)
TUESDAY = _SEPTEMBER + timedelta(days=(1 - _SEPTEMBER.weekday()) % 7)


@pytest.fixture(scope="module")
def calendar_page(browser, live_server):
    """One calendar page with the Add Event modal open, reused across cases."""
    context = browser.new_context(viewport={"width": 1280, "height": 1000})
    page = context.new_page()
    page.goto(live_server + "/calendar", wait_until="networkidle")
    page.evaluate(
        """(start) => {
            openAddEventModal(start);
            document.getElementById('event-start-time').value = `${start}T16:00`;
            document.getElementById('event-end-time').value = `${start}T17:00`;
        }""",
        TUESDAY.isoformat(),
    )
    yield page
    page.close()
    context.close()


def build(page, script: str) -> dict:
    """Set the controls, then read back what the form would send and say."""
    return page.evaluate(
        """async (script) => {
            const fire = (el, t = 'change') => el.dispatchEvent(new Event(t, { bubbles: true }));
            const repeat = document.getElementById('event-repeat');
            repeat.value = 'custom';
            fire(repeat);
            // Defaults, so one case cannot leak into the next.
            document.getElementById('event-custom-interval').value = '1';
            document.getElementById('event-custom-weekdays-only').checked = false;
            document.querySelectorAll('#event-custom-weekdays input').forEach(i => { i.checked = false; });
            document.querySelector('input[name="event-custom-monthly-mode"][value="day"]').checked = true;
            document.querySelector('input[name="event-ends"][value="never"]').checked = true;
            new Function('fire', script)(fire);
            updateCustomGroups();
            updateEndsGroups();
            await runRecurrencePreview();
            const allDay = document.getElementById('event-all-day').checked;
            const start = allDay
                ? document.getElementById('event-start-date').value
                : document.getElementById('event-start-time').value;
            return {
                rrule: compileRepeat('custom', start, allDay),
                preview: document.getElementById('event-recurrence-preview').textContent,
                intervalDisabled: document.getElementById('event-custom-interval').disabled,
                monthDayHint: document.getElementById('event-custom-monthly-day-hint').offsetParent !== null,
            };
        }""",
        script,
    )


SET_FREQ = "const f=document.getElementById('event-custom-freq'); f.value=%r; fire(f);"


# --- Each control compiles to the rule it claims -------------------------------


@pytest.mark.parametrize(
    ("script", "rrule", "preview"),
    [
        (
            SET_FREQ % "daily" + "document.getElementById('event-custom-interval').value='3';",
            "FREQ=DAILY;INTERVAL=3",
            "Repeats every 3 days.",
        ),
        (
            SET_FREQ % "daily"
            + "const w=document.getElementById('event-custom-weekdays-only'); w.checked=true; fire(w);",
            "FREQ=DAILY;BYDAY=MO,TU,WE,TH,FR",
            "Repeats every weekday.",
        ),
        (
            SET_FREQ % "weekly"
            + "document.getElementById('event-custom-interval').value='2';"
            + "document.querySelectorAll('#event-custom-weekdays input')"
            + ".forEach(i=>{ i.checked=['TU','TH'].includes(i.value); });",
            "FREQ=WEEKLY;INTERVAL=2;BYDAY=TU,TH",
            "Repeats every 2 weeks on Tuesday and Thursday.",
        ),
        (
            SET_FREQ % "monthly"
            + "document.getElementById('event-custom-monthly-day').value='-1';",
            "FREQ=MONTHLY;BYMONTHDAY=-1",
            "Repeats monthly on the last day.",
        ),
        (
            SET_FREQ % "monthly"
            + 'document.querySelector(\'input[name="event-custom-monthly-mode"][value="weekday"]\').checked=true;'
            + "document.getElementById('event-custom-ordinal').value='1';"
            + "document.getElementById('event-custom-weekday').value='SU';",
            "FREQ=MONTHLY;BYDAY=1SU",
            "Repeats monthly on the first Sunday.",
        ),
        (
            SET_FREQ % "monthly"
            + 'document.querySelector(\'input[name="event-custom-monthly-mode"][value="weekday"]\').checked=true;'
            + "document.getElementById('event-custom-ordinal').value='-1';"
            + "document.getElementById('event-custom-weekday').value='FR';",
            "FREQ=MONTHLY;BYDAY=-1FR",
            "Repeats monthly on the last Friday.",
        ),
        (
            SET_FREQ % "yearly" + "document.getElementById('event-custom-interval').value='2';",
            "FREQ=YEARLY;INTERVAL=2",
            "Repeats every 2 years.",
        ),
    ],
)
def test_the_controls_compile_to_the_rule_they_describe(calendar_page, script, rrule, preview):
    result = build(calendar_page, script)
    assert result["rrule"] == rrule
    assert result["preview"] == preview


def test_last_day_is_not_a_synonym_for_the_31st(calendar_page):
    """`BYMONTHDAY=31` skips the five short months; `-1` never does.

    Both are legal and they mean different things, which is the whole reason
    `Last day` is an entry rather than an alias.
    """
    last = build(
        calendar_page,
        SET_FREQ % "monthly" + "document.getElementById('event-custom-monthly-day').value='-1';",
    )
    thirty_first = build(
        calendar_page,
        SET_FREQ % "monthly" + "document.getElementById('event-custom-monthly-day').value='31';",
    )
    assert last["rrule"] != thirty_first["rrule"]
    assert last["preview"] == "Repeats monthly on the last day."
    assert thirty_first["preview"] == "Repeats monthly on the 31st."
    # The short-month warning belongs only on the choice that has the problem.
    assert thirty_first["monthDayHint"] is True
    assert last["monthDayHint"] is False


def test_every_weekday_pins_the_interval_rather_than_ignoring_it(calendar_page):
    """RRULE cannot say "every third weekday", so the field is disabled, not ignored."""
    result = build(
        calendar_page,
        SET_FREQ % "daily"
        + "document.getElementById('event-custom-interval').value='3';"
        + "const w=document.getElementById('event-custom-weekdays-only'); w.checked=true; fire(w);",
    )
    assert result["rrule"] == "FREQ=DAILY;BYDAY=MO,TU,WE,TH,FR"
    assert result["intervalDisabled"] is True


def test_a_weekly_rule_with_no_day_selected_builds_nothing(calendar_page):
    """Better to save no recurrence than to invent a day the user did not pick."""
    result = build(calendar_page, SET_FREQ % "weekly")
    assert result["rrule"] is None
    assert "at least one day" in result["preview"]


# --- Bounds --------------------------------------------------------------------


def test_an_end_date_becomes_an_inclusive_until(calendar_page):
    end = TUESDAY + timedelta(weeks=10)
    result = build(
        calendar_page,
        SET_FREQ % "daily"
        + 'const r=document.querySelector(\'input[name="event-ends"][value="until"]\'); r.checked=true; fire(r);'
        + f"document.getElementById('event-ends-until').value='{end.isoformat()}';",
    )
    assert result["rrule"] == f"FREQ=DAILY;UNTIL={end.strftime('%Y%m%d')}T235959Z"
    assert "until" in result["preview"]


def test_a_count_bound_is_written_as_count(calendar_page):
    result = build(
        calendar_page,
        SET_FREQ % "daily"
        + 'const r=document.querySelector(\'input[name="event-ends"][value="count"]\'); r.checked=true; fire(r);'
        + "document.getElementById('event-ends-count').value='8';",
    )
    assert result["rrule"] == "FREQ=DAILY;COUNT=8"
    assert result["preview"] == "Repeats daily, 8 times."


def test_ends_is_offered_for_the_fixed_choices_too(calendar_page):
    """A bound is orthogonal to a cadence — needing `Custom` to get one would be arbitrary."""
    shown = calendar_page.evaluate(
        """() => {
            const repeat = document.getElementById('event-repeat');
            const seen = {};
            for (const choice of ['', 'daily', 'weekly', 'monthly', 'yearly', 'custom']) {
                repeat.value = choice;
                repeat.dispatchEvent(new Event('change', { bubbles: true }));
                seen[choice || 'none'] =
                    document.getElementById('event-ends-group').offsetParent !== null;
            }
            return seen;
        }"""
    )
    assert shown["none"] is False, "a one-off event has nothing to end"
    for choice in ("daily", "weekly", "monthly", "yearly", "custom"):
        assert shown[choice] is True, f"Ends should be offered for {choice}"


def test_an_end_before_the_start_is_called_out_in_the_form(calendar_page):
    """The contradiction is caught where it is made, not by a failed save."""
    before = TUESDAY - timedelta(days=30)
    result = build(
        calendar_page,
        SET_FREQ % "daily"
        + 'const r=document.querySelector(\'input[name="event-ends"][value="until"]\'); r.checked=true; fire(r);'
        + f"document.getElementById('event-ends-until').value='{before.isoformat()}';",
    )
    assert "never repeat" in result["preview"]


# --- The round trip ------------------------------------------------------------


@pytest.mark.parametrize(
    "rrule",
    [
        "FREQ=DAILY;INTERVAL=3",
        "FREQ=DAILY;BYDAY=MO,TU,WE,TH,FR",
        "FREQ=WEEKLY;INTERVAL=2;BYDAY=TU,TH",
        "FREQ=MONTHLY;BYMONTHDAY=-1",
        "FREQ=MONTHLY;INTERVAL=3;BYDAY=1SU",
        "FREQ=YEARLY;INTERVAL=2",
        "FREQ=WEEKLY;BYDAY=TU;COUNT=8",
    ],
)
def test_a_saved_rule_reloads_into_the_same_rule(browser, live_server, rrule):
    """Save, reopen, recompile — the form must reproduce exactly what it stored.

    This is the property that keeps a recurrence from narrowing over successive
    edits, and it catches a parse and a compile that disagree, which no
    one-directional test can.
    """
    context = browser.new_context(viewport={"width": 1280, "height": 1000})
    page = context.new_page()
    try:
        created = page.request.post(
            live_server + "/api/events",
            data={
                "title": f"Round trip {rrule}",
                "start": f"{TUESDAY.isoformat()}T16:00",
                "end": f"{TUESDAY.isoformat()}T17:00",
                "rrule": rrule,
            },
        )
        assert created.ok, created.text()
        event_id = created.json()["id"]

        page.goto(live_server + "/calendar", wait_until="networkidle")
        recompiled = page.evaluate(
            """async (eventId) => {
                await openEditEventModal({ event_id: eventId, occurrence_date: null });
                await new Promise(r => setTimeout(r, 300));
                const allDay = document.getElementById('event-all-day').checked;
                const start = allDay
                    ? document.getElementById('event-start-date').value
                    : document.getElementById('event-start-time').value;
                const choice = document.getElementById('event-repeat').value;
                return { choice, rrule: compileRepeat(choice, start, allDay) };
            }""",
            event_id,
        )
        assert recompiled["choice"] not in ("", "other"), f"{rrule} was not recognised"
        assert sorted(recompiled["rrule"].split(";")) == sorted(rrule.split(";"))
    finally:
        page.close()
        context.close()
