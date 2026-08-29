"""Drag-to-reorder on the shopping list, driven through a real browser.

This is behavior rather than geometry, so it does not read the design-system
probe — but it needs the same seeded server and the same Chromium, and there is
no cheaper way to test it. A drag is pointer capture, hit testing against
`elementFromPoint`, and a commit that reads the DOM back; none of that survives
being unit-tested against a fake.

The touch case goes through CDP's `Input.dispatchTouchEvent` rather than
synthetic `PointerEvent`s on purpose. A dispatched event ignores `touch-action`,
which is exactly the declaration that decides whether a drag on a phone lifts
the row or scrolls the page — testing around it would prove nothing.
"""

from __future__ import annotations

import json
import urllib.request

import pytest

# Tall enough that every seeded row is on screen at once, so a drag never has to
# race the edge auto-scroll to reach its target.
DESKTOP = {"width": 1440, "height": 1600}
PHONE = {"width": 390, "height": 844}

GROUPS_JS = """() => {
    const out = {};
    for (const group of document.querySelectorAll('.shopping-group')) {
        const name = group.querySelector('.shopping-group-name').textContent.trim();
        out[name] = [...group.querySelectorAll('.editable-item:not(.completed)')]
            .map(row => row.querySelector('.editable-item-title').textContent.trim());
    }
    return out;
}"""


def api_get(base: str, path: str):
    with urllib.request.urlopen(f"{base}{path}") as response:
        return json.load(response)


