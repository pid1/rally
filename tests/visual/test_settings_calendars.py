"""Settings → Calendars, as the page draws it.

Calendars and family members load at the same time, and a calendar row names
its owner, so the row has to be right whichever answer arrives first.
"""

from __future__ import annotations

DESKTOP = {"width": 1280, "height": 900}


def test_a_calendar_row_names_its_owner_when_members_load_last(browser, live_server):
    context = browser.new_context(viewport=DESKTOP)
    page = context.new_page()
    held = []
    # Hold the members back until the calendars are drawn: the order that
    # used to leave every row without its owner.
    page.route("**/api/family", lambda route: held.append(route))
    try:
        page.goto(f"{live_server}/settings")
        page.wait_for_selector("#calendar-list .editable-item")
        while not held:
            page.wait_for_timeout(50)
        for route in held:
            route.continue_()
        page.wait_for_function(
            "() => document.querySelector('#family-list .editable-item') !== null"
        )
        titles = page.eval_on_selector_all(
            "#calendar-list .editable-item-title", "rows => rows.map(r => r.textContent)"
        )
        assert titles and all("—" in title for title in titles), titles
    finally:
        context.close()
