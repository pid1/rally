"""Save & Add Another on the Add Meal Plan modal, driven in a real browser.

Meals are planned in batches, so the modal's cost is paid once per meal: close,
reopen, re-enter the date, the meal type, the attendees and the cook. This
button pays it once per *batch* instead, keeping everything but the menu text.

The design-system probe cannot see any of this — it measures visible controls
and a closed modal has none — so the button's placement in a three-button row is
unmeasured there too. That is asserted here rather than assumed.
"""

from __future__ import annotations

import json
import urllib.request

import pytest

# Far enough ahead to stay on the planner (which shows today onward) however
# long this suite lives, and unique enough to find again for cleanup.
PLAN_DATE = "2099-03-04"
FIRST_MEAL = "rally-visual-probe first meal"
SECOND_MEAL = "rally-visual-probe second meal"
MARKER = "rally-visual-probe"

MEASURE_JS = r"""() => {
  const px = (v) => Math.round(v * 10) / 10;
  const box = (el) => {
    const r = el.getBoundingClientRect();
    return { x: px(r.x), y: px(r.y), w: px(r.width), h: px(r.height), bottom: px(r.bottom) };
  };
  const addAnother = document.getElementById('btn-save-add-another');
  return {
    overlayShown: getComputedStyle(document.getElementById('modal-overlay')).display !== 'none',
    text: document.getElementById('plan-text').value,
    date: document.getElementById('plan-date').value,
    mealType: document.getElementById('plan-meal-type').value,
    cook: document.getElementById('plan-cook').value,
    attendees: Array.from(document.querySelectorAll('.attendee-cb:checked')).map((cb) => cb.value),
    focused: document.activeElement ? document.activeElement.id : null,
    addAnother: box(addAnother),
    addAnotherShown: getComputedStyle(addAnother).display !== 'none',
    actions: box(document.querySelector('.modal-actions')),
    horizontalOverflow:
      document.documentElement.scrollWidth > document.documentElement.clientWidth + 1,
  };
}"""


def _plans(live_server: str) -> list[dict]:
    with urllib.request.urlopen(f"{live_server}/api/dinner-plans", timeout=5) as resp:
        return json.load(resp)


def _delete_probe_plans(live_server: str) -> None:
    """This suite shares one session-scoped database; leave it as it was found."""
    for plan in _plans(live_server):
        if MARKER in (plan.get("plan") or ""):
            req = urllib.request.Request(
                f"{live_server}/api/dinner-plans/{plan['id']}", method="DELETE"
            )
            urllib.request.urlopen(req, timeout=5).close()


@pytest.fixture(scope="module")
def batch(browser, live_server):
    """Add two meals in one modal session and measure the state between them.

    Returns (after_first_save, after_second_save, plans_created).
    """
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    page = context.new_page()
    try:
        page.goto(live_server + "/dinner-planner", wait_until="networkidle")
        page.click("#btn-add-meal")

        page.fill("#plan-date", PLAN_DATE)
        page.select_option("#plan-meal-type", "Lunch")
        page.check(".attendee-cb >> nth=0")
        page.select_option("#plan-cook", index=1)
        page.fill("#plan-text", FIRST_MEAL)

        page.click("#btn-save-add-another")
        # The save is what clears the field, so waiting on it is waiting on the
        # round trip. A fixed timeout here would pass before anything happened.
        page.wait_for_function("() => document.getElementById('plan-text').value === ''")
        after_first = page.evaluate(MEASURE_JS)

        # The second entry types only the menu — which is the whole point.
        page.fill("#plan-text", SECOND_MEAL)
        page.click("#btn-save-add-another")
        page.wait_for_function("() => document.getElementById('plan-text').value === ''")
        after_second = page.evaluate(MEASURE_JS)

        created = [p for p in _plans(live_server) if MARKER in (p.get("plan") or "")]
        yield after_first, after_second, created
    finally:
        page.close()
        context.close()
        _delete_probe_plans(live_server)


def test_the_modal_stays_open_with_only_the_menu_cleared(batch):
    after_first, _, _ = batch

    assert after_first["overlayShown"], "Save & Add Another closed the modal"
    assert after_first["text"] == "", "the menu text was not cleared for the next entry"
    assert after_first["focused"] == "plan-text", (
        "the next menu should be typeable without reaching for the mouse"
    )


