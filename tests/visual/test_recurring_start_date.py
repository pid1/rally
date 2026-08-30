"""The Starts row and the recurrence read-back, measured in a real browser.

The design-system probe only ever sees a *closed* modal — an overlay with no
size measures as nothing — so the tallest modal in the app is exactly the one
its assertions cannot reach. This opens it at each viewport and measures the two
things this feature added: a date row and a preview line whose text comes back
from the server.
"""

from __future__ import annotations

from datetime import date

import pytest

# A start date far enough ahead that it is still the future whenever this runs,
# and on a day-1 boundary so the monthly floor lands exactly on it.
START_YEAR = date.today().year + 3
START_DATE = f"{START_YEAR}-01-01"

VIEWPORTS = {
    "mobile": (390, 844),
    "tablet": (834, 1112),
    "desktop": (1440, 900),
}

MEASURE_JS = r"""() => {
  const px = (v) => Math.round(v * 10) / 10;
  const box = (el) => {
    const r = el.getBoundingClientRect();
    return { x: px(r.x), y: px(r.y), w: px(r.width), h: px(r.height), bottom: px(r.bottom) };
  };
  const content = document.querySelector('#recurring-modal-overlay .modal-content');
  return {
    modal: box(content),
    start: box(document.getElementById('recurring-start-date')),
    hint: document.getElementById('recurring-start-hint').textContent.trim(),
    lead: document.querySelector('.recurrence-preview-lead').textContent.trim(),
    rest: document.querySelector('.recurrence-preview-rest').textContent.trim(),
    previewWidth: px(document.getElementById('recurrence-preview').getBoundingClientRect().width),
    bodyWidth: px(document.querySelector('#recurring-modal-overlay .modal-body')
        .getBoundingClientRect().width),
    viewportHeight: window.innerHeight,
    horizontalOverflow:
      document.documentElement.scrollWidth > document.documentElement.clientWidth + 1,
  };
}"""


@pytest.fixture(scope="module")
def recurring_modal(browser, live_server):
    """Open Add Recurring at a viewport and return the measurement."""
    cache: dict[str, dict] = {}

    def _open(viewport: str) -> dict:
        if viewport in cache:
            return cache[viewport]
        width, height = VIEWPORTS[viewport]
        context = browser.new_context(
            viewport={"width": width, "height": height},
            is_mobile=(viewport == "mobile"),
            has_touch=(viewport != "desktop"),
        )
        page = context.new_page()
        try:
            page.goto(live_server + "/todo", wait_until="networkidle")
            page.click("#btn-add-recurring")
            page.select_option("#recurring-type", "monthly")
            page.fill("#recurring-start-date", START_DATE)
            # The line is server-rendered data, so wait for the date rather than
            # for a timeout: an empty box would pass every assertion below.
            page.wait_for_function(
                "year => (document.querySelector('.recurrence-preview-lead') || {}).textContent"
                "?.includes(year)",
                arg=str(START_YEAR),
            )
            cache[viewport] = page.evaluate(MEASURE_JS)
        finally:
            page.close()
            context.close()
        return cache[viewport]

    return _open


@pytest.mark.parametrize("viewport", list(VIEWPORTS))
def test_the_starts_row_reads_back_the_dates_the_rule_produces(recurring_modal, viewport):
    data = recurring_modal(viewport)

    # Monthly on the 1st from 1 January: the floor lands on the start date.
    weekday = date(START_YEAR, 1, 1).strftime("%A")
    assert data["lead"] == f"First task: {weekday}, January 1, {START_YEAR}"
    assert data["rest"] == f"then February 1, {START_YEAR}, March 1, {START_YEAR}"
    assert data["hint"].startswith("Leave blank")


@pytest.mark.parametrize("viewport", list(VIEWPORTS))
def test_the_new_row_fits_the_modal_at_every_width(recurring_modal, viewport):
    data = recurring_modal(viewport)

    assert not data["horizontalOverflow"], f"{viewport}: the Starts row pushes the page sideways"
    assert data["previewWidth"] <= data["bodyWidth"] + 1
    assert data["start"]["w"] <= data["bodyWidth"] + 1
    # The chassis caps the modal at 90vh and scrolls inside it; a taller modal
    # would put Save off-screen with nothing to say so.
    assert data["modal"]["bottom"] <= data["viewportHeight"] + 1


def test_the_start_date_is_a_touch_target_on_a_phone(recurring_modal):
    # 44px on a coarse pointer, the rule every other control on this page keeps.
    assert recurring_modal("mobile")["start"]["h"] >= 44
