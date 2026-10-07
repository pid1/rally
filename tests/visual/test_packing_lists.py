"""Packing Lists (issue #249) driven through a real browser.

The API tests pin the rules (edits to a packing list reach every day; a check, or
an edit made on a day, reaches only that day). These pin the page that carries
them, where a packing list is edited in its own row and a day is packed in its
card: that a tick is saved and counted without touching anything else, that an
item edited or added on a day says so and stays there, that Save & Add Another
keeps the owner and bag a list is being entered with, that the page groups
every packing list by owner or by bag, that a drag onto another owner reassigns
the item and a drag cannot leave its packing list, that bags are managed and
item names suggested, that the
Schedule modal, one packing list at a time, adds a day and updates a schedule
with a button (and a Cancel) each, folds the schedule away until it is needed,
and takes one off with Remove schedule, that a label reads before the recurring mark,
that Pause lives on the row only, and that the archive shows what was packed
and nothing that can change it.

Every test builds its own packing list and deletes it afterwards. The server is
session-scoped and the design-system suite measures the seeded packing list
pages, so a test that changed those would change what the others see.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

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


def delete_template(base: str, template_id: int) -> None:
    """Delete a template the test made, and its days first.

    Deleting a template keeps its days, each made templateless (#252), so a
    test that only deleted the template would leave days behind in Coming Up
    for every test after it on the session-scoped server."""
    for day in api(base, "GET", "/api/packing-list-days"):
        if day["packing_list_template_id"] == template_id:
            api(base, "DELETE", f"/api/packing-list-days/{day['id']}")
    api(base, "DELETE", f"/api/packing-list-templates/{template_id}")


# `rally.cli.seed()` sets this, and the server decides what "today" is in it.
# The tests have to agree, or Today, Tomorrow and Due now land on the wrong day
# for the hours each evening when UTC has already moved on.
SEEDED_TZ = ZoneInfo("America/Chicago")


def today() -> date:
    """Today in the timezone the server is configured with, not the runner's."""
    return datetime.now(SEEDED_TZ).date()


def in_days(n: int) -> str:
    return (today() + timedelta(days=n)).isoformat()


BAG = "Browser test bag"


@pytest.fixture
def packing_list(live_server):
    """A packing list on two days, its items owned by the seed's Emma and Dad (and
    one by nobody), two of them in a bag of their own. Deleted, with its days
    and its bag, afterwards."""
    members = {m["name"]: m["id"] for m in api(live_server, "GET", "/api/family")}
    created = api(live_server, "POST", "/api/packing-list-templates", {"name": "Browser test trip"})
    cid = created["id"]
    items = {
        name: api(
            live_server,
            "POST",
            f"/api/packing-list-templates/{cid}/items",
            {"name": name, "owner_id": owner, "bag": bag},
        )
        for name, owner, bag in (
            ("Goggles", members["Emma"], BAG),
            ("Towel", members["Emma"], BAG),
            ("Keys", members["Dad"], None),
            ("Snacks", None, None),
        )
    }
    days = [
        api(
            live_server,
            "POST",
            "/api/packing-list-days",
            {"packing_list_template_id": cid, "date": in_days(n)},
        )
        for n in (20, 27)
    ]
    try:
        yield {
            "id": cid,
            "members": members,
            "bag_id": items["Goggles"]["bag_id"],
            "items": items,
            "days": days,
        }
    finally:
        for cleanup in (
            lambda: delete_template(live_server, cid),
            lambda: api(
                live_server, "DELETE", f"/api/packing-list-bags/{items['Goggles']['bag_id']}"
            ),
        ):
            try:
                cleanup()
            except urllib.error.HTTPError:
                pass


def open_page(browser, url, viewport=DESKTOP, dialogs=None):
    """Open a page that accepts every dialog, recording each message in
    dialogs when one is passed."""
    context = browser.new_context(viewport=viewport)
    page = context.new_page()

    def accept(dialog):
        if dialogs is not None:
            dialogs.append(dialog.message)
        dialog.accept()

    page.on("dialog", accept)
    page.goto(url, wait_until="networkidle")
    return context, page


def sections(root) -> dict[str, list[str]]:
    """Group name -> item names, within one row or card (a Locator)."""
    return root.evaluate(
        """(root) => Object.fromEntries([...root.querySelectorAll('.list-group')].map(g => [
            g.querySelector('.list-group-name')?.textContent.trim() ?? '',
            [...g.querySelectorAll('.editable-item-title')].map(t => t.textContent.trim()),
        ]))"""
    )


def card(page, day_id):
    return page.locator(f'[data-day-card="{day_id}"]')


def disclosure(root):
    """A day's card or a template's row has its own "View more": the summary
    of its own disclosure, not of one of the groups folded inside it."""
    return root.locator(":scope > .disclosure > summary")


def open_groups(root):
    """Unfold every group in a card or row, the way somebody packing opens
    one owner or bag at a time, so a test about the rows can reach them."""
    folded = root.locator(".list-group--collapsible:not([open]) > summary")
    while folded.count():
        folded.first.click()


def open_card(page, day_id, groups=True):
    """Expand a day's card: its items are under a collapsed "View more", each
    group folded under its own header. `groups=False` leaves them folded."""
    target = card(page, day_id)
    disclosure(target).click()
    target.locator(".disclosure-body").wait_for()
    if groups:
        open_groups(target)
    return target


def test_a_day_card_starts_collapsed(browser, live_server, packing_list):
    first, _ = packing_list["days"]
    context, page = open_page(browser, f"{live_server}/packing-lists")
    try:
        target = card(page, first["id"])
        assert target.locator(".packing-list-progress").text_content() == "0 of 4 packed"
        assert not target.get_by_label("Packed Goggles").is_visible()
        assert disclosure(target).inner_text().strip() == "View more"
        # There is no separate page to open any more.
        assert target.get_by_role("link", name="Open").count() == 0

        open_card(page, first["id"])
        assert target.get_by_label("Packed Goggles").is_visible()
        assert disclosure(target).inner_text().strip() == "View less"
    finally:
        context.close()


def test_a_days_packing_lists_share_one_box_dated_once(browser, live_server, packing_list):
    first, _ = packing_list["days"]
    other = api(live_server, "POST", "/api/packing-list-templates", {"name": "Browser test other"})
    api(
        live_server,
        "POST",
        "/api/packing-list-days",
        {"packing_list_template_id": other["id"], "date": first["date"]},
    )
    context, page = open_page(browser, f"{live_server}/packing-lists")
    try:
        box = page.locator(f'[data-day-box="{first["date"]}"]')
        assert box.count() == 1
        names = box.locator(
            ":scope > [data-day-card] > .editable-item-content > .editable-item-title"
        )
        # Both come from a template, so both carry ⧉.
        assert sorted(" ".join(t.split()) for t in names.all_inner_texts()) == [
            "Browser test other ⧉",
            "Browser test trip ⧉",
        ]
        # The date is the box's, stated once as its footer; each packing list
        # says when to pack it instead.
        assert box.locator(":scope > .date-label").count() == 1
        last = box.locator(":scope > *").last
        assert last.evaluate("el => el.classList.contains('date-label')")
        packs = box.locator(
            ":scope > [data-day-card] .editable-item-meta"
            ":not(.packing-list-progress):not(.packing-list-bag-progress)"
        )
        assert set(packs.all_inner_texts()) == {"Pack the day of"}
        # A subtle hairline, inset to the entries' padding, sits between the
        # two packing lists and above neither the first nor the footer.
        lines = box.evaluate(
            """box => [...box.querySelectorAll(':scope > [data-day-card]')].map(entry => {
                const line = getComputedStyle(entry, '::before');
                return [line.content !== 'none' && line.borderTopWidth === '1px',
                        line.left, line.borderTopColor];
            })"""
        )
        subtle = page.evaluate(
            "getComputedStyle(document.documentElement).getPropertyValue('--rule-subtle').trim()"
        )
        assert [has for has, _, _ in lines] == [False, True]
        assert lines[1][1] != "0px"
        assert page.evaluate(
            """([color, hex]) => {
                const probe = document.createElement('div');
                probe.style.color = hex;
                document.body.append(probe);
                const same = getComputedStyle(probe).color === color;
                probe.remove();
                return same;
            }""",
            [lines[1][2], subtle],
        )
    finally:
        context.close()
        delete_template(live_server, other["id"])


def test_today_and_tomorrow_are_named_in_the_day_box(browser, live_server, packing_list):
    made = [
        api(
            live_server,
            "POST",
            "/api/packing-list-days",
            {"packing_list_template_id": packing_list["id"], "date": d},
        )
        for d in (in_days(0), in_days(1), in_days(2))
    ]
    context, page = open_page(browser, f"{live_server}/packing-lists")
    try:

        def label(day):
            return page.locator(f'[data-day-box="{day["date"]}"] > .date-label').inner_text()

        assert label(made[0]).lower() == "today"
        assert label(made[1]).lower() == "tomorrow"
        assert label(made[2]).lower() not in ("today", "tomorrow")
    finally:
        context.close()


def test_due_now_shows_what_has_reached_its_packing_day(browser, live_server):
    early = api(
        live_server,
        "POST",
        "/api/packing-list-templates",
        {"name": "Browser test early", "pack_days_before": 2},
    )
    late = api(live_server, "POST", "/api/packing-list-templates", {"name": "Browser test late"})
    ids = {
        # Packing day was yesterday, day is tomorrow: still to pack.
        "due": api(
            live_server,
            "POST",
            "/api/packing-list-days",
            {"packing_list_template_id": early["id"], "date": in_days(1)},
        )["id"],
        # Packing day is in two days: not yet.
        "not_yet": api(
            live_server,
            "POST",
            "/api/packing-list-days",
            {"packing_list_template_id": early["id"], "date": in_days(4)},
        )["id"],
        # Packed the day of, and the day is today.
        "today": api(
            live_server,
            "POST",
            "/api/packing-list-days",
            {"packing_list_template_id": late["id"], "date": in_days(0)},
        )["id"],
    }
    api(live_server, "POST", f"/api/packing-list-days/{ids['today']}/check-all")
    context, page = open_page(browser, f"{live_server}/packing-lists")
    try:
        chip = page.locator('#day-filters [data-filter="due-now"]')
        assert chip.inner_text() == "Due now"
        assert page.locator("#packing-label").inner_text() == "Packing"
        assert card(page, ids["not_yet"]).count() == 1

        chip.click()
        assert "active" in chip.get_attribute("class")
        assert card(page, ids["due"]).count() == 1
        # Packed or not, it shows: the filter is about when, not how far along.
        assert card(page, ids["today"]).count() == 1
        assert card(page, ids["not_yet"]).count() == 0

        page.click("#filter-clear")
        assert "active" not in chip.get_attribute("class")
        assert card(page, ids["not_yet"]).count() == 1
    finally:
        context.close()
        delete_template(live_server, early["id"])
        delete_template(live_server, late["id"])


def test_a_day_before_packing_list_names_its_packing_day(browser, live_server):
    packing_list = api(
        live_server,
        "POST",
        "/api/packing-list-templates",
        {"name": "Browser test early", "pack_days_before": 1},
    )
    day = api(
        live_server,
        "POST",
        "/api/packing-list-days",
        {"packing_list_template_id": packing_list["id"], "date": in_days(21)},
    )
    context, page = open_page(browser, f"{live_server}/packing-lists")
    try:
        pack = card(page, day["id"]).locator(".editable-item-meta").first.inner_text()
        expected = (today() + timedelta(days=20)).strftime("%A")
        assert pack == f"Pack {expected}"
    finally:
        context.close()
        delete_template(live_server, packing_list["id"])


def test_edit_packing_list_changes_one_days_date_label_and_lead_time(
    browser, live_server, packing_list
):
    first, second = packing_list["days"]
    api(
        live_server,
        "PUT",
        f"/api/packing-list-templates/{packing_list['id']}",
        {"description": "Lake trip"},
    )
    context, page = open_page(browser, f"{live_server}/packing-lists")
    try:
        card(page, first["id"]).locator("[data-edit-day]").click()
        page.wait_for_selector("#day-edit-modal-overlay .modal-content", state="visible")
        assert (
            page.locator("#day-edit-modal-overlay h3").inner_text().lower() == "edit packing list"
        )
        # The template's name and description are text, not fields.
        assert page.locator("#day-edit-name").inner_text() == "Browser test trip"
        assert page.locator("#day-edit-description").inner_text() == "Lake trip"
        assert (
            page.locator(
                # The name field is a templateless day's only, so it is hidden here.
                "#day-edit-modal-overlay input:not([type=date]):not([type=number]):visible"
            ).count()
            == 1
        )  # the label only
        assert page.input_value("#day-edit-pack-days") == "0"
        # "Pack ___ days before" reads as one line, the number inside it.
        width = page.locator("#day-edit-pack-days").bounding_box()["width"]
        assert width < 120

        page.fill("#day-edit-label", "Cousins visiting")
        page.fill("#day-edit-pack-days", "3")
        page.fill("#day-edit-date", in_days(22))
        page.click("#day-edit-form ~ .modal-actions button[type=submit]")
        page.wait_for_selector("#day-edit-modal-overlay", state="hidden")

        edited = card(page, first["id"])
        edited.locator(".editable-item-title", has_text="Cousins visiting").wait_for()
        pack = today() + timedelta(days=19)
        assert edited.locator(".editable-item-meta").first.inner_text() == (
            f"Pack {pack.strftime('%A')}, {pack.strftime('%b')} {pack.day}"
        )
    finally:
        context.close()

    day = api(live_server, "GET", f"/api/packing-list-days/{first['id']}")
    assert (day["date"], day["label"], day["pack_days_before"]) == (
        in_days(22),
        "Cousins visiting",
        3,
    )
    # The other day still follows the template.
    assert (
        api(live_server, "GET", f"/api/packing-list-days/{second['id']}")["pack_days_before"] == 0
    )


def test_moving_a_day_onto_a_date_it_is_on_opens_that_day(browser, live_server, packing_list):
    first, second = packing_list["days"]
    context, page = open_page(browser, f"{live_server}/packing-lists")
    try:
        card(page, first["id"]).locator("[data-edit-day]").click()
        page.fill("#day-edit-date", second["date"])
        page.click("#day-edit-form ~ .modal-actions button[type=submit]")
        page.wait_for_selector("#day-edit-modal-overlay", state="hidden")
        page.wait_for_function(
            f"""document.querySelector('[data-day-card="{second["id"]}"] details')?.open"""
        )
    finally:
        context.close()

    assert api(live_server, "GET", f"/api/packing-list-days/{first['id']}")["date"] == first["date"]


def test_the_template_sets_its_lead_time_as_a_number(browser, live_server, packing_list):
    context, page = open_page(browser, f"{live_server}/packing-lists")
    try:
        packing_list_row(page, packing_list["id"]).locator("[data-edit-template]").click()
        page.fill("#template-pack-days", "2")
        page.click("#template-form ~ .modal-actions button[type=submit]")
        page.wait_for_selector("#template-modal-overlay", state="hidden")
        row = packing_list_row(page, packing_list["id"])
        row.locator(".editable-item-meta", has_text="Pack 2 days before").wait_for()
    finally:
        context.close()


def test_a_tick_is_saved_and_counted_and_touches_nothing_else(browser, live_server, packing_list):
    first, second = packing_list["days"]
    context, page = open_page(browser, f"{live_server}/packing-lists")
    try:
        target = open_card(page, first["id"])
        target.get_by_label("Packed Goggles").check()
        page.wait_for_function(
            f"""document.querySelector('[data-day-card="{first["id"]}"] .packing-list-progress')
                .textContent === '1 of 4 packed'"""
        )
        emma = packing_list["members"]["Emma"]
        # A day's group keys carry the day's id: every day shares one drag container.
        emma_count = target.locator(
            f'.list-group[data-group="{first["id"]}:{emma}"] .list-group-count'
        )
        assert emma_count.text_content() == "1 of 2 packed"
        # Focus stays on the box that was ticked: only the counts re-render.
        assert page.evaluate("document.activeElement.getAttribute('aria-label')") == (
            "Packed Goggles"
        )

        page.reload(wait_until="networkidle")
        assert open_card(page, first["id"]).get_by_label("Packed Goggles").is_checked()
    finally:
        context.close()

    assert api(live_server, "GET", f"/api/packing-list-days/{second['id']}")["checked"] == 0
    master = api(live_server, "GET", f"/api/packing-list-templates/{packing_list['id']}")
    assert all("checked" not in item for item in master["items"])


def test_the_checkbox_hit_area_is_a_full_target_on_a_phone(browser, live_server, packing_list):
    day = packing_list["days"][0]
    context, page = open_page(browser, f"{live_server}/packing-lists", PHONE)
    try:
        target = open_card(page, day["id"])
        checkbox = target.locator(".item-checkbox").first
        checkbox.scroll_into_view_if_needed()
        box = checkbox.bounding_box()
        assert box["width"] >= 44 and box["height"] >= 44
        # The whole square is the control, not only the 18px box inside it.
        page.mouse.click(box["x"] + 3, box["y"] + 3)
        page.wait_for_function(
            f"""document.querySelector('[data-day-card="{day["id"]}"] .packing-list-progress')
                .textContent === '1 of 4 packed'"""
        )
    finally:
        context.close()


def test_check_all_sits_before_uncheck_all_and_asks_first(browser, live_server, packing_list):
    first, second = packing_list["days"]
    dialogs = []
    context, page = open_page(browser, f"{live_server}/packing-lists", dialogs=dialogs)
    try:
        target = open_card(page, first["id"])
        buttons = target.locator(".packing-list-day-actions button").all_inner_texts()
        assert buttons[:2] == ["Check All", "Uncheck All"]
        target.get_by_role("button", name="Check All", exact=True).click()
        page.wait_for_function(
            f"""document.querySelector('[data-day-card="{first["id"]}"] .packing-list-progress')
                .textContent === 'All 4 packed'"""
        )
        assert dialogs == [dialogs[0]] and dialogs[0].startswith(
            "Check everything on Browser test trip"
        )
    finally:
        context.close()

    assert api(live_server, "GET", f"/api/packing-list-days/{second['id']}")["checked"] == 0


def test_uncheck_all_and_remove_live_in_the_card(browser, live_server, packing_list):
    first, second = packing_list["days"]
    item = packing_list["items"]["Towel"]
    api(
        live_server,
        "PUT",
        f"/api/packing-list-days/{first['id']}/template-items/{item['id']}",
        {"checked": True},
    )
    context, page = open_page(browser, f"{live_server}/packing-lists")
    try:
        target = open_card(page, first["id"])
        target.get_by_role("button", name="Uncheck All").click()
        page.wait_for_function(
            f"""document.querySelector('[data-day-card="{first["id"]}"] .packing-list-progress')
                .textContent === '0 of 4 packed'"""
        )
        # Re-rendering keeps the card open.
        assert card(page, first["id"]).get_by_label("Packed Towel").is_visible()

        card(page, first["id"]).get_by_role("button", name="Remove from day").click()
        card(page, first["id"]).wait_for(state="detached")
        assert card(page, second["id"]).count() == 1
    finally:
        context.close()


def packing_list_row(page, packing_list_template_id):
    return page.locator(f'[data-template-row="{packing_list_template_id}"]')


def open_row(page, packing_list_template_id, groups=True):
    """Expand a packing list's row: its items are under its own "View more",
    each group folded under its own header. `groups=False` leaves them folded."""
    target = packing_list_row(page, packing_list_template_id)
    disclosure(target).click()
    target.locator(".disclosure-body").wait_for()
    if groups:
        open_groups(target)
    return target


def item_row(root, name):
    return root.locator(".list-group .editable-item", has_text=name)


def save_item(page):
    page.click("#item-form button[type=submit]:not(#btn-save-add-another)")
    page.wait_for_selector("#item-modal-overlay", state="hidden")


# --- Editing a packing list in its row -----------------------------------------------------


def test_there_is_no_separate_packing_list_page(browser, live_server, packing_list):
    context, page = open_page(browser, f"{live_server}/packing-lists")
    try:
        assert packing_list_row(page, packing_list["id"]).locator("a").count() == 0
        assert page.goto(f"{live_server}/packing-lists/{packing_list['id']}").status == 404
    finally:
        context.close()


def test_save_and_add_another_keeps_the_owner_and_bag(browser, live_server, packing_list):
    emma = str(packing_list["members"]["Emma"])
    context, page = open_page(browser, f"{live_server}/packing-lists")
    try:
        open_row(page, packing_list["id"]).locator("[data-add-item]").click()
        page.select_option("#item-owner", emma)
        page.fill("#item-bag", BAG)
        page.fill("#item-name", "Swim cap")
        page.click("#btn-save-add-another")
        page.wait_for_function("document.querySelector('#item-name').value === ''")

        assert page.locator("#item-modal-overlay").is_visible()
        assert page.input_value("#item-owner") == emma
        assert page.input_value("#item-bag") == BAG

        page.fill("#item-name", "Flip-flops")
        save_item(page)
        assert sections(packing_list_row(page, packing_list["id"]))["Emma"] == [
            "Goggles",
            "Towel",
            "Swim cap",
            "Flip-flops",
        ]
    finally:
        context.close()


def test_edit_mode_offers_delete_not_add_another(browser, live_server, packing_list):
    context, page = open_page(browser, f"{live_server}/packing-lists")
    try:
        target = open_row(page, packing_list["id"])
        item_row(target, "Towel").locator("[data-edit-item]").click()
        assert page.locator("#btn-delete-item").is_visible()
        assert page.locator("#btn-delete-item").inner_text() == "Delete"
        assert not page.locator("#btn-save-add-another").is_visible()
        assert page.input_value("#item-name") == "Towel"
        assert "every day" in page.locator("#item-scope-note").inner_text()
    finally:
        context.close()


def test_the_view_switch_groups_every_packing_list_by_owner_or_by_bag(
    browser, live_server, packing_list
):
    context, page = open_page(browser, f"{live_server}/packing-lists")
    try:
        row = open_row(page, packing_list["id"])
        # By owner on arrival: the family's order, then Everyone. Each item
        # shows its bag beside it — with whose it is when that is not the
        # group's person: Emma's goggles are in a bag nobody owns.
        assert list(sections(row)) == ["Dad", "Emma", "Everyone"]
        assert (
            item_row(row, "Goggles").locator(".editable-item-description").inner_text()
            == f"{BAG} (Everyone)"
        )
        assert item_row(row, "Keys").locator(".editable-item-description").inner_text() == "No bag"

        page.click('#view-chips [data-view="bag"]')
        assert page.get_attribute('#view-chips [data-view="bag"]', "aria-pressed") == "true"
        row = packing_list_row(page, packing_list["id"])
        assert sections(row) == {BAG: ["Goggles", "Towel"], "No bag": ["Keys", "Snacks"]}
        # The row stays open; its new groups start folded (#256).
        assert row.locator(":scope > .disclosure").evaluate("d => d.open")
        assert row.locator(".list-group--collapsible[open]").count() == 0
        open_groups(row)
        assert item_row(row, "Keys").locator(".editable-item-description").inner_text() == "Dad"
        # Nothing reads blank: an item with no owner says so.
        snacks = item_row(row, "Snacks").locator(".editable-item-description")
        assert snacks.inner_text() == "Everyone"
        # The day cards regroup with it.
        day = open_card(page, packing_list["days"][0]["id"])
        assert list(sections(day)) == [BAG, "No bag"]
        # It is a view, not a filter: Clear Filters leaves it alone.
        page.click("#filter-clear")
        assert page.get_attribute('#view-chips [data-view="bag"]', "aria-pressed") == "true"
    finally:
        context.close()


def test_a_packing_list_with_one_group_still_heads_it(browser, live_server):
    lone = api(live_server, "POST", "/api/packing-list-templates", {"name": "Browser test lone"})
    api(live_server, "POST", f"/api/packing-list-templates/{lone['id']}/items", {"name": "Bucket"})
    context, page = open_page(browser, f"{live_server}/packing-lists")
    try:
        assert list(sections(open_row(page, lone["id"]))) == ["Everyone"]
        page.click('#view-chips [data-view="bag"]')
        assert list(sections(packing_list_row(page, lone["id"]))) == ["No bag"]
        # Manage bags sits beside the archive link, not in the toolbar.
        assert page.locator(".page-header-meta #btn-manage-bags").count() == 1
    finally:
        context.close()
        delete_template(live_server, lone["id"])


def test_a_bag_typed_on_an_item_joins_the_list_and_manage_bags_renames_it(
    browser, live_server, packing_list
):
    context, page = open_page(browser, f"{live_server}/packing-lists")
    try:
        open_row(page, packing_list["id"]).locator("[data-add-item]").click()
        page.fill("#item-name", "Sunglasses")
        # Bags already in the household are suggested as you type.
        page.fill("#item-bag", "browser test")
        page.locator("#bag-suggestions .autocomplete-option", has_text=BAG).click()
        assert page.input_value("#item-bag") == BAG
        page.fill("#item-bag", "Browser test car")
        save_item(page)

        # A row is one line until its Edit unfolds it in place.
        page.click("#btn-manage-bags")
        page.get_by_role("button", name="Edit Browser test car").click()
        page.fill("#bag-edit-name", "Browser test glovebox")
        page.locator("#bag-manage-list").get_by_role("button", name="Save").click()
        page.locator(
            "#bag-manage-list .manage-row .editable-item-title", has_text="Browser test glovebox"
        ).wait_for()
        page.click("#btn-close-bags")
        page.click('#view-chips [data-view="bag"]')
        assert "Browser test glovebox" in sections(packing_list_row(page, packing_list["id"]))
    finally:
        context.close()

    for bag in api(live_server, "GET", "/api/packing-list-bags"):
        if bag["name"] == "Browser test glovebox":
            api(live_server, "DELETE", f"/api/packing-list-bags/{bag['id']}")


def test_an_item_name_is_suggested_with_the_owner_and_bag_it_had(
    browser, live_server, packing_list
):
    context, page = open_page(browser, f"{live_server}/packing-lists")
    try:
        open_row(page, packing_list["id"]).locator("[data-add-item]").click()
        page.locator("#item-name").press_sequentially("Gogg")
        option = page.locator("#item-suggestions .autocomplete-option", has_text="Goggles").first
        option.click()
        assert page.input_value("#item-name") == "Goggles"
        assert page.input_value("#item-owner") == str(packing_list["members"]["Emma"])
        assert page.input_value("#item-bag") == BAG
    finally:
        context.close()


def drag(page, grip, target_box, steps=20):
    """Press the grip and move to the target, without letting go.

    `bounding_box()` does not scroll, and a press below the viewport lands on
    nothing, so the grip is brought into view first and `target_box` (measured
    before that) is moved by however far the page scrolled. `steps=1` jumps
    straight there, for a test about where the pointer ends rather than what
    it passes over on the way."""
    before = page.evaluate("window.scrollY")
    grip.scroll_into_view_if_needed()
    shift = page.evaluate("window.scrollY") - before
    target_box = {**target_box, "y": target_box["y"] - shift}
    box = grip.bounding_box()
    page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    page.mouse.down()
    page.mouse.move(target_box["x"] + 200, target_box["y"] + 30, steps=steps)


def test_dragging_an_item_onto_another_owner_reassigns_it(browser, live_server, packing_list):
    context, page = open_page(browser, f"{live_server}/packing-lists")
    try:
        target = open_row(page, packing_list["id"])
        grip = item_row(target, "Snacks").locator(".drag-handle")
        group = target.locator('.list-group:has(.list-group-name:text-is("Dad"))')
        drag(page, grip, group.bounding_box())
        # Wait for the save, not a fixed pause: the API is checked below.
        with page.expect_response("**/items/reorder"):
            page.mouse.up()
        page.wait_for_load_state("networkidle")

        # Dropped on Dad: Dad owns it now, and it reads where it was dropped.
        assert sections(packing_list_row(page, packing_list["id"]))["Dad"] == ["Snacks", "Keys"]
    finally:
        context.close()

    snacks = next(
        i
        for i in api(live_server, "GET", f"/api/packing-list-templates/{packing_list['id']}")[
            "items"
        ]
        if i["name"] == "Snacks"
    )
    assert snacks["owner_id"] == packing_list["members"]["Dad"]


def test_a_drag_cannot_leave_its_packing_list(browser, live_server, packing_list):
    other = api(live_server, "POST", "/api/packing-list-templates", {"name": "Browser test other"})
    api(live_server, "POST", f"/api/packing-list-templates/{other['id']}/items", {"name": "Bucket"})
    context, page = open_page(browser, f"{live_server}/packing-lists")
    try:
        target = open_row(page, packing_list["id"])
        elsewhere = open_row(page, other["id"])
        reorders = []
        page.on("request", lambda r: reorders.append(r.url) if "reorder" in r.url else None)
        grip = item_row(target, "Snacks").locator(".drag-handle")
        # Straight there: on the way, the pointer would pass over this list's
        # own groups, and a row let go after that is a move within its list.
        drag(page, grip, elsewhere.locator(".list-group").first.bounding_box(), steps=1)
        page.mouse.up()
        page.wait_for_timeout(500)

        assert reorders == []
        assert "Snacks" in sections(packing_list_row(page, packing_list["id"]))["Everyone"]
        assert sections(packing_list_row(page, other["id"])) == {"Everyone": ["Bucket"]}
    finally:
        context.close()
        delete_template(live_server, other["id"])


def test_the_edit_form_is_details_only(browser, live_server, packing_list):
    context, page = open_page(browser, f"{live_server}/packing-lists")
    try:
        packing_list_row(page, packing_list["id"]).locator("[data-edit-template]").click()
        page.wait_for_selector("#template-modal-overlay .modal-content", state="visible")
        assert page.locator("#template-modal-overlay input[type=checkbox]").count() == 0
        assert page.locator("#btn-delete-template").is_visible()
        page.click("#btn-cancel-template")
        page.click("#btn-add-template")
        assert not page.locator("#btn-delete-template").is_visible()
    finally:
        context.close()


# --- A day's own changes ------------------------------------------------------------------


def test_an_item_edited_on_a_day_stays_on_that_day(browser, live_server, packing_list):
    first, second = packing_list["days"]
    context, page = open_page(browser, f"{live_server}/packing-lists")
    try:
        target = open_card(page, first["id"])
        item_row(target, "Goggles").locator("[data-edit-day-item]").click()
        note = page.locator("#item-scope-note").inner_text()
        assert note.startswith('For "Browser test trip" on ')
        assert note.endswith(" only. The packing list template stays as it is.")
        assert page.locator("#btn-delete-item").inner_text() == "Remove"
        page.fill("#item-name", "Blue goggles")
        # A day can give a template item another owner, for that day.
        page.select_option("#item-owner", str(packing_list["members"]["Dad"]))
        save_item(page)

        # It keeps its place in the order, under its new owner, marked.
        assert sections(card(page, first["id"]))["Dad"] == ["Blue goggles (changed)", "Keys"]
        mark = item_row(card(page, first["id"]), "Blue goggles").locator(".item-mark")
        assert mark.inner_text().lower() == "(changed)"
    finally:
        context.close()

    other = api(live_server, "GET", f"/api/packing-list-days/{second['id']}")
    assert "Goggles" in [i["name"] for i in other["items"]]
    master = api(live_server, "GET", f"/api/packing-list-templates/{packing_list['id']}")
    assert "Goggles" in [i["name"] for i in master["items"]]


def test_an_item_added_to_a_day_is_marked_and_one_can_be_removed(
    browser, live_server, packing_list
):
    first, _ = packing_list["days"]
    context, page = open_page(browser, f"{live_server}/packing-lists")
    try:
        open_card(page, first["id"]).locator("[data-add-day-item]").click()
        page.fill("#item-name", "Field trip form")
        save_item(page)

        added = item_row(card(page, first["id"]), "Field trip form")
        assert added.locator(".item-mark").inner_text().lower() == "(added)"
        # Beside the day's Edit, in the description's type.
        changes = card(page, first["id"]).locator(":scope > .editable-item-actions > .day-changes")
        assert changes.inner_text() == "1 item changed"
        assert "editable-item-description" in changes.get_attribute("class")
        progress = card(page, first["id"]).locator(".packing-list-progress")
        assert progress.inner_text() == "0 of 5 packed"

        item_row(card(page, first["id"]), "Towel").locator("[data-edit-day-item]").click()
        page.click("#btn-delete-item")
        page.wait_for_selector("#item-modal-overlay", state="hidden")
        assert item_row(card(page, first["id"]), "Towel").count() == 0
        # A removal counts too, though the item is no longer on the card.
        assert card(page, first["id"]).locator(".day-changes").inner_text() == "2 items changed"
    finally:
        context.close()

    master = api(live_server, "GET", f"/api/packing-list-templates/{packing_list['id']}")
    assert [i["name"] for i in master["items"]] == ["Goggles", "Towel", "Keys", "Snacks"]


# --- Schedule --------------------------------------------------------------------------------


def open_schedule(page, packing_list_template_id):
    packing_list_row(page, packing_list_template_id).locator("[data-schedule]").click()
    page.wait_for_selector("#schedule-modal-overlay .modal-content", state="visible")


def press_and_close(page, button_id):
    page.click(f"#{button_id}")
    page.wait_for_selector("#schedule-modal-overlay", state="hidden")


def test_the_modal_is_for_one_packing_list_named_in_its_title(browser, live_server, packing_list):
    context, page = open_page(browser, f"{live_server}/packing-lists")
    try:
        # Scheduling starts from a packing list's own row: there is no picker,
        # and so no page-level Schedule button to open one from.
        assert page.locator("#btn-schedule").count() == 0
        open_schedule(page, packing_list["id"])
        assert page.locator("#schedule-modal-title").text_content() == "Schedule: Browser test trip"
        assert page.locator("#schedule-modal-overlay select#schedule-packing-list").count() == 0
    finally:
        context.close()


def open_add_day(page):
    page.click("#btn-add-day")
    page.wait_for_selector("#day-add-modal-overlay .modal-content", state="visible")


def save_add_day(page):
    page.click("#day-add-modal-overlay button[type=submit]")
    page.wait_for_selector("#day-add-modal-overlay", state="hidden")


def delete_days_named(base: str, name: str) -> None:
    for day in api(base, "GET", "/api/packing-list-days"):
        if day["name"] == name and day["packing_list_template_id"] is None:
            api(base, "DELETE", f"/api/packing-list-days/{day['id']}")


def test_the_schedule_modal_only_repeats(browser, live_server, packing_list):
    """A single day is Add Packing List's (#254). With nothing repeating and
    Repeats off there is nothing to save, so Cancel is the only button."""
    context, page = open_page(browser, f"{live_server}/packing-lists")
    try:
        open_schedule(page, packing_list["id"])
        modal = page.locator("#schedule-modal-overlay")
        assert modal.locator("#day-date").count() == 0
        assert modal.locator("legend").count() == 0
        assert not page.locator("#schedule-details").is_visible()
        assert not page.locator("#btn-update-schedule").is_visible()
        assert page.locator("[data-cancel-schedule]:visible").count() == 1
        page.click("[data-cancel-schedule]")
        page.wait_for_selector("#schedule-modal-overlay", state="hidden")
    finally:
        context.close()


def test_add_packing_list_sits_where_add_meal_does(browser, live_server):
    def place(page, button):
        return page.evaluate(
            """(id) => {
                const button = document.getElementById(id);
                const row = button.closest('.page-header-row');
                const heading = row.querySelector('h2').getBoundingClientRect();
                const box = button.getBoundingClientRect();
                return { inActions: !!button.closest('.page-header-actions'),
                         right: Math.round(row.getBoundingClientRect().right - box.right),
                         middle: Math.round((box.top + box.height / 2) - (heading.top + heading.height / 2)) };
            }""",
            button,
        )

    context, page = open_page(browser, f"{live_server}/meal-planner")
    try:
        meal = place(page, "btn-add-meal")
        page.goto(f"{live_server}/packing-lists", wait_until="networkidle")
        packing = place(page, "btn-add-day")
    finally:
        context.close()
    assert packing == meal
    assert packing["inActions"]


def test_kept_in_sync_on_a_day_it_is_already_on_opens_that_day(browser, live_server, packing_list):
    existing = packing_list["days"][0]
    context, page = open_page(browser, f"{live_server}/packing-lists")
    try:
        open_add_day(page)
        # Blank, so nothing lands on a day nobody picked.
        assert page.input_value("#day-date") == ""
        page.check('input[name="day-add-start"][value="template"]')
        page.select_option("#day-add-source", label="Browser test trip")
        page.check('input[name="day-add-mode"][value="sync"]')
        page.fill("#day-date", existing["date"])
        save_add_day(page)
        page.wait_for_function(
            f"""document.querySelector('[data-day-card="{existing["id"]}"] details')?.open"""
        )
        assert not page.locator("#item-modal-overlay").is_visible()
    finally:
        context.close()
    same_day = [
        d
        for d in api(live_server, "GET", "/api/packing-list-days")
        if d["packing_list_template_id"] == packing_list["id"] and d["date"] == existing["date"]
    ]
    assert len(same_day) == 1


def test_a_template_kept_in_sync_lands_in_coming_up_with_its_label(
    browser, live_server, packing_list
):
    context, page = open_page(browser, f"{live_server}/packing-lists")
    try:
        # With the packing filter on, a new day thirty days out would be
        # hidden; adding one turns the filter off so it shows.
        chip = page.locator("#day-filters .filter-chip").first
        chip.click()
        open_add_day(page)
        page.check('input[name="day-add-start"][value="template"]')
        page.select_option("#day-add-source", label="Browser test trip")
        page.check('input[name="day-add-mode"][value="sync"]')
        page.fill("#day-date", in_days(30))
        page.fill("#day-label", "Long weekend")
        save_add_day(page)
        # The modal closes before the list is refetched, so wait for the card
        # rather than reading the list the moment the modal hides.
        row = page.locator("#days-container [data-day-card]", has_text="Long weekend")
        row.wait_for()
        assert row.count() == 1
        assert "0 of 4 packed" in row.text_content()
        title = row.locator(":scope > .editable-item-content .editable-item-title")
        assert " ".join(title.inner_text().split()) == "Browser test trip — Long weekend ⧉"
        assert "active" not in chip.get_attribute("class")
        # It already has items, so Add Item does not open.
        assert not page.locator("#item-modal-overlay").is_visible()
    finally:
        context.close()


def test_a_blank_list_opens_add_item_and_a_copy_does_not(browser, live_server, packing_list):
    context, page = open_page(browser, f"{live_server}/packing-lists")
    try:
        open_add_day(page)
        # Name and Date are required: an empty form does not close.
        page.click("#day-add-modal-overlay button[type=submit]")
        page.wait_for_timeout(300)
        assert page.locator("#day-add-modal-overlay").is_visible()
        page.fill("#day-add-name", "Browser test concert")
        page.fill("#day-date", in_days(31))
        save_add_day(page)
        page.wait_for_selector("#item-modal-overlay", state="visible")
        assert (
            page.locator("#item-scope-note")
            .inner_text()
            .startswith('For "Browser test concert" on ')
        )
        page.click("#btn-cancel-item")

        open_add_day(page)
        page.check('input[name="day-add-start"][value="template"]')
        page.select_option("#day-add-source", label="Browser test trip")
        page.fill("#day-add-name", "Browser test copy")
        page.fill("#day-date", in_days(31))
        save_add_day(page)
        copy = page.locator("#days-container [data-day-card]", has_text="Browser test copy")
        copy.wait_for()
        page.wait_for_timeout(300)
        assert not page.locator("#item-modal-overlay").is_visible()
        title = copy.locator(":scope > .editable-item-content .editable-item-title")
        assert " ".join(title.inner_text().split()) == "Browser test copy"
        assert "0 of 4 packed" in copy.text_content()
    finally:
        context.close()
        delete_days_named(live_server, "Browser test concert")
        delete_days_named(live_server, "Browser test copy")


def test_picking_a_template_fills_the_form(browser, live_server, packing_list):
    api(
        live_server,
        "PUT",
        f"/api/packing-list-templates/{packing_list['id']}",
        {"pack_days_before": 2},
    )
    context, page = open_page(browser, f"{live_server}/packing-lists")
    try:
        open_add_day(page)
        # Blank list is the default; the template fields wait for Template.
        assert page.is_checked('input[name="day-add-start"][value="blank"]')
        fields = page.locator("#day-add-template-fields")
        assert not fields.is_visible()

        page.check('input[name="day-add-start"][value="template"]')
        # The templates are listed below, none chosen yet, and Save refuses
        # until one is.
        assert fields.is_visible()
        assert page.input_value("#day-add-source") == ""
        page.fill("#day-add-name", "Anything")
        page.fill("#day-date", in_days(33))
        page.click("#day-add-modal-overlay button[type=submit]")
        page.wait_for_timeout(300)
        assert page.locator("#day-add-modal-overlay").is_visible()
        page.fill("#day-add-name", "")

        page.select_option("#day-add-source", label="Browser test trip")
        # A copy is the default, named after the template until somebody
        # types a name, with the template's lead time.
        assert page.is_checked('input[name="day-add-mode"][value="copy"]')
        assert page.input_value("#day-add-name") == "Browser test trip"
        assert page.input_value("#day-add-pack-days") == "2"

        page.fill("#day-add-name", "Trip, my way")
        page.check('input[name="day-add-mode"][value="sync"]')
        # Kept in sync, it is called what the template is called.
        assert page.input_value("#day-add-name") == "Browser test trip"
        assert page.evaluate("document.getElementById('day-add-name').readOnly")
        page.check('input[name="day-add-mode"][value="copy"]')
        assert page.input_value("#day-add-name") == "Trip, my way"

        page.check('input[name="day-add-start"][value="blank"]')
        assert not fields.is_visible()
        assert page.input_value("#day-add-name") == "Trip, my way"
    finally:
        context.close()


def test_start_from_is_hidden_with_no_templates(browser, live_server):
    context = browser.new_context(viewport=DESKTOP)
    page = context.new_page()
    page.route(
        "**/api/packing-list-templates",
        lambda route: route.fulfill(status=200, content_type="application/json", body="[]"),
    )
    try:
        page.goto(f"{live_server}/packing-lists", wait_until="networkidle")
        open_add_day(page)
        assert not page.locator("#day-add-source-group").is_visible()
        assert page.locator("#day-add-name").is_visible()
    finally:
        context.close()


def test_a_one_off_is_renamed_from_edit(browser, live_server):
    day = api(
        live_server,
        "POST",
        "/api/packing-list-days",
        {"name": "Browser test one-off", "date": in_days(32), "pack_days_before": 0},
    )
    context, page = open_page(browser, f"{live_server}/packing-lists")
    try:
        card(page, day["id"]).locator("[data-edit-day]").click()
        page.wait_for_selector("#day-edit-modal-overlay", state="visible")
        assert not page.locator("#day-edit-name").is_visible()
        page.fill("#day-edit-name-input", "Browser test one-off, renamed")
        page.click("#day-edit-modal-overlay button[type=submit]")
        page.wait_for_selector("#day-edit-modal-overlay", state="hidden")
    finally:
        context.close()
    assert api(live_server, "GET", f"/api/packing-list-days/{day['id']}")["name"] == (
        "Browser test one-off, renamed"
    )
    api(live_server, "DELETE", f"/api/packing-list-days/{day['id']}")


def test_view_schedule_is_folded_until_it_is_needed(browser, live_server, packing_list):
    context, page = open_page(browser, f"{live_server}/packing-lists")
    try:
        open_schedule(page, packing_list["id"])
        assert not page.locator("#schedule-details").is_visible()
        # Turning Repeats on for a packing list with no schedule opens the
        # controls, since setting one up is what that asks for.
        page.check("#schedule-repeats")
        assert page.locator("#schedule-details").is_visible()
        assert page.evaluate("document.getElementById('schedule-details').open")
        assert page.locator("#btn-update-schedule").inner_text() == "Update schedule"
        page.locator("[data-cancel-schedule]").first.click()
    finally:
        context.close()

    _repeat_later(live_server, packing_list)
    context, page = open_page(browser, f"{live_server}/packing-lists")
    try:
        # A schedule that exists opens folded: it is described on the row.
        open_schedule(page, packing_list["id"])
        assert page.is_checked("#schedule-repeats")
        assert not page.evaluate("document.getElementById('schedule-details').open")
        assert not page.locator("#recurring-type").is_visible()
        # The button sits outside the fold, beside its Cancel.
        assert page.locator("#btn-update-schedule").is_visible()
        page.click("#schedule-details > summary")
        assert page.locator("#recurring-type").is_visible()
    finally:
        context.close()


def test_a_repeating_schedule_is_set_up_from_its_row(browser, live_server, packing_list):
    context, page = open_page(browser, f"{live_server}/packing-lists")
    try:
        open_schedule(page, packing_list["id"])
        page.check("#schedule-repeats")
        page.select_option("#recurring-type", "daily")
        page.fill("#schedule-start-date", in_days(50))
        page.fill("#schedule-end-date", in_days(51))
        page.fill("#schedule-label", "Camp")
        # The end date bounds the read-back as well as the days: two dates,
        # where an unbounded daily rule reads back three.
        page.wait_for_function(
            "(document.querySelector('.recurrence-preview-rest') || {}).textContent"
            "?.split(',').length === 2"
        )
        press_and_close(page, "btn-update-schedule")

        row = packing_list_row(page, packing_list["id"])
        row.locator(".recurring-schedule").wait_for()
        assert "Daily" in row.locator(".recurring-schedule").inner_text()
        # The label reads before the recurring mark.
        title = " ".join(
            row.locator(":scope > .editable-item-content > .editable-item-title")
            .inner_text()
            .split()
        )
        assert title == "Browser test trip — Camp ↻"
    finally:
        context.close()

    schedule = api(live_server, "GET", f"/api/packing-list-templates/{packing_list['id']}")[
        "schedule"
    ]
    assert (schedule["recurrence_type"], schedule["label"]) == ("daily", "Camp")


def test_a_scheduled_days_label_reads_before_the_recurring_mark(browser, live_server, packing_list):
    schedule = api(
        live_server,
        "POST",
        "/api/packing-list-template-schedules",
        {
            "packing_list_template_id": packing_list["id"],
            "recurrence_type": "daily",
            "start_date": in_days(1),
            "end_date": in_days(1),
            "label": "Emma",
        },
    )
    context, page = open_page(browser, f"{live_server}/packing-lists")
    try:
        scheduled = page.locator("#days-container [data-day-card]", has_text="Browser test trip")
        scheduled = scheduled.filter(
            has=page.locator('.title-indicator[title="Added by a recurring schedule"]')
        ).first
        title = " ".join(scheduled.locator(".editable-item-title").first.inner_text().split())
        assert title == "Browser test trip — Emma ⧉ ↻"
    finally:
        context.close()
        api(live_server, "DELETE", f"/api/packing-list-template-schedules/{schedule['id']}")


def test_weekdays_and_weekend_days_read_as_one_phrase(browser, live_server):
    """The read-back is shared with Recurring Tasks, so this holds on both pages."""
    rules = [
        ({"recurrence_type": "daily", "custom_rule": {"weekdays_only": True}}, "Every weekday"),
        (
            {
                "recurrence_type": "custom",
                "custom_rule": {"freq": "weekly", "interval": 1, "weekdays": [0, 1, 2, 3, 4]},
            },
            "Every weekday",
        ),
        (
            {
                "recurrence_type": "custom",
                "custom_rule": {"freq": "weekly", "interval": 1, "weekdays": [6, 5]},
            },
            "Every weekend day",
        ),
        (
            {
                "recurrence_type": "custom",
                "custom_rule": {"freq": "weekly", "interval": 2, "weekdays": [0, 1, 2, 3, 4]},
            },
            "Every 2 weeks on weekdays",
        ),
        (
            {
                "recurrence_type": "custom",
                "custom_rule": {"freq": "weekly", "interval": 2, "weekdays": [5, 6]},
            },
            "Every 2 weeks on weekend days",
        ),
        (
            {
                "recurrence_type": "custom",
                "custom_rule": {"freq": "weekly", "interval": 1, "weekdays": [0, 2]},
            },
            "Every week on Mon, Wed",
        ),
        ({"recurrence_type": "daily", "custom_rule": None}, "Daily"),
    ]
    for path in ("/packing-lists", "/todo"):
        context, page = open_page(browser, f"{live_server}{path}")
        try:
            for rule, expected in rules:
                assert page.evaluate("rule => RecurrenceForm.describe(rule)", rule) == expected
        finally:
            context.close()


def test_each_page_explains_custom_weekdays_only_in_its_own_words(browser, live_server):
    titles = {}
    for path in ("/packing-lists", "/todo"):
        context, page = open_page(browser, f"{live_server}{path}")
        try:
            titles[path] = page.locator("label:has(#custom-weekdays-only)").get_attribute("title")
        finally:
            context.close()
    assert titles["/packing-lists"] == (
        "When checked, a day that falls on a weekend moves to the following Monday instead."
    )
    assert "tasks" in titles["/todo"]


def test_daily_can_skip_weekends(browser, live_server, packing_list):
    context, page = open_page(browser, f"{live_server}/packing-lists")
    try:
        open_schedule(page, packing_list["id"])
        page.check("#schedule-repeats")
        page.select_option("#recurring-type", "daily")
        page.check("#daily-weekdays-only")
        page.fill("#schedule-start-date", in_days(60))
        press_and_close(page, "btn-update-schedule")

        row = packing_list_row(page, packing_list["id"])
        row.locator(".recurring-schedule").wait_for()
        assert row.locator(".recurring-schedule").inner_text().startswith("Every weekday")

        # It reads back into the same controls.
        open_schedule(page, packing_list["id"])
        page.click("#schedule-details > summary")
        assert page.input_value("#recurring-type") == "daily"
        assert page.is_checked("#daily-weekdays-only")
        # Under Custom, every N days, the other weekdays-only box is offered
        # here too; the Daily one is not.
        page.select_option("#recurring-type", "custom")
        page.select_option("#custom-freq", "daily")
        assert page.locator("#custom-weekdays-only").is_visible()
        assert not page.locator("#daily-weekdays-only").is_visible()
    finally:
        context.close()

    schedule = api(live_server, "GET", f"/api/packing-list-templates/{packing_list['id']}")[
        "schedule"
    ]
    assert (schedule["recurrence_type"], schedule["custom_rule"]) == (
        "daily",
        {"weekdays_only": True},
    )


def _repeat_later(live_server, packing_list):
    """A schedule that starts beyond the week a schedule fills ahead."""
    api(
        live_server,
        "POST",
        "/api/packing-list-template-schedules",
        {
            "packing_list_template_id": packing_list["id"],
            "recurrence_type": "daily",
            "start_date": in_days(60),
        },
    )


def test_pause_is_on_the_row_only(browser, live_server, packing_list):
    _repeat_later(live_server, packing_list)
    context, page = open_page(browser, f"{live_server}/packing-lists")
    try:
        open_schedule(page, packing_list["id"])
        assert page.is_checked("#schedule-repeats")
        assert page.locator("#schedule-modal-overlay button", has_text="Pause").count() == 0
        page.locator("[data-cancel-schedule]").first.click()

        packing_list_row(page, packing_list["id"]).locator("[data-toggle-schedule]").click()
        row = packing_list_row(page, packing_list["id"])
        row.locator(".recurring-paused", has_text="Paused").wait_for()
        assert row.locator("[data-toggle-schedule]").inner_text() == "Resume"
    finally:
        context.close()


def test_unchecking_repeats_turns_the_button_into_remove_schedule(
    browser, live_server, packing_list
):
    _repeat_later(live_server, packing_list)
    dialogs = []
    context, page = open_page(browser, f"{live_server}/packing-lists", dialogs=dialogs)
    try:
        open_schedule(page, packing_list["id"])
        page.uncheck("#schedule-repeats")
        # The button says what it will now do, and the rule is put away.
        assert page.locator("#btn-update-schedule").inner_text() == "Remove schedule"
        assert not page.locator("#schedule-details").is_visible()
        press_and_close(page, "btn-update-schedule")
        # The row's own ↻, not a bag's ▣ further down it.
        page.wait_for_function(
            f"""!document.querySelector(
                '[data-template-row="{packing_list["id"]}"] > .editable-item-content .title-indicator')"""
        )
        assert any(m.startswith("Stop repeating Browser test trip?") for m in dialogs)
    finally:
        context.close()

    assert (
        api(live_server, "GET", f"/api/packing-list-templates/{packing_list['id']}")["schedule"]
        is None
    )


def test_the_archive_shows_what_was_packed_and_cannot_change_it(browser, live_server):
    # The seed's past swims: every item packed, two and four weeks ago.
    context, page = open_page(browser, f"{live_server}/packing-lists/previous")
    try:
        # Days come in boxes here too, newest first, dated once at the foot.
        page.locator(".day-box > .date-label").first.wait_for()
        # The most recent past swim: it shares its day with a half-packed
        # beach trip, so it is found by name rather than by position.
        first = page.locator("[data-day-card]", has_text="Swim at Nana's").first
        first.wait_for()
        disclosure(first).click()
        open_groups(first)
        boxes = first.locator("input[data-item]")
        assert boxes.count() == 13
        assert all(boxes.nth(i).is_checked() and boxes.nth(i).is_disabled() for i in range(13))
        # Its bags are there to read too, and cannot be grabbed after the fact.
        bag_boxes = first.locator("input[data-bag]")
        assert bag_boxes.count() == 2
        assert all(bag_boxes.nth(i).is_disabled() for i in range(2))
        assert first.get_by_role("button").count() == 0
        # No "items changed" note in the archive.
        assert page.locator(".day-changes").count() == 0
        # Every day here is past, so a packed one is not dimmed as a whole;
        # its packed items are still muted row by row.
        assert first.evaluate("el => getComputedStyle(el).opacity") == "1"
        packed = first.locator(".list-group .editable-item.completed").first
        assert packed.evaluate("el => getComputedStyle(el).opacity") == "0.6"

        # An unpacked item there is grayed the same way: nothing on a past
        # day can be checked off, packed or not.
        beach = page.locator("[data-day-card]", has_text="Beach day").first
        disclosure(beach).click()
        open_groups(beach)
        unpacked = beach.locator(".list-group .editable-item:not(.completed)").first
        assert unpacked.evaluate("el => getComputedStyle(el).opacity") == "0.6"
        assert unpacked.locator("input[type=checkbox]").is_disabled()

        page.fill("#search-input", "no such packing list")
        page.click("#search-btn")
        page.get_by_text("No packing lists match your search.").wait_for()
    finally:
        context.close()


# --- #252: templateless days, a day's own order, the bag menu --------------------------


def test_a_deleted_templates_day_stays_and_says_it_has_no_template(browser, live_server):
    gone = api(live_server, "POST", "/api/packing-list-templates", {"name": "Browser test gone"})
    api(live_server, "POST", f"/api/packing-list-templates/{gone['id']}/items", {"name": "Tickets"})
    day = api(
        live_server,
        "POST",
        "/api/packing-list-days",
        {"packing_list_template_id": gone["id"], "date": in_days(21)},
    )
    dialogs: list[str] = []
    context, page = open_page(browser, f"{live_server}/packing-lists", dialogs=dialogs)
    try:
        title = card(page, day["id"]).locator(
            ":scope > .editable-item-content .editable-item-title"
        )
        assert " ".join(title.inner_text().split()) == "Browser test gone ⧉"

        packing_list_row(page, gone["id"]).locator("[data-edit-template]").click()
        with page.expect_response("**/api/packing-list-templates/*"):
            page.click("#btn-delete-template")
        page.wait_for_load_state("networkidle")
        assert dialogs[-1] == (
            "Delete Browser test gone? The day it's on keeps its packing list as it is now."
        )

        # Still in Coming Up, as it was, with no template mark, no item marks,
        # and Delete in place of Remove from day.
        kept = open_card(page, day["id"])
        assert " ".join(title.inner_text().split()) == "Browser test gone"
        assert kept.locator(".item-mark").count() == 0
        assert kept.locator("[data-remove-day]").inner_text() == "Delete"
        kept.locator("[data-add-day-item]").click()
        note = page.locator("#item-scope-note").inner_text()
        assert note.startswith('For "Browser test gone" on ') and "template" not in note
        page.click("#btn-cancel-item")

        with page.expect_response(f"**/api/packing-list-days/{day['id']}"):
            kept.locator("[data-remove-day]").click()
        assert dialogs[-1] == "Delete Browser test gone? Its items go with it."
        card(page, day["id"]).wait_for(state="detached")
    finally:
        context.close()


def test_a_days_items_reorder_for_that_day_only(browser, live_server, packing_list):
    first, second = packing_list["days"]
    context, page = open_page(browser, f"{live_server}/packing-lists")
    try:
        target = open_card(page, first["id"])
        # Within a group, by keyboard: order only, nothing marked changed.
        with page.expect_response("**/items/reorder"):
            item_row(target, "Towel").locator(".drag-handle").press("ArrowUp")
        page.wait_for_function(
            """document.activeElement.closest('.editable-item')
                ?.querySelector('.editable-item-title').textContent.trim() === 'Towel'"""
        )
        assert sections(card(page, first["id"]))["Emma"] == ["Towel", "Goggles"]
        assert card(page, first["id"]).locator(".item-mark").count() == 0

        # Into another group, by pointer: Dad's on this day, and marked so.
        grip = item_row(card(page, first["id"]), "Snacks").locator(".drag-handle")
        group = card(page, first["id"]).locator('.list-group:has(.list-group-name:text-is("Dad"))')
        drag(page, grip, group.bounding_box())
        with page.expect_response("**/items/reorder"):
            page.mouse.up()
        page.wait_for_load_state("networkidle")
        assert "Snacks (changed)" in sections(card(page, first["id"]))["Dad"]
    finally:
        context.close()

    # The template and the other day keep their own order and owners.
    template = api(live_server, "GET", f"/api/packing-list-templates/{packing_list['id']}")
    assert [i["name"] for i in template["items"]] == ["Goggles", "Towel", "Keys", "Snacks"]
    other = api(live_server, "GET", f"/api/packing-list-days/{second['id']}")
    assert [i["name"] for i in other["items"]] == ["Goggles", "Towel", "Keys", "Snacks"]
    assert next(i for i in other["items"] if i["name"] == "Snacks")["owner_id"] is None


def test_the_bag_menu_stays_in_view_while_typing_on_a_phone(browser, live_server, packing_list):
    """The Bag field is low in its modal. Its suggestions used to send the
    modal back to the top on every keystroke, leaving the field at the bottom
    edge and its menu out of sight."""
    context, page = open_page(
        browser, f"{live_server}/packing-lists", viewport={"width": 375, "height": 667}
    )
    try:
        target = open_row(page, packing_list["id"])
        target.locator("[data-add-item]").click()
        page.wait_for_selector("#item-modal-overlay", state="visible")
        page.evaluate(
            """() => {
                const body = document.querySelector('#item-modal-overlay .modal-body');
                body.scrollTop = body.scrollHeight;
            }"""
        )
        page.focus("#item-bag")
        page.keyboard.type("Browser")
        page.wait_for_selector("#bag-suggestions.open")
        geometry = page.evaluate(
            """() => {
                const body = document.querySelector('#item-modal-overlay .modal-body').getBoundingClientRect();
                const field = document.getElementById('item-bag').getBoundingClientRect();
                const menu = document.getElementById('bag-suggestions').getBoundingClientRect();
                return { bodyTop: body.top, bodyBottom: body.bottom, fieldTop: field.top,
                         menuBottom: menu.bottom };
            }"""
        )
        assert geometry["fieldTop"] >= geometry["bodyTop"] - 0.5
        assert geometry["menuBottom"] <= geometry["bodyBottom"] + 0.5
    finally:
        context.close()


# --- Folding a packing list's groups (#256) -------------------------------------------


def group(root, name):
    return root.locator(f'.list-group:has(.list-group-name:text-is("{name}"))')


def open_groups_named(root) -> list[str]:
    return root.evaluate(
        """(root) => [...root.querySelectorAll('.list-group--collapsible[open] .list-group-name')]
            .map(n => n.textContent.trim())"""
    )


def test_groups_start_folded_and_stay_as_left_while_on_the_page(browser, live_server, packing_list):
    day = packing_list["days"][0]
    context, page = open_page(browser, f"{live_server}/packing-lists", dialogs=[])
    try:
        target = open_card(page, day["id"], groups=False)
        # Every group folded, each still saying how far along it is.
        assert open_groups_named(target) == []
        assert not target.get_by_label("Packed Goggles").is_visible()
        assert group(target, "Emma").locator(".list-group-count").inner_text() == "0 of 2 packed"

        # The whole header opens one group, and only that one.
        group(target, "Emma").locator(".list-group-rule").click()
        assert open_groups_named(card(page, day["id"])) == ["Emma"]
        # `toggle` is dispatched as a task of its own: wait for it to be kept.
        page.wait_for_function("openDayGroups.size === 1")
        assert target.get_by_label("Packed Goggles").is_visible()

        # Folding the packing list away and back leaves its groups as they were.
        disclosure(target).click()
        disclosure(target).click()
        assert open_groups_named(card(page, day["id"])) == ["Emma"]

        # So do the minute's refetch and a whole-list action.
        page.evaluate("loadDays()")
        page.wait_for_load_state("networkidle")
        assert open_groups_named(card(page, day["id"])) == ["Emma"]
        with page.expect_response("**/check-all"):
            card(page, day["id"]).locator("[data-check-all-day]").click()
        page.wait_for_load_state("networkidle")
        assert open_groups_named(card(page, day["id"])) == ["Emma"]

        # An item added to a folded group leaves it folded; the count says so.
        card(page, day["id"]).locator("[data-add-day-item]").click()
        page.fill("#item-name", "Wallet")
        page.select_option("#item-owner", str(packing_list["members"]["Dad"]))
        save_item(page)
        dad = group(card(page, day["id"]), "Dad")
        assert dad.locator(".list-group-count").inner_text() == "1 of 2 packed"
        assert open_groups_named(card(page, day["id"])) == ["Emma"]
    finally:
        context.close()


def test_switching_view_folds_every_group_and_leaves_lists_open(browser, live_server, packing_list):
    day = packing_list["days"][0]
    context, page = open_page(browser, f"{live_server}/packing-lists")
    try:
        open_card(page, day["id"])
        open_row(page, packing_list["id"])
        page.click('#view-chips [data-view="bag"]')
        for root in (card(page, day["id"]), packing_list_row(page, packing_list["id"])):
            assert root.locator(":scope > .disclosure").evaluate("d => d.open")
            assert open_groups_named(root) == []
        # And back again: still folded, not what was open before the switch.
        page.click('#view-chips [data-view="owner"]')
        assert open_groups_named(card(page, day["id"])) == []
    finally:
        context.close()


def test_a_row_dropped_on_a_folded_group_goes_to_its_end(browser, live_server, packing_list):
    context, page = open_page(browser, f"{live_server}/packing-lists")
    try:
        target = open_row(page, packing_list["id"], groups=False)
        group(target, "Everyone").locator("summary").click()
        grip = item_row(target, "Snacks").locator(".drag-handle")
        drag(page, grip, group(target, "Dad").locator("summary").bounding_box())
        with page.expect_response("**/items/reorder"):
            page.mouse.up()
        page.wait_for_load_state("networkidle")

        row = packing_list_row(page, packing_list["id"])
        # Wait for the row to be redrawn from the server: the drag moved the
        # row in the page at once, but only the redraw recounts the group.
        page.wait_for_function(
            f"""document.querySelector('[data-template-row="{packing_list["id"]}"]'
                + ' .list-group[data-group$=":{packing_list["members"]["Dad"]}"]'
                + ' .list-group-count')?.textContent === '2 items'"""
        )
        # Everyone has no items left, but it keeps its group: the fixture's
        # bag has no owner, so it is Everyone's to grab.
        assert sections(row)["Everyone"] == [f"{BAG} ▣"]
        assert sections(row)["Dad"] == ["Keys", "Snacks"]
        # Dad stays folded; Everyone, still there for its bag, stays as the
        # test left it, open.
        assert open_groups_named(row) == ["Everyone"]
        assert group(row, "Dad").locator(".list-group-count").inner_text() == "2 items"
    finally:
        context.close()


def test_a_group_header_is_a_full_target_with_a_focus_ring_on_a_phone(
    browser, live_server, packing_list
):
    day = packing_list["days"][0]
    context, page = open_page(browser, f"{live_server}/packing-lists", PHONE)
    try:
        target = open_card(page, day["id"], groups=False)
        header = target.locator(".list-group--collapsible > summary").first
        header.scroll_into_view_if_needed()
        assert header.bounding_box()["height"] >= 44
        # From the packing list's own summary, Tab lands on its first group.
        disclosure(target).focus()
        page.keyboard.press("Tab")
        assert header.evaluate("el => el === document.activeElement")
        assert header.evaluate("el => getComputedStyle(el).outlineStyle") != "none"
        page.keyboard.press("Enter")
        assert target.locator(".list-group--collapsible[open]").count() == 1
    finally:
        context.close()


def test_the_archive_folds_groups_and_view_refolds_them(browser, live_server):
    context, page = open_page(browser, f"{live_server}/packing-lists/previous")
    try:
        first = page.locator("[data-day-card]", has_text="Swim at Nana's").first
        first.wait_for()
        disclosure(first).click()
        assert open_groups_named(first) == []
        first.locator(".list-group--collapsible > summary").first.click()
        opened = open_groups_named(first)
        assert len(opened) == 1
        # Load more and search redraw every loaded day; the group stays open.
        # (`toggle` is dispatched as a task of its own, so wait for it.)
        page.wait_for_function("openDayGroups.size === 1")
        page.evaluate("archive.render()")
        first = page.locator("[data-day-card]", has_text="Swim at Nana's").first
        assert open_groups_named(first) == opened

        page.click('#view-chips [data-view="bag"]')
        first = page.locator("[data-day-card]", has_text="Swim at Nana's").first
        assert first.locator(":scope > .disclosure").evaluate("d => d.open")
        assert open_groups_named(first) == []
    finally:
        context.close()


# --- Entering items from the keyboard (#258) -------------------------------------------------


def test_notes_is_the_last_field_of_the_item_form(browser, live_server, packing_list):
    context, page = open_page(browser, f"{live_server}/packing-lists")
    try:
        open_row(page, packing_list["id"]).locator("[data-add-item]").click()
        labels = page.locator("#item-form .form-group > label").all_inner_texts()
        assert labels == ["Item", "Owner", "Bag (optional)", "Notes (optional)"]
    finally:
        context.close()


def test_enter_in_the_name_is_save_and_add_another(browser, live_server, packing_list):
    emma = str(packing_list["members"]["Emma"])
    context, page = open_page(browser, f"{live_server}/packing-lists")
    try:
        open_row(page, packing_list["id"]).locator("[data-add-item]").click()
        page.select_option("#item-owner", emma)
        page.fill("#item-bag", BAG)
        page.fill("#item-note", "The blue one")
        page.fill("#item-name", "Swim cap")
        with page.expect_response("**/items"):
            page.press("#item-name", "Enter")
        page.wait_for_function("document.querySelector('#item-name').value === ''")

        # Still open, owner and bag kept, notes cleared, back in the name.
        assert page.locator("#item-modal-overlay").is_visible()
        assert page.input_value("#item-owner") == emma
        assert page.input_value("#item-bag") == BAG
        assert page.input_value("#item-note") == ""
        assert page.evaluate("document.activeElement.id") == "item-name"
        names = [
            i["name"]
            for i in api(live_server, "GET", f"/api/packing-list-templates/{packing_list['id']}")[
                "items"
            ]
        ]
        assert "Swim cap" in names
    finally:
        context.close()


def test_enter_on_a_blank_name_says_so_and_saves_nothing(browser, live_server, packing_list):
    context, page = open_page(browser, f"{live_server}/packing-lists")
    try:
        open_row(page, packing_list["id"]).locator("[data-add-item]").click()
        requests = []
        page.on("request", lambda r: r.method == "POST" and requests.append(r.url))
        page.fill("#item-name", "   ")
        page.press("#item-name", "Enter")

        assert page.locator("#item-modal-overlay").is_visible()
        assert page.evaluate("document.querySelector('#item-name').validationMessage") == (
            "A name can't be empty."
        )
        assert requests == []
        # Typing clears it, so the form can be sent again.
        page.fill("#item-name", "Sunscreen")
        assert page.evaluate("document.querySelector('#item-name').validity.valid")
    finally:
        context.close()


def test_enter_while_editing_an_item_saves_and_closes(browser, live_server, packing_list):
    context, page = open_page(browser, f"{live_server}/packing-lists")
    try:
        target = open_row(page, packing_list["id"])
        item_row(target, "Towel").locator("[data-edit-item]").click()
        page.fill("#item-name", "Beach towel")
        page.press("#item-name", "Enter")
        page.wait_for_selector("#item-modal-overlay", state="hidden")
        names = [
            i["name"]
            for i in api(live_server, "GET", f"/api/packing-list-templates/{packing_list['id']}")[
                "items"
            ]
        ]
        assert "Beach towel" in names
    finally:
        context.close()


# --- The Items filter (#258) -----------------------------------------------------------------


def chip(page, value):
    return page.locator(f'#packed-filters [data-packed="{value}"]')


def test_the_items_filter_narrows_coming_up_and_leaves_folding_alone(
    browser, live_server, packing_list
):
    first, second = packing_list["days"]
    api(
        live_server,
        "PUT",
        f"/api/packing-list-days/{first['id']}/template-items/{packing_list['items']['Goggles']['id']}",
        {"checked": True},
    )
    context, page = open_page(browser, f"{live_server}/packing-lists")
    try:
        target = open_card(page, first["id"], groups=False)
        group(target, "Emma").locator("summary").click()
        page.wait_for_function("openDayGroups.size === 1")

        chip(page, "false").click()
        assert chip(page, "false").get_attribute("aria-pressed") == "true"
        target = card(page, first["id"])
        # The fixture's bag has no owner, and nobody has grabbed it: it heads
        # Everyone under Not packed, as an unpacked item would.
        assert sections(target) == {
            "Dad": ["Keys"],
            "Emma": ["Towel"],
            "Everyone": [f"{BAG} ▣", "Snacks"],
        }
        # Folding as it was, counts the whole list's.
        assert open_groups_named(target) == ["Emma"]
        assert group(target, "Emma").locator(".list-group-count").inner_text() == "1 of 2 packed"
        assert target.locator(".packing-list-progress").inner_text() == "1 of 4 packed"

        # Packed: the day with nothing packed drops out.
        chip(page, "true").click()
        assert chip(page, "false").get_attribute("aria-pressed") == "false"
        assert sections(card(page, first["id"])) == {"Emma": ["Goggles"]}
        assert card(page, second["id"]).count() == 0

        # Clicking the chip that is on turns it off; everything is back as left.
        chip(page, "true").click()
        target = card(page, first["id"])
        assert sections(target)["Emma"] == ["Goggles", "Towel"]
        assert open_groups_named(target) == ["Emma"]
        assert card(page, second["id"]).count() == 1

        # It combines with Due now, and Clear Filters turns both off.
        chip(page, "false").click()
        page.click('#day-filters [data-filter="due-now"]')
        assert card(page, first["id"]).count() == 0  # packs in 20 days
        page.click("#filter-clear")
        assert chip(page, "false").get_attribute("aria-pressed") == "false"
        assert page.locator("#day-filters .filter-chip.active").count() == 0
        assert card(page, first["id"]).count() == 1
    finally:
        context.close()


def test_a_row_leaves_as_it_is_ticked_and_focus_moves_on(browser, live_server, packing_list):
    first = packing_list["days"][0]
    context, page = open_page(browser, f"{live_server}/packing-lists")
    try:
        target = open_card(page, first["id"], groups=False)
        assert list(sections(target)) == ["Dad", "Emma", "Everyone"]
        group(target, "Emma").locator("summary").click()
        group(target, "Everyone").locator("summary").click()
        chip(page, "false").click()
        target = card(page, first["id"])

        def tick(name):
            with page.expect_response("**/template-items/**"):
                target.get_by_label(f"Packed {name}").click()

        def grab(name):
            with page.expect_response("**/bags/**"):
                target.get_by_label(f"Grabbed {name}").click()

        def focused():
            return page.evaluate(
                """() => { const el = document.activeElement;
                    return el.getAttribute('aria-label')
                        || el.closest('.list-group')?.querySelector('.list-group-name').textContent
                        || (el.closest('details[data-day]') ? 'View more' : el.tagName); }"""
            )

        # The next row in its group.
        tick("Goggles")
        assert item_row(target, "Goggles").count() == 0
        assert focused() == "Packed Towel"
        # Its group goes with it: the next open group's first row, which is
        # Everyone's bag — bags head their group.
        tick("Towel")
        assert group(target, "Emma").count() == 0
        assert focused() == f"Grabbed {BAG}"
        # A bag leaves the same way, and focus moves on to the item under it.
        grab(BAG)
        assert focused() == "Packed Snacks"
        # No open group left: the header of the group still drawn, folded.
        tick("Snacks")
        assert focused() == "Dad"
        assert not group(target, "Dad").evaluate("g => g.open")
        page.keyboard.press("Enter")
        # The whole list goes: the next packing list's View more.
        tick("Keys")
        assert card(page, first["id"]).count() == 0
        assert focused() == "View more"
        # Counts went to the server: everything on the day is packed.
        assert api(live_server, "GET", f"/api/packing-list-days/{first['id']}")["checked"] == 4
    finally:
        context.close()


def test_under_packed_an_unticked_row_leaves(browser, live_server, packing_list):
    first = packing_list["days"][0]
    for name in ("Goggles", "Towel"):
        api(
            live_server,
            "PUT",
            f"/api/packing-list-days/{first['id']}/template-items/{packing_list['items'][name]['id']}",
            {"checked": True},
        )
    context, page = open_page(browser, f"{live_server}/packing-lists")
    try:
        chip(page, "true").click()
        target = open_card(page, first["id"])
        with page.expect_response("**/template-items/**"):
            target.get_by_label("Packed Goggles").click()
        assert sections(target) == {"Emma": ["Towel"]}
        page.wait_for_function(
            f"""document.querySelector('[data-day-card="{first["id"]}"] .packing-list-progress')
                .textContent === '1 of 4 packed'"""
        )
    finally:
        context.close()


def test_enter_in_the_bag_is_save_and_add_another(browser, live_server, packing_list):
    context, page = open_page(browser, f"{live_server}/packing-lists")
    try:
        open_row(page, packing_list["id"]).locator("[data-add-item]").click()
        page.fill("#item-name", "Swim cap")
        # A highlighted bag suggestion is accepted, and nothing is saved yet.
        requests = []
        page.on("request", lambda r: r.method == "POST" and requests.append(r.url))
        page.fill("#item-bag", BAG[:6])
        page.wait_for_selector("#bag-suggestions.open")
        page.press("#item-bag", "ArrowDown")
        page.press("#item-bag", "Enter")
        assert page.input_value("#item-bag") == BAG
        assert requests == []

        with page.expect_response("**/items"):
            page.press("#item-bag", "Enter")
        page.wait_for_function("document.querySelector('#item-name').value === ''")
        assert page.locator("#item-modal-overlay").is_visible()
        assert page.input_value("#item-bag") == BAG
        assert page.evaluate("document.activeElement.id") == "item-name"
    finally:
        context.close()


def test_enter_in_the_bag_on_a_blank_name_sends_you_to_the_name(browser, live_server, packing_list):
    context, page = open_page(browser, f"{live_server}/packing-lists")
    try:
        open_row(page, packing_list["id"]).locator("[data-add-item]").click()
        requests = []
        page.on("request", lambda r: r.method == "POST" and requests.append(r.url))
        page.fill("#item-name", "  ")
        page.fill("#item-bag", "Car")
        page.press("#item-bag", "Enter")
        assert page.evaluate("document.activeElement.id") == "item-name"
        assert page.evaluate("document.querySelector('#item-name').validationMessage") == (
            "A name can't be empty."
        )
        assert requests == []
    finally:
        context.close()


def test_enter_in_the_bag_while_editing_saves_and_closes(browser, live_server, packing_list):
    context, page = open_page(browser, f"{live_server}/packing-lists")
    try:
        target = open_row(page, packing_list["id"])
        item_row(target, "Keys").locator("[data-edit-item]").click()
        page.fill("#item-bag", BAG)
        page.press("#item-bag", "Escape")  # close the suggestion menu, not the modal
        page.press("#item-bag", "Enter")
        page.wait_for_selector("#item-modal-overlay", state="hidden")
        keys = next(
            i
            for i in api(live_server, "GET", f"/api/packing-list-templates/{packing_list['id']}")[
                "items"
            ]
            if i["name"] == "Keys"
        )
        assert keys["bag_id"] == packing_list["bag_id"]
    finally:
        context.close()


# --- Starting a template from a copy of another (#260) --------------------------------


def open_add_template(page):
    page.click("#btn-add-template")
    page.wait_for_selector("#template-modal-overlay .modal-content", state="visible")


def delete_templates_named(base: str, name: str) -> None:
    for template in api(base, "GET", "/api/packing-list-templates"):
        if template["name"] == name:
            delete_template(base, template["id"])


def test_a_new_template_can_start_from_a_copy_of_another(browser, live_server, packing_list):
    context, page = open_page(browser, f"{live_server}/packing-lists")
    requests = []
    page.on(
        "request",
        lambda r: (
            requests.append(r.post_data_json)
            if r.method == "POST" and r.url.endswith("/api/packing-list-templates")
            else None
        ),
    )
    try:
        open_add_template(page)
        # Blank list is the default and shows no template to pick.
        assert page.is_checked('input[name="template-start"][value="blank"]')
        assert not page.locator("#template-template-fields").is_visible()
        # A template has no copy/sync choice: that is a day's.
        assert page.locator("#template-mode-group").count() == 0

        page.check('input[name="template-start"][value="template"]')
        assert page.locator("#template-template-fields").is_visible()
        assert page.input_value("#template-source") == ""
        # Picking Template means picking one: Save refuses until then.
        page.fill("#template-name", "Browser test sleepover")
        page.click("#template-modal-overlay button[type=submit]")
        page.wait_for_timeout(300)
        assert page.locator("#template-modal-overlay").is_visible()

        page.select_option("#template-source", label="Browser test trip")
        # Nothing is filled in: the name, description and lead time are its own.
        assert page.input_value("#template-name") == "Browser test sleepover"
        assert page.input_value("#template-description") == ""
        assert page.input_value("#template-pack-days") == "0"
        page.click("#template-modal-overlay button[type=submit]")
        page.wait_for_selector("#template-modal-overlay", state="hidden")

        assert requests[-1]["copy_from_template_id"] == packing_list["id"]
        row = page.locator("[data-template-row]", has_text="Browser test sleepover")
        row.wait_for()
        # Opens like any new template, and its items came with it.
        assert row.locator(".disclosure").evaluate("el => el.open")
        assert "4 items" in row.locator(".editable-item-meta").inner_text()
        open_groups(row)
        for name in ("Goggles", "Towel", "Keys", "Snacks"):
            assert item_row(row, name).count() == 1
    finally:
        context.close()
        delete_templates_named(live_server, "Browser test sleepover")


def test_a_blank_template_sends_no_source(browser, live_server, packing_list):
    context, page = open_page(browser, f"{live_server}/packing-lists")
    requests = []
    page.on(
        "request",
        lambda r: (
            requests.append(r.post_data_json)
            if r.method == "POST" and r.url.endswith("/api/packing-list-templates")
            else None
        ),
    )
    try:
        open_add_template(page)
        page.fill("#template-name", "Browser test blank")
        page.click("#template-modal-overlay button[type=submit]")
        page.wait_for_selector("#template-modal-overlay", state="hidden")
        assert "copy_from_template_id" not in requests[-1]
        row = page.locator("[data-template-row]", has_text="Browser test blank")
        row.wait_for()
        assert "0 items" in row.locator(".editable-item-meta").inner_text()
    finally:
        context.close()
        delete_templates_named(live_server, "Browser test blank")


def test_start_from_is_for_a_new_template_and_reopens_blank(browser, live_server, packing_list):
    context, page = open_page(browser, f"{live_server}/packing-lists")
    try:
        open_add_template(page)
        page.check('input[name="template-start"][value="template"]')
        page.select_option("#template-source", label="Browser test trip")
        page.click("#btn-cancel-template")
        page.wait_for_selector("#template-modal-overlay", state="hidden")

        # Each opening starts from Blank list again.
        open_add_template(page)
        assert page.is_checked('input[name="template-start"][value="blank"]')
        assert not page.locator("#template-template-fields").is_visible()
        page.click("#btn-cancel-template")
        page.wait_for_selector("#template-modal-overlay", state="hidden")

        # Editing one has nothing to start from, and does not need a source.
        packing_list_row(page, packing_list["id"]).locator("[data-edit-template]").click()
        page.wait_for_selector("#template-modal-overlay .modal-content", state="visible")
        assert not page.locator("#template-start-from").is_visible()
        page.fill("#template-description", "Edited")
        page.click("#template-modal-overlay button[type=submit]")
        page.wait_for_selector("#template-modal-overlay", state="hidden")
        assert (
            api(live_server, "GET", f"/api/packing-list-templates/{packing_list['id']}")[
                "description"
            ]
            == "Edited"
        )
    finally:
        context.close()


def test_template_start_from_is_hidden_with_no_templates(browser, live_server):
    context = browser.new_context(viewport=DESKTOP)
    page = context.new_page()
    page.route(
        "**/api/packing-list-templates",
        lambda route: route.fulfill(status=200, content_type="application/json", body="[]"),
    )
    try:
        page.goto(f"{live_server}/packing-lists", wait_until="networkidle")
        open_add_template(page)
        assert not page.locator("#template-source-group").is_visible()
        assert page.locator("#template-name").is_visible()
    finally:
        context.close()


def test_a_taken_name_is_refused_and_the_modal_stays(browser, live_server, packing_list):
    dialogs = []
    context, page = open_page(browser, f"{live_server}/packing-lists", dialogs=dialogs)
    try:
        open_add_template(page)
        page.check('input[name="template-start"][value="template"]')
        page.select_option("#template-source", label="Browser test trip")
        page.fill("#template-name", "Browser test trip")
        page.click("#template-modal-overlay button[type=submit]")
        page.wait_for_timeout(500)
        assert dialogs == ["A packing list template with that name already exists."]
        assert page.locator("#template-modal-overlay").is_visible()
    finally:
        context.close()


# --- Bags: owners, nesting, grabbing (#262) ------------------------------------------------


def _own_bag(live_server, packing_list, owner="Emma"):
    api(
        live_server,
        "PUT",
        f"/api/packing-list-bags/{packing_list['bag_id']}",
        {"owner_id": packing_list["members"][owner]},
    )


def test_a_persons_bags_head_their_group_and_a_grab_counts_in_place(
    browser, live_server, packing_list
):
    _own_bag(live_server, packing_list)
    day = packing_list["days"][0]
    context, page = open_page(browser, f"{live_server}/packing-lists")
    try:
        target = open_card(page, day["id"])
        emma = group(target, "Emma")
        # The bag first, marked as one, then her items; the count says both.
        assert sections(target)["Emma"] == [f"{BAG} ▣", "Goggles", "Towel"]
        assert emma.locator(".list-group-lead .editable-item").count() == 1
        assert emma.locator(".list-group-count").inner_text() == "0 of 2 packed · 0 of 1 bag"
        bag_line = target.locator(".packing-list-bag-progress")
        assert bag_line.inner_text() == "0 of 1 bag grabbed"

        box = target.get_by_label(f"Grabbed {BAG}")
        with page.expect_response("**/bags/**"):
            box.click()
        page.wait_for_function(
            f"document.querySelector('[data-day-card=\"{day['id']}\"] .packing-list-bag-progress')"
            "?.textContent === '1 of 1 bag grabbed'"
        )
        assert emma.locator(".list-group-count").inner_text() == "0 of 2 packed · 1 of 1 bag"
        # Only the counts moved: focus is still on the box just ticked.
        assert box.evaluate("el => el === document.activeElement")
        # A bag's row is not part of the group's order: no grip, no Edit.
        lead = emma.locator(".list-group-lead .editable-item")
        assert lead.locator(".drag-handle").count() == 0
        assert lead.get_by_role("button").count() == 0

        # By bag, no bag rows: the groups are the bags.
        page.click('#view-chips [data-view="bag"]')
        assert card(page, day["id"]).locator("[data-bag-row]").count() == 0
    finally:
        context.close()

    assert api(live_server, "GET", f"/api/packing-list-days/{day['id']}")["bags_checked"] == 1


def test_change_bags_removes_a_bag_from_one_day_and_keeps_its_items(
    browser, live_server, packing_list
):
    _own_bag(live_server, packing_list)
    first, second = packing_list["days"]
    dialogs = []
    context, page = open_page(browser, f"{live_server}/packing-lists", dialogs=dialogs)
    try:
        target = open_card(page, first["id"])
        target.locator("[data-change-day-bags]").click()
        modal = page.locator("#list-bags-modal-overlay")
        modal.locator(".modal-content").wait_for()
        assert page.locator("#list-bags-modal-title").inner_text().lower() == (
            "change bags: browser test trip"
        )
        # Remove only marks the bag; nothing is written until Save.
        modal.get_by_role("button", name="Remove from day").click()
        modal.get_by_text("Removed when you save.").wait_for()
        assert dialogs == []
        with page.expect_response("**/remove"):
            modal.get_by_role("button", name="Save").click()
        modal.wait_for(state="hidden")
        assert dialogs == [
            f'Remove {BAG} from this day? The 2 items in it move to "No bag" on this day'
            " only — nothing is taken off the list."
        ]
        page.wait_for_function(
            f"!document.querySelector('[data-day-card=\"{first['id']}\"] [data-bag-row]')"
        )
    finally:
        context.close()

    on_first = api(live_server, "GET", f"/api/packing-list-days/{first['id']}")
    assert on_first["bags"] == []
    assert len(on_first["items"]) == 4
    assert {i["name"] for i in on_first["items"] if i["changed"]} == {"Goggles", "Towel"}
    # The other day, and the template, keep the bag.
    on_second = api(live_server, "GET", f"/api/packing-list-days/{second['id']}")
    assert [b["name"] for b in on_second["bags"]] == [BAG]


# --- Resync with template (#266) ----------------------------------------------------


def test_resync_shows_only_on_a_changed_day_and_asks_how_much(browser, live_server, packing_list):
    first, second = packing_list["days"]
    towel, goggles = packing_list["items"]["Towel"], packing_list["items"]["Goggles"]
    base = f"/api/packing-list-days/{first['id']}"
    api(live_server, "PUT", f"{base}/template-items/{towel['id']}", {"checked": True})
    api(live_server, "PUT", f"{base}/template-items/{goggles['id']}", {"name": "Spare goggles"})
    context, page = open_page(browser, f"{live_server}/packing-lists")
    try:
        unchanged = open_card(page, second["id"], groups=False)
        assert unchanged.locator("[data-resync-day]").count() == 0

        target = open_card(page, first["id"], groups=False)
        buttons = target.locator(".packing-list-day-actions button").all_inner_texts()
        assert buttons[-1] == "Resync with template"
        target.get_by_role("button", name="Resync with template").click()

        modal = page.locator("#day-resync-modal-overlay")
        modal.wait_for(state="visible")
        assert modal.locator("h3").inner_text().lower() == "resync with template: browser test trip"
        radios = modal.locator('input[name="day-resync-scope"]')
        assert [radios.nth(i).is_checked() for i in range(radios.count())] == [False, False]
        confirm = modal.get_by_role("button", name="Resync", exact=True)
        assert confirm.is_disabled()

        modal.get_by_label("Only item changes").check()
        assert confirm.is_enabled()
        confirm.click()
        modal.wait_for(state="hidden")
        card(page, first["id"]).locator("[data-resync-day]").wait_for(state="detached")
        assert card(page, first["id"]).locator(".day-changes").count() == 0
    finally:
        context.close()

    day = api(live_server, "GET", base)
    assert (day["changed_count"], day["checked"]) == (0, 1)
    assert "Goggles" in [i["name"] for i in day["items"]]


def test_cancel_leaves_the_day_as_it_was(browser, live_server, packing_list):
    first = packing_list["days"][0]
    goggles = packing_list["items"]["Goggles"]
    base = f"/api/packing-list-days/{first['id']}"
    api(live_server, "DELETE", f"{base}/template-items/{goggles['id']}")
    context, page = open_page(browser, f"{live_server}/packing-lists")
    try:
        target = open_card(page, first["id"], groups=False)
        target.get_by_role("button", name="Resync with template").click()
        modal = page.locator("#day-resync-modal-overlay")
        modal.get_by_label("All changes on this day").check()
        modal.get_by_role("button", name="Cancel").click()
        modal.wait_for(state="hidden")
    finally:
        context.close()
    assert api(live_server, "GET", base)["changed_count"] == 1


def test_a_dot_separates_quiet_buttons_but_never_starts_a_line(browser, live_server, packing_list):
    """At 320px the day's quiet buttons wrap: "Change bags · Remove from day"
    on one line and "Resync with template" alone on the next, flush with the
    row's edge and with no dot before it."""
    first = packing_list["days"][0]
    goggles = packing_list["items"]["Goggles"]
    api(
        live_server,
        "PUT",
        f"/api/packing-list-days/{first['id']}/template-items/{goggles['id']}",
        {"name": "Spare goggles"},
    )
    context, page = open_page(
        browser, f"{live_server}/packing-lists", viewport={"width": 320, "height": 800}
    )
    try:
        target = open_card(page, first["id"], groups=False)
        page.wait_for_function(
            f"""document.querySelector('[data-resync-day="{first["id"]}"]')
                .classList.contains('is-line-start')"""
        )
        quiet = target.locator(".packing-list-day-actions").evaluate(
            """(row) => {
                const left = row.getBoundingClientRect().left;
                return [...row.querySelectorAll('.btn--quiet + .btn--quiet')].map(b => {
                    const before = b.previousElementSibling.getBoundingClientRect();
                    const box = b.getBoundingClientRect();
                    return {
                        name: b.textContent.trim(),
                        wrapped: box.top >= before.bottom - 1,
                        marked: b.classList.contains('is-line-start'),
                        dot: getComputedStyle(b, '::before').content,
                        flush: Math.abs(box.left - left) < 1,
                    };
                });
            }"""
        )
    finally:
        context.close()

    assert quiet == [
        {
            "name": "Remove from day",
            "wrapped": False,
            "marked": False,
            "dot": '"·"',
            "flush": False,
        },
        {
            "name": "Resync with template",
            "wrapped": True,
            "marked": True,
            "dot": "none",
            "flush": True,
        },
    ]
