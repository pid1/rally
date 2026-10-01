"""Checklists (issue #249) driven through a real browser.

The API tests pin the two rules (edits reach every day; a check reaches only
its own day). These pin the pages that carry them: that a tick on a day's
checklist is saved and counted without touching anything else, that the
editor's Save & Add Another keeps the group a list is being entered into, that
Group by person and a drag between groups both land, and that adding a
checklist to a day it is already on opens that day instead of failing.

Every test builds its own checklist and deletes it afterwards. The server is
session-scoped and the design-system suite measures the seeded checklist
pages, so a test that changed those would change what the others see.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from datetime import date, timedelta

import pytest

DESKTOP = {"width": 1280, "height": 1400}
PHONE = {"width": 390, "height": 844}


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


def in_days(n: int) -> str:
    # The seed and the server both run in UTC unless a timezone is set, and
    # these dates only need to be safely in the future.
    return (date.today() + timedelta(days=n)).isoformat()


@pytest.fixture
def checklist(live_server):
    """A grouped checklist on two days, deleted (with its days) afterwards."""
    created = api(live_server, "POST", "/api/checklists", {"name": "Browser test trip"})
    cid = created["id"]
    kid = api(live_server, "POST", f"/api/checklists/{cid}/groups", {"name": "Kid"})
    grown = api(live_server, "POST", f"/api/checklists/{cid}/groups", {"name": "Grown-up"})
    items = {
        name: api(
            live_server, "POST", f"/api/checklists/{cid}/items", {"name": name, "group_id": group}
        )
        for name, group in (
            ("Goggles", kid["id"]),
            ("Towel", kid["id"]),
            ("Keys", grown["id"]),
            ("Snacks", None),
        )
    }
    days = [
        api(live_server, "POST", "/api/checklist-days", {"checklist_id": cid, "date": in_days(n)})
        for n in (20, 27)
    ]
    try:
        yield {"id": cid, "groups": {"Kid": kid, "Grown-up": grown}, "items": items, "days": days}
    finally:
        try:
            api(live_server, "DELETE", f"/api/checklists/{cid}")
        except urllib.error.HTTPError:
            pass


def open_page(browser, url, viewport=DESKTOP):
    context = browser.new_context(viewport=viewport)
    page = context.new_page()
    page.on("dialog", lambda dialog: dialog.accept())
    page.goto(url, wait_until="networkidle")
    return context, page


def sections(page) -> dict[str, list[str]]:
    return page.evaluate(
        """() => Object.fromEntries([...document.querySelectorAll('.list-group')].map(g => [
            g.querySelector('.list-group-name')?.textContent.trim() ?? '',
            [...g.querySelectorAll('.editable-item-title')].map(t => t.textContent.trim()),
        ]))"""
    )


def test_a_tick_is_saved_and_counted_and_touches_nothing_else(browser, live_server, checklist):
    first, second = checklist["days"]
    context, page = open_page(browser, f"{live_server}/checklists/days/{first['id']}")
    try:
        assert page.locator("#day-progress").text_content() == "0 of 4 packed"
        page.get_by_label("Packed Goggles").check()
        page.wait_for_function(
            "document.querySelector('#day-progress').textContent === '1 of 4 packed'"
        )
        kid_count = page.locator(".list-group[data-group] .list-group-count").first
        assert kid_count.text_content() == "1 of 2 packed"

        page.reload(wait_until="networkidle")
        assert page.get_by_label("Packed Goggles").is_checked()
    finally:
        context.close()

    assert api(live_server, "GET", f"/api/checklist-days/{second['id']}")["checked"] == 0
    master = api(live_server, "GET", f"/api/checklists/{checklist['id']}")
    assert all("checked" not in item for item in master["items"])


def test_the_checkbox_hit_area_is_a_full_target_on_a_phone(browser, live_server, checklist):
    day = checklist["days"][0]
    context, page = open_page(browser, f"{live_server}/checklists/days/{day['id']}", PHONE)
    try:
        box = page.locator(".item-checkbox").first.bounding_box()
        assert box["width"] >= 44 and box["height"] >= 44
        # The whole square is the control, not only the 18px box inside it.
        page.mouse.click(box["x"] + 3, box["y"] + 3)
        page.wait_for_function(
            "document.querySelector('#day-progress').textContent === '1 of 4 packed'"
        )
    finally:
        context.close()


def test_save_and_add_another_keeps_the_group(browser, live_server, checklist):
    context, page = open_page(browser, f"{live_server}/checklists/{checklist['id']}")
    try:
        page.click("#btn-add-item")
        page.select_option("#item-group", str(checklist["groups"]["Kid"]["id"]))
        page.fill("#item-name", "Swim cap")
        page.click("#btn-save-add-another")
        page.wait_for_function("document.querySelector('#item-name').value === ''")

        assert page.locator("#item-modal-overlay").is_visible()
        assert page.input_value("#item-group") == str(checklist["groups"]["Kid"]["id"])

        page.fill("#item-name", "Flip-flops")
        page.click("#item-form button[type=submit]:not(#btn-save-add-another)")
        page.wait_for_selector("#item-modal-overlay", state="hidden")
        assert sections(page)["Kid"] == ["Goggles", "Towel", "Swim cap", "Flip-flops"]
    finally:
        context.close()


def test_edit_mode_offers_delete_not_add_another(browser, live_server, checklist):
    context, page = open_page(browser, f"{live_server}/checklists/{checklist['id']}")
    try:
        page.locator(".editable-item", has_text="Towel").get_by_role("button", name="Edit").click()
        assert page.locator("#btn-delete-item").is_visible()
        assert not page.locator("#btn-save-add-another").is_visible()
        assert page.input_value("#item-name") == "Towel"
    finally:
        context.close()


def test_group_by_person_adds_the_family(browser, live_server, checklist):
    family = [m["name"] for m in api(live_server, "GET", "/api/family")]
    context, page = open_page(browser, f"{live_server}/checklists/{checklist['id']}")
    try:
        page.click("#btn-manage-groups")
        page.click("#btn-group-by-person")
        page.wait_for_function(
            f"document.querySelectorAll('#group-manage-list .manage-row').length === {2 + len(family)}"
        )
        page.click("#btn-close-groups")
        names = list(sections(page))
        assert names[:2] == ["Kid", "Grown-up"]
        assert set(family) <= set(names)
        assert names[-1] == "General"
    finally:
        context.close()


def test_dragging_an_item_onto_another_group_moves_it(browser, live_server, checklist):
    context, page = open_page(browser, f"{live_server}/checklists/{checklist['id']}")
    try:
        grip = page.locator(".editable-item", has_text="Snacks").locator(".drag-handle")
        box = grip.bounding_box()
        target = page.locator(
            '.list-group:has(.list-group-name:text-is("Grown-up"))'
        ).bounding_box()
        page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
        page.mouse.down()
        page.mouse.move(target["x"] + 200, target["y"] + 30, steps=20)
        # Wait for the save, not a fixed pause: the API is checked below.
        with page.expect_response("**/items/reorder"):
            page.mouse.up()
        page.wait_for_load_state("networkidle")

        assert sections(page)["Grown-up"] == ["Snacks", "Keys"]
    finally:
        context.close()

    snacks = next(
        i
        for i in api(live_server, "GET", f"/api/checklists/{checklist['id']}")["items"]
        if i["name"] == "Snacks"
    )
    assert snacks["group_id"] == checklist["groups"]["Grown-up"]["id"]


def test_adding_to_a_day_it_is_already_on_opens_that_day(browser, live_server, checklist):
    existing = checklist["days"][0]
    context, page = open_page(browser, f"{live_server}/checklists")
    try:
        page.locator(".editable-item", has_text="Browser test trip").get_by_role(
            "button", name="Add to a Day"
        ).click()
        assert page.input_value("#day-checklist") == str(checklist["id"])
        page.fill("#day-date", existing["date"])
        page.click("#day-form ~ .modal-actions button[type=submit]")
        page.wait_for_url(f"**/checklists/days/{existing['id']}")
    finally:
        context.close()


def test_a_new_day_appears_under_coming_up(browser, live_server, checklist):
    context, page = open_page(browser, f"{live_server}/checklists")
    try:
        page.locator(".editable-item", has_text="Browser test trip").get_by_role(
            "button", name="Add to a Day"
        ).click()
        page.fill("#day-date", in_days(30))
        page.fill("#day-label", "Long weekend")
        page.click("#day-form ~ .modal-actions button[type=submit]")
        page.wait_for_selector("#day-modal-overlay", state="hidden")
        # The modal closes before the list is refetched, so wait for the row
        # rather than reading the list the moment the modal hides.
        row = page.locator("#days-container .editable-item", has_text="Long weekend")
        row.wait_for()
        assert row.count() == 1
        assert "0 of 4 packed" in row.text_content()
    finally:
        context.close()