def api_post(base: str, path: str, payload: dict) -> None:
    request = urllib.request.Request(
        f"{base}{path}",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    urllib.request.urlopen(request).close()


def restore_arrangement(base: str, snapshot: list[dict]) -> None:
    """Put the seeded list back exactly as it was.

    The listing comes back in render order, so replaying it one store at a time
    restores both the arrangement and any store an item was dragged out of.
    """
    grouped: dict[int | None, list[int]] = {}
    for item in snapshot:
        grouped.setdefault(item["store_id"], []).append(item["id"])
    for store_id, item_ids in grouped.items():
        api_post(
            base,
            "/api/shopping/items/reorder",
            {"store_id": store_id, "item_ids": item_ids},
        )


@pytest.fixture
def shopping(browser, live_server):
    """A /shopping page, with the seeded arrangement put back afterwards.

    `live_server` is session-scoped and every other visual test measures the
    same seeded pages, so a test that leaves an item in a different store has
    quietly changed what those tests are looking at.
    """
    before = api_get(live_server, "/api/shopping/items")

    context = browser.new_context(viewport=DESKTOP)
    page = context.new_page()
    page.goto(f"{live_server}/shopping")
    page.wait_for_selector(".drag-handle")
    try:
        yield page
    finally:
        context.close()
        restore_arrangement(live_server, before)


def groups(page) -> dict[str, list[str]]:
    return page.evaluate(GROUPS_JS)


def row(page, name):
    return page.locator(f'.editable-item:has(.editable-item-title:text-is("{name}"))')


def grip(page, name):
    return row(page, name).locator(".drag-handle")


def center(box) -> tuple[float, float]:
    return box["x"] + box["width"] / 2, box["y"] + box["height"] / 2


def drag_to(page, name, x, y):
    """Press the grip, travel to (x, y) in steps, release."""
    start_x, start_y = center(grip(page, name).bounding_box())
    page.mouse.move(start_x, start_y)
    page.mouse.down()
    # In steps, not one jump: the drag only begins past a 5px threshold, and the
    # drop target is recomputed on each move.
    page.mouse.move(x, y, steps=20)
    page.mouse.up()
    page.wait_for_timeout(400)


def below(page, name) -> tuple[float, float]:
    """A point just inside the bottom edge of a row — i.e. past its midpoint."""
    box = row(page, name).bounding_box()
    return box["x"] + box["width"] / 2, box["y"] + box["height"] - 4


def test_dragging_a_row_reorders_its_store(shopping):
    assert groups(shopping)["Costco"] == ["Paper towels", "Rotisserie chicken"]

    drag_to(shopping, "Paper towels", *below(shopping, "Rotisserie chicken"))

    assert groups(shopping)["Costco"] == ["Rotisserie chicken", "Paper towels"]


def test_a_reorder_survives_a_reload(shopping):
    """The order is saved, not just drawn."""
    drag_to(shopping, "Paper towels", *below(shopping, "Rotisserie chicken"))

    shopping.reload()
    shopping.wait_for_selector(".drag-handle")

    assert groups(shopping)["Costco"] == ["Rotisserie chicken", "Paper towels"]


def test_dragging_a_row_onto_another_store_moves_it_there(shopping, live_server):
    trader_joes = shopping.locator(
        '.shopping-group:has(.shopping-group-name:text-is("Trader Joe\'s"))'
    ).bounding_box()

    drag_to(shopping, "Stamps", trader_joes["x"] + 200, trader_joes["y"] + 40)

    after = groups(shopping)
    assert after["Trader Joe's"][0] == "Stamps"
    assert "Stamps" not in after["Anywhere"]

    # And the store change is what the server holds, not only what is rendered.
    stores = {s["name"]: s["id"] for s in api_get(live_server, "/api/shopping/stores")}
    stamps = next(
        i for i in api_get(live_server, "/api/shopping/items") if i["name"] == "Stamps"
    )
    assert stamps["store_id"] == stores["Trader Joe's"]
    assert stamps["sort_order"] == 0


def test_escape_abandons_a_drag(shopping):
    before = groups(shopping)
    start_x, start_y = center(grip(shopping, "Batteries").bounding_box())
    target = shopping.locator(
        '.shopping-group:has(.shopping-group-name:text-is("Costco"))'
    ).bounding_box()

    shopping.mouse.move(start_x, start_y)
    shopping.mouse.down()
    shopping.mouse.move(target["x"] + 200, target["y"] + 40, steps=20)
    shopping.keyboard.press("Escape")
    shopping.mouse.up()
    shopping.wait_for_timeout(300)

    assert groups(shopping) == before


def test_the_arrow_keys_reorder_from_the_grip(shopping):
    """The grip is a button, so the same move has to work without a pointer."""
    grip(shopping, "Paper towels").focus()
    shopping.keyboard.press("ArrowDown")
    shopping.wait_for_timeout(400)

    assert groups(shopping)["Costco"] == ["Rotisserie chicken", "Paper towels"]


def test_focus_follows_the_row_it_moved(shopping):
    """The commit re-renders the list; without this you cannot arrow twice."""
    grip(shopping, "Paper towels").focus()
    shopping.keyboard.press("ArrowDown")
    shopping.wait_for_timeout(400)

    focused = shopping.evaluate(
        "() => document.activeElement.getAttribute('aria-label')"
    )
    assert focused == "Reorder Paper towels"


def test_a_move_is_announced(shopping):
    grip(shopping, "Paper towels").focus()
    shopping.keyboard.press("ArrowDown")
    shopping.wait_for_timeout(400)

    assert shopping.locator("#reorder-status").text_content() == (
        "Paper towels, 2 of 2 in Costco"
    )


def test_a_group_emptied_by_dragging_is_still_a_drop_target(shopping):
    """The path that needs the group wrapper and the notice-hiding.

    An empty group only renders when its store is filtered, and it renders a
    "nothing here" notice instead of rows — so there is no row to aim at and,
    without the wrapper, nothing to hit-test against either.
    """
    # Filter to two stores so a store emptied by dragging still renders.
    shopping.click('.filter-chip[data-value="1"]')
    shopping.click('.filter-chip[data-value="2"]')
    shopping.wait_for_timeout(200)

    costco = shopping.locator(
        '.shopping-group:has(.shopping-group-name:text-is("Costco"))'
    )
    for name in ["Almond milk", "Frozen dumplings"]:
        box = costco.bounding_box()
        drag_to(shopping, name, box["x"] + 200, box["y"] + 40)

    trader_joes = shopping.locator(
        '.shopping-group:has(.shopping-group-name:text-is("Trader Joe\'s"))'
    )
    assert groups(shopping)["Trader Joe's"] == []
    assert trader_joes.locator(".container-empty-state").count() == 1

    box = trader_joes.bounding_box()
    drag_to(shopping, "Almond milk", box["x"] + 200, box["y"] + box["height"] / 2)

    assert groups(shopping)["Trader Joe's"] == ["Almond milk"]
    assert trader_joes.locator(".container-empty-state").count() == 0


def test_a_purchased_row_has_no_grip(shopping):
    """Rearranging something already bought means nothing; it sorts by when."""
    assert row(shopping, "Coffee beans").locator(".drag-handle").count() == 0


def test_a_touch_drag_reorders_on_a_phone(browser, live_server):
    """The gesture Rally is actually used with. `dragstart` never fires here."""
    before = api_get(live_server, "/api/shopping/items")
    context = browser.new_context(viewport=PHONE, is_mobile=True, has_touch=True)
    page = context.new_page()
    try:
        page.goto(f"{live_server}/shopping")
        page.wait_for_selector(".drag-handle")
        grip(page, "Frozen dumplings").scroll_into_view_if_needed()
        page.wait_for_timeout(200)

        start = groups(page)["Trader Joe's"]
        start_x, start_y = center(grip(page, "Almond milk").bounding_box())
        end_x, end_y = below(page, "Frozen dumplings")

        cdp = context.new_cdp_session(page)

        def touch(kind: str, x: float, y: float) -> None:
            cdp.send(
                "Input.dispatchTouchEvent",
                {
                    "type": kind,
                    "touchPoints": [] if kind == "touchEnd" else [{"x": x, "y": y}],
                },
            )

        touch("touchStart", start_x, start_y)
        for step in range(1, 21):
            touch(
                "touchMove",
                start_x + (end_x - start_x) * step / 20,
                start_y + (end_y - start_y) * step / 20,
            )
        touch("touchEnd", end_x, end_y)
        page.wait_for_timeout(500)

        expected = [name for name in start if name != "Almond milk"] + ["Almond milk"]
        assert groups(page)["Trader Joe's"] == expected
    finally:
        context.close()
        restore_arrangement(live_server, before)
