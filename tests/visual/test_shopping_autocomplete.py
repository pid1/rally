"""The shopping list's item-name suggestions, driven through a real browser.

The suggestions endpoint is tested on its own; these pin the page's wiring of
the shared `attachAutocomplete`: a suggestion names the store it was last
bought at, accepting one fills the store only when none has been chosen, and
× forgets it.

Each test makes its own store and history entry and removes both afterwards.
The server is session-scoped and the design-system suite measures the seeded
shopping page, so nothing here may be left on the list.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

import pytest

DESKTOP = {"width": 1280, "height": 1400}
NAME = "Browsertest quinoa"
STORE = "Browser test market"


def api(base: str, method: str, path: str, payload: dict | None = None):
    request = urllib.request.Request(
        f"{base}{path}",
        data=None if payload is None else json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method=method,
    )
    with urllib.request.urlopen(request) as response:
        body = response.read()
    return json.loads(body) if body else None


def suggestions(base: str) -> list[dict]:
    query = urllib.parse.quote("Browsertest")
    return [
        s for s in api(base, "GET", f"/api/shopping/suggestions?q={query}") if s["name"] == NAME
    ]


@pytest.fixture
def remembered(live_server):
    """A store, and an item name remembered at it but no longer on the list."""
    store = api(live_server, "POST", "/api/shopping/stores", {"name": STORE})
    item = api(live_server, "POST", "/api/shopping/items", {"name": NAME, "store_id": store["id"]})
    api(live_server, "DELETE", f"/api/shopping/items/{item['id']}")
    try:
        yield store
    finally:
        for listed in api(live_server, "GET", "/api/shopping/items?include_hidden=true"):
            if listed["name"] == NAME:
                api(live_server, "DELETE", f"/api/shopping/items/{listed['id']}")
        for suggestion in suggestions(live_server):
            api(live_server, "DELETE", f"/api/shopping/suggestions/{suggestion['id']}")
        try:
            api(live_server, "DELETE", f"/api/shopping/stores/{store['id']}")
        except urllib.error.HTTPError:
            pass


def open_add_modal(browser, live_server):
    context = browser.new_context(viewport=DESKTOP)
    page = context.new_page()
    page.goto(f"{live_server}/shopping", wait_until="networkidle")
    page.click("#btn-add-item")
    page.wait_for_selector("#item-modal-overlay", state="visible")
    return context, page


def option(page):
    return page.locator("#suggestion-menu .autocomplete-option", has_text=NAME)


def test_accepting_a_suggestion_fills_a_blank_store(browser, live_server, remembered):
    context, page = open_add_modal(browser, live_server)
    try:
        page.locator("#item-name").press_sequentially("Browsertest q")
        # The suggestion names the store it was last bought at.
        assert option(page).locator(".autocomplete-detail").text_content() == STORE
        option(page).click()
        assert page.input_value("#item-name") == NAME
        assert page.input_value("#item-store") == str(remembered["id"])
        assert (
            page.locator("#suggestion-menu").evaluate("m => m.classList.contains('open')") is False
        )
    finally:
        context.close()


def test_a_chosen_store_is_never_overwritten(browser, live_server, remembered):
    context, page = open_add_modal(browser, live_server)
    try:
        # Any seeded store other than the one the suggestion remembers.
        other = page.eval_on_selector(
            "#item-store",
            "(s, mine) => [...s.options].map(o => o.value).find(v => v && v !== mine)",
            str(remembered["id"]),
        )
        page.select_option("#item-store", other)

        page.locator("#item-name").press_sequentially("Browsertest q")
        option(page).wait_for()
        page.keyboard.press("ArrowDown")
        page.keyboard.press("Enter")
        assert page.input_value("#item-name") == NAME
        assert page.input_value("#item-store") == other
    finally:
        context.close()


def test_forgetting_a_suggestion_removes_it(browser, live_server, remembered):
    context, page = open_add_modal(browser, live_server)
    try:
        page.locator("#item-name").press_sequentially("Browsertest q")
        with page.expect_response(lambda r: r.request.method == "DELETE"):
            option(page).locator(".autocomplete-dismiss").click()
        option(page).wait_for(state="detached")
        # Forgetting is not accepting: the field keeps what was typed.
        assert page.input_value("#item-name") == "Browsertest q"
    finally:
        context.close()

    assert suggestions(live_server) == []