def test_everything_but_the_menu_is_carried_over(batch):
    after_first, _, _ = batch

    assert after_first["date"] == PLAN_DATE
    assert after_first["mealType"] == "Lunch"
    assert after_first["cook"] != ""
    assert len(after_first["attendees"]) == 1, (
        "Who's Eating? was reset — re-checking it is the cost this button removes"
    )


def test_both_meals_reach_the_server_with_the_retained_fields(batch):
    after_first, _, created = batch

    assert len(created) == 2, f"expected two saved meals, got {len(created)}"
    first, second = sorted(created, key=lambda p: p["id"])

    assert first["plan"] == FIRST_MEAL
    assert second["plan"] == SECOND_MEAL
    # The second was saved without re-entering any of these.
    for field in ("date", "meal_type", "cook_id", "attendee_ids"):
        assert first[field] == second[field], f"{field} was not carried to the second meal"
    assert second["date"] == PLAN_DATE
    assert second["meal_type"] == "Lunch"


def test_a_second_save_does_not_overwrite_the_first(batch):
    _, _, created = batch

    ids = {p["id"] for p in created}
    assert len(ids) == 2, "the second save edited the first meal instead of adding one"


def test_edit_mode_does_not_offer_save_and_add_another(browser, live_server):
    """Add and Edit are one modal; only Add is a batch activity."""
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    page = context.new_page()
    try:
        page.goto(live_server + "/dinner-planner", wait_until="networkidle")

        page.click("#btn-add-meal")
        assert page.evaluate(MEASURE_JS)["addAnotherShown"], "Add mode should offer the button"
        page.click("#btn-cancel")

        page.wait_for_selector(".editable-item button:has-text('Edit')")
        page.click(".editable-item button:has-text('Edit') >> nth=0")
        page.wait_for_function(
            "() => document.getElementById('modal-title').textContent === 'Edit Meal Plan'"
        )
        state = page.evaluate(MEASURE_JS)
        assert not state["addAnotherShown"], "Edit mode must not offer Save & Add Another"
    finally:
        page.close()
        context.close()


def test_save_still_closes_the_modal(browser, live_server):
    """The button that existed before this change behaves as it always did."""
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    page = context.new_page()
    try:
        page.goto(live_server + "/dinner-planner", wait_until="networkidle")
        page.click("#btn-add-meal")
        page.fill("#plan-date", PLAN_DATE)
        page.fill("#plan-text", f"{MARKER} plain save")
        page.click("#plan-form button[type=submit]:not(#btn-save-add-another)")
        page.wait_for_function(
            "() => getComputedStyle(document.getElementById('modal-overlay')).display === 'none'"
        )
    finally:
        page.close()
        context.close()
        _delete_probe_plans(live_server)


def test_an_empty_menu_still_blocks_the_save(browser, live_server):
    """Both buttons submit the form, so both get the browser's required check."""
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    page = context.new_page()
    try:
        page.goto(live_server + "/dinner-planner", wait_until="networkidle")
        page.click("#btn-add-meal")
        page.fill("#plan-date", PLAN_DATE)
        page.fill("#plan-text", "")
        page.click("#btn-save-add-another")
        state = page.evaluate(MEASURE_JS)
        assert state["overlayShown"], "an invalid form must not save or close"
        assert not [p for p in _plans(live_server) if MARKER in (p.get("plan") or "")], (
            "an empty menu was saved"
        )
    finally:
        page.close()
        context.close()
        _delete_probe_plans(live_server)


@pytest.mark.parametrize("viewport", [(390, 844), (834, 1112), (1440, 900)])
def test_the_third_button_fits_the_action_row(browser, live_server, viewport):
    """`.btn` sets `white-space: nowrap`, so a long label cannot shrink to fit.

    The row wraps, which is what should absorb it — but nothing in the suite
    measured this row before, and the phone is where it is tightest.
    """
    width, height = viewport
    context = browser.new_context(
        viewport={"width": width, "height": height},
        is_mobile=(width == 390),
        has_touch=(width != 1440),
    )
    page = context.new_page()
    try:
        page.goto(live_server + "/dinner-planner", wait_until="networkidle")
        page.click("#btn-add-meal")
        state = page.evaluate(MEASURE_JS)

        assert not state["horizontalOverflow"], (
            f"{width}px: the action row pushes the page sideways"
        )
        assert state["addAnother"]["w"] <= state["actions"]["w"] + 1, (
            f"{width}px: Save & Add Another is wider than the row that holds it"
        )
        if width == 390:
            # The rule every control keeps on a coarse pointer.
            assert state["addAnother"]["h"] >= 44
    finally:
        page.close()
        context.close()
