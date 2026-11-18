"""Save & Add Another on the Shopping list's Add Item modal (#342), in a real browser.

A shopping list is entered in a burst, often for one store, so the button keeps
the modal open with the store just used, and says under Item what it added,
since the list it went on is behind the modal. An item already open in that
store adds nothing, and says so in the same place.

Each test makes its own store and removes it, its items and their history
afterwards. The server is session-scoped and the design-system suite measures
the seeded shopping page, so nothing here may be left on the list.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

import pytest

DESKTOP = {"width": 1280, "height": 1400}
STORE = "Browser test grocer"
PREFIX = "Browsertest"


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


def probe_items(base: str) -> list[dict]:
    return [
        i
        for i in api(base, "GET", "/api/shopping/items?include_hidden=true")
        if i["name"].startswith(PREFIX)
    ]


@pytest.fixture
def store(live_server):
    created = api(live_server, "POST", "/api/shopping/stores", {"name": STORE})
    try:
        yield created
    finally:
        for item in probe_items(live_server):
            api(live_server, "DELETE", f"/api/shopping/items/{item['id']}")
        query = urllib.parse.quote(PREFIX)
        for suggestion in api(live_server, "GET", f"/api/shopping/suggestions?q={query}"):
            if suggestion["name"].startswith(PREFIX):
                api(live_server, "DELETE", f"/api/shopping/suggestions/{suggestion['id']}")
        try:
            api(live_server, "DELETE", f"/api/shopping/stores/{created['id']}")
        except urllib.error.HTTPError:
            pass


@pytest.fixture
def page(browser, live_server, store):
    context = browser.new_context(viewport=DESKTOP)
    page = context.new_page()
    page.goto(f"{live_server}/shopping", wait_until="networkidle")
    page.click("#btn-add-item")
    page.wait_for_selector("#item-modal-overlay", state="visible")
    try:
        yield page
    finally:
        context.close()


def visible_buttons(page) -> list[str]:
    return page.evaluate(
        """() => [...document.querySelectorAll('#item-modal-overlay .modal-actions .btn')]
            .filter((b) => getComputedStyle(b).display !== 'none')
            .map((b) => b.textContent.trim())"""
    )


def message(page):
    return page.locator("#item-name-message")


def add_another(page, name: str, *, store_id: int | None = None, note: str = "") -> None:
    page.select_option("#item-store", "" if store_id is None else str(store_id))
    page.fill("#item-name", name)
    page.fill("#item-note", note)
    page.click("#btn-save-add-another")
    message(page).wait_for(state="visible")


def test_the_action_row_reads_save_then_the_variant_then_cancel(page):
    assert visible_buttons(page) == ["Save", "Save & Add Another", "Cancel"]


def test_save_and_add_another_keeps_the_store_and_says_what_it_added(page, live_server, store):
    add_another(page, f"{PREFIX} olive oil", store_id=store["id"], note="The big tin")

    assert page.locator("#item-modal-overlay").is_visible()
    assert page.input_value("#item-name") == ""
    assert page.input_value("#item-note") == ""
    assert page.input_value("#item-store") == str(store["id"])
    assert page.evaluate("document.activeElement.id") == "item-name"
    assert message(page).inner_text() == f'"{PREFIX} olive oil" added to the {STORE} list.'
    # A status, which a screen reader reads, so nothing is announced twice.
    assert message(page).get_attribute("role") == "status"
    assert page.locator("#live-status").text_content() == ""
    saved = probe_items(live_server)
    assert [(i["name"], i["store_id"], i["note"]) for i in saved] == [
        (f"{PREFIX} olive oil", store["id"], "The big tin")
    ]


def test_an_item_with_no_store_is_added_to_the_anywhere_list(page):
    add_another(page, f"{PREFIX} batteries")
    assert message(page).inner_text() == f'"{PREFIX} batteries" added to the Anywhere list.'


def test_enter_in_item_is_save_and_add_another(page, live_server, store):
    page.select_option("#item-store", str(store["id"]))
    page.fill("#item-name", f"{PREFIX} capers")
    with page.expect_response("**/api/shopping/items"):
        page.press("#item-name", "Enter")
    message(page).wait_for(state="visible")

    assert page.locator("#item-modal-overlay").is_visible()
    assert page.input_value("#item-name") == ""
    assert page.input_value("#item-store") == str(store["id"])
    assert [i["name"] for i in probe_items(live_server)] == [f"{PREFIX} capers"]


def test_an_item_already_open_in_that_store_adds_nothing_and_says_so(page, live_server, store):
    api(
        live_server,
        "POST",
        "/api/shopping/items",
        {"name": f"{PREFIX} milk", "store_id": store["id"]},
    )
    add_another(page, f"{PREFIX} milk", store_id=store["id"], note="Oat")

    assert message(page).inner_text() == f'"{PREFIX} milk" is already on the {STORE} list.'
    # Everything stays as typed, so the name can be corrected and no note is lost.
    assert page.input_value("#item-name") == f"{PREFIX} milk"
    assert page.input_value("#item-note") == "Oat"
    assert len(probe_items(live_server)) == 1


@pytest.mark.parametrize("change", ["item", "note", "store"])
def test_a_change_to_the_form_clears_the_message(page, store, change):
    """The message is about the last save, so it goes once the form moves on."""
    add_another(page, f"{PREFIX} clears on {change}", store_id=store["id"])
    if change == "item":
        page.type("#item-name", "x")
    elif change == "note":
        page.type("#item-note", "x")
    else:
        page.select_option("#item-store", "")
    assert not message(page).is_visible()


def test_a_new_add_replaces_the_duplicate_message(page, live_server, store):
    api(
        live_server,
        "POST",
        "/api/shopping/items",
        {"name": f"{PREFIX} eggs", "store_id": store["id"]},
    )
    add_another(page, f"{PREFIX} eggs", store_id=store["id"])
    assert "is already on" in message(page).inner_text()
    # Moving it to Anywhere makes it a new item there.
    page.select_option("#item-store", "")
    page.click("#btn-save-add-another")
    page.wait_for_function(
        "() => document.getElementById('item-name-message').textContent.includes('added to')"
    )
    assert message(page).inner_text() == f'"{PREFIX} eggs" added to the Anywhere list.'


def test_a_plain_save_that_adds_closes_and_is_announced(page, live_server):
    page.fill("#item-name", f"{PREFIX} rice")
    page.click("#item-modal-overlay .modal-actions .btn--primary")
    page.wait_for_selector("#item-modal-overlay", state="hidden")
    assert page.locator("#live-status").text_content() == f"Added {PREFIX} rice."
    assert [i["name"] for i in probe_items(live_server)] == [f"{PREFIX} rice"]


def test_edit_mode_hides_the_button_and_enter_saves_and_closes(page, live_server, store):
    item = api(
        live_server,
        "POST",
        "/api/shopping/items",
        {"name": f"{PREFIX} flour", "store_id": store["id"]},
    )
    page.click("#btn-cancel-item")
    page.wait_for_selector("#item-modal-overlay", state="hidden")
    page.reload(wait_until="networkidle")
    page.evaluate(f"openEditModal({item['id']})")
    page.wait_for_selector("#item-modal-overlay", state="visible")

    assert visible_buttons(page) == ["Save", "Cancel", "Delete"]
    page.fill("#item-name", f"{PREFIX} bread flour")
    page.press("#item-name", "Enter")
    page.wait_for_selector("#item-modal-overlay", state="hidden")
    assert [i["name"] for i in probe_items(live_server)] == [f"{PREFIX} bread flour"]
    # An edit is not an add: nothing is said.
    assert page.locator("#live-status").text_content() == ""
