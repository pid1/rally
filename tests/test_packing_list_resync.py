"""Resyncing a day's packing list with its template (#266).

A day kept in sync with a template can drift from it — items edited, removed
or added on that day only, its own order, its own reading of a bag, a label,
a lead time — and before this there was no way back. ``items`` undoes the
item changes and nothing else a person chose; ``all`` undoes every way the day
differs. The template, other days and item history are never touched.

The bag rule is the subtle one: taking a bag off a day un-nests the bags that
went in it, and only that removal — recorded on the reading as
``removed_from_bag_id`` — is put back, and only once the bag is on the day
again. A hand edit since then is somebody's choice and is left alone.
"""

from datetime import UTC, datetime

import pytest

from rally.models import (
    PackingListDay,
    PackingListDayBag,
    PackingListDayBagCheck,
    PackingListDayCheck,
    PackingListDayItem,
    PackingListItemHistory,
)

# Thursday, 1 October 2026, mid-morning in Chicago.
NOW = datetime(2026, 10, 1, 15, 0, tzinfo=UTC)
SATURDAY = "2026-10-03"
SUNDAY = "2026-10-04"


@pytest.fixture(autouse=True)
def _frozen(frozen_now, local_timezone):
    local_timezone("America/Chicago")
    frozen_now(NOW)


def _ok(response, status=200):
    assert response.status_code == status, response.text
    return response.json()


def _bag(client, name, **defaults):
    bag = _ok(client.post("/api/packing-list-bags", json={"name": name}), 201)
    if defaults:
        bag = _ok(client.put(f"/api/packing-list-bags/{bag['id']}", json=defaults))
    return bag


def _item(client, template_id, name, **extra):
    return _ok(
        client.post(
            f"/api/packing-list-templates/{template_id}/items", json={"name": name, **extra}
        ),
        201,
    )


def _day(client, template_id, day=SATURDAY, **extra):
    return _ok(
        client.post(
            "/api/packing-list-days",
            json={"packing_list_template_id": template_id, "date": day, **extra},
        ),
        201,
    )


def _resync(client, day_id, scope):
    return _ok(client.post(f"/api/packing-list-days/{day_id}/resync", json={"scope": scope}))


def _items(payload):
    return {i["name"]: i for i in payload["items"]}


def _bags(payload):
    """{name: parent name} for each bag on a list."""
    names = {b["id"]: b["name"] for b in payload["bags"]}
    return {b["name"]: names.get(b["parent_bag_id"]) for b in payload["bags"]}


def _reading(db_session, day_id, bag_id):
    db_session.expire_all()
    return (
        db_session.query(PackingListDayBag)
        .filter(PackingListDayBag.day_id == day_id, PackingListDayBag.bag_id == bag_id)
        .first()
    )


@pytest.fixture
def beach(client, make_member):
    """The household's Toiletries bag goes in the Suitcase and the Electronics
    pouch in the Carry-on, which holds nothing directly. The template packs a
    lead time of 1 day; Saturday was added by hand with a label and a lead time
    of its own."""
    emma = make_member("Emma")
    suitcase = _bag(client, "Suitcase")
    toiletries = _bag(client, "Toiletries bag", parent_bag_id=suitcase["id"])
    carry_on = _bag(client, "Carry-on")
    pouch = _bag(client, "Electronics pouch", parent_bag_id=carry_on["id"])
    template = _ok(
        client.post(
            "/api/packing-list-templates", json={"name": "Beach weekend", "pack_days_before": 1}
        ),
        201,
    )
    goggles = _item(client, template["id"], "Goggles")
    sunscreen = _item(client, template["id"], "Sunscreen")
    towel = _item(client, template["id"], "Towel")
    swimsuit = _item(client, template["id"], "Swimsuit", bag_id=suitcase["id"])
    toothbrush = _item(client, template["id"], "Toothbrush", bag_id=toiletries["id"])
    charger = _item(client, template["id"], "Charger", bag_id=pouch["id"])
    day = _day(client, template["id"], label="Emma", pack_days_before=2)
    return {
        "emma": emma,
        "suitcase": suitcase,
        "toiletries": toiletries,
        "carry_on": carry_on,
        "pouch": pouch,
        "template": template,
        "goggles": goggles,
        "sunscreen": sunscreen,
        "towel": towel,
        "swimsuit": swimsuit,
        "toothbrush": toothbrush,
        "charger": charger,
        "day": day,
    }


def _drift(client, beach):
    """Tuesday's mistake: goggles edited, sunscreen removed, a swim cap added,
    goggles and towel packed, the day reordered and a bag read differently."""
    base = f"/api/packing-list-days/{beach['day']['id']}"
    _ok(
        client.put(
            f"{base}/template-items/{beach['goggles']['id']}", json={"name": "Spare goggles"}
        )
    )
    _ok(client.delete(f"{base}/template-items/{beach['sunscreen']['id']}"))
    _ok(client.post(f"{base}/day-items", json={"name": "Swim cap"}), 201)
    for item in (beach["goggles"], beach["towel"]):
        _ok(client.put(f"{base}/template-items/{item['id']}", json={"checked": True}))
    _ok(client.put(f"{base}/bags/{beach['suitcase']['id']}", json={"checked": True}))
    _ok(
        client.post(
            f"{base}/items/reorder",
            json={
                "view": "owner",
                "key": None,
                "items": [{"source": "template", "id": beach["towel"]["id"]}],
            },
        )
    )
    _ok(client.put(f"{base}/bags/{beach['pouch']['id']}", json={"owner_id": beach["emma"].id}))
    return _ok(client.get(base))


# --- Only item changes -------------------------------------------------------------


def test_only_item_changes_undoes_edits_removals_and_additions(client, beach):
    drifted = _drift(client, beach)
    assert drifted["changed_count"] == 3

    day = _resync(client, beach["day"]["id"], "items")

    items = _items(day)
    assert set(items) == {"Goggles", "Sunscreen", "Towel", "Swimsuit", "Toothbrush", "Charger"}
    assert not any(i["changed"] for i in day["items"])
    assert day["changed_count"] == 0
    # A check names the template item, so the edited goggles stay packed; the
    # sunscreen's check went when it was removed, so it comes back unchecked.
    assert items["Goggles"]["checked"] and items["Towel"]["checked"]
    assert not items["Sunscreen"]["checked"]
    assert (day["checked"], day["total"]) == (2, 6)


def test_only_item_changes_leaves_everything_else_alone(client, db_session, beach):
    _drift(client, beach)
    day_id = beach["day"]["id"]
    order_before = db_session.get(PackingListDay, day_id).item_order

    day = _resync(client, day_id, "items")

    assert (day["label"], day["pack_days_before"]) == ("Emma", 2)
    db_session.expire_all()
    assert db_session.get(PackingListDay, day_id).item_order == order_before
    pouch = next(b for b in day["bags"] if b["name"] == "Electronics pouch")
    assert (pouch["owner_id"], pouch["changed"]) == (beach["emma"].id, True)
    suitcase = next(b for b in day["bags"] if b["name"] == "Suitcase")
    assert suitcase["checked"]


def test_a_restored_item_reads_after_a_days_own_order(client, beach):
    _drift(client, beach)
    day = _resync(client, beach["day"]["id"], "items")
    # The order was written while sunscreen was off the day, so it does not
    # name it: it reads after everything the order names.
    assert [i["name"] for i in day["items"]][-1] == "Sunscreen"


# --- All changes on this day -------------------------------------------------------


def test_all_changes_undoes_every_way_the_day_differs(client, db_session, beach):
    _drift(client, beach)
    day_id = beach["day"]["id"]

    day = _resync(client, day_id, "all")

    assert [i["name"] for i in day["items"]] == [
        "Goggles",
        "Sunscreen",
        "Towel",
        "Swimsuit",
        "Toothbrush",
        "Charger",
    ]
    assert (day["checked"], day["bags_checked"], day["changed_count"]) == (0, 0, 0)
    assert not any(b["changed"] for b in day["bags"])
    assert day["label"] is None
    assert day["pack_days_before"] == 1
    db_session.expire_all()
    row = db_session.get(PackingListDay, day_id)
    assert (row.item_order, row.pack_days_before, row.label_edited) == (None, None, False)
    for model in (
        PackingListDayItem,
        PackingListDayCheck,
        PackingListDayBag,
        PackingListDayBagCheck,
    ):
        assert db_session.query(model).filter(model.day_id == day_id).count() == 0, model


def test_all_changes_gives_a_scheduled_day_its_schedules_label_again(client, beach):
    template_id = beach["template"]["id"]
    schedule = _ok(
        client.post(
            "/api/packing-list-template-schedules",
            json={
                "packing_list_template_id": template_id,
                "recurrence_type": "daily",
                "start_date": SUNDAY,
                "label": "Emma",
            },
        ),
        201,
    )
    days = _ok(client.get("/api/packing-list-days"))
    sunday = next(d for d in days if d["date"] == SUNDAY)
    base = f"/api/packing-list-days/{sunday['id']}"
    _ok(client.put(base, json={"label": "Emma — field trip"}))
    _ok(client.delete(f"{base}/template-items/{beach['goggles']['id']}"))

    assert _resync(client, sunday["id"], "all")["label"] == "Emma"

    # The label is no longer the day's own, so the schedule's relabel reaches it.
    _ok(
        client.put(
            f"/api/packing-list-template-schedules/{schedule['id']}",
            json={"label": "Emma and Jake"},
        )
    )
    assert _ok(client.get(base))["label"] == "Emma and Jake"


# --- What a resync never touches ---------------------------------------------------


@pytest.mark.parametrize("scope", ["items", "all"])
def test_the_template_other_days_and_history_are_untouched(client, db_session, beach, scope):
    template_id = beach["template"]["id"]
    sunday = _day(client, template_id, day=SUNDAY)
    sunday_base = f"/api/packing-list-days/{sunday['id']}"
    _ok(client.delete(f"{sunday_base}/template-items/{beach['goggles']['id']}"))
    _ok(client.put(f"{sunday_base}/template-items/{beach['towel']['id']}", json={"checked": True}))
    _drift(client, beach)
    template_before = _ok(client.get(f"/api/packing-list-templates/{template_id}"))
    sunday_before = _ok(client.get(sunday_base))
    history_before = sorted(
        (h.name_key, h.times_added) for h in db_session.query(PackingListItemHistory)
    )

    day = _resync(client, beach["day"]["id"], scope)

    assert (day["date"], day["packing_list_template_id"]) == (SATURDAY, template_id)
    assert _ok(client.get(f"/api/packing-list-templates/{template_id}")) == template_before
    assert _ok(client.get(sunday_base)) == sunday_before
    db_session.expire_all()
    history_after = sorted(
        (h.name_key, h.times_added) for h in db_session.query(PackingListItemHistory)
    )
    assert history_after == history_before


@pytest.mark.parametrize("scope", ["items", "all"])
def test_resync_is_idempotent(client, beach, scope):
    _drift(client, beach)
    once = _resync(client, beach["day"]["id"], scope)
    assert _resync(client, beach["day"]["id"], scope) == once


def test_resync_is_refused_where_it_cannot_apply(client, db_session, beach):
    day_id = beach["day"]["id"]
    url = f"/api/packing-list-days/{day_id}/resync"
    assert client.post(url, json={}).status_code == 422
    assert client.post(url, json={"scope": "everything"}).status_code == 422
    assert (
        client.post("/api/packing-list-days/999/resync", json={"scope": "all"}).status_code == 404
    )

    one_off = _ok(
        client.post(
            "/api/packing-list-days",
            json={"name": "Concert", "date": SATURDAY, "pack_days_before": 0},
        ),
        201,
    )
    response = client.post(
        f"/api/packing-list-days/{one_off['id']}/resync", json={"scope": "items"}
    )
    assert response.status_code == 422

    db_session.query(PackingListDay).filter(PackingListDay.id == day_id).update(
        {PackingListDay.date: "2026-09-30"}
    )
    db_session.commit()
    assert client.post(url, json={"scope": "all"}).status_code == 403


def test_taking_a_list_off_its_day_still_clears_every_row(client, db_session, beach):
    _drift(client, beach)
    day_id = beach["day"]["id"]
    assert client.delete(f"/api/packing-list-days/{day_id}").status_code == 204
    db_session.expire_all()
    for model in (
        PackingListDayItem,
        PackingListDayCheck,
        PackingListDayBag,
        PackingListDayBagCheck,
    ):
        assert db_session.query(model).filter(model.day_id == day_id).count() == 0, model


# --- Bags a removal un-nested -------------------------------------------------------


def _remove_bag(client, day_id, bag_id):
    return _ok(client.post(f"/api/packing-list-days/{day_id}/bags/{bag_id}/remove"))


def test_removing_a_bag_records_what_it_took_out(client, db_session, beach):
    day_id = beach["day"]["id"]
    _remove_bag(client, day_id, beach["suitcase"]["id"])
    reading = _reading(db_session, day_id, beach["toiletries"]["id"])
    assert (reading.parent_bag_id, reading.removed_from_bag_id) == (None, beach["suitcase"]["id"])


def test_only_item_changes_puts_a_removed_bags_bags_back_in_it(client, db_session, beach):
    day_id = beach["day"]["id"]
    removed = _remove_bag(client, day_id, beach["suitcase"]["id"])
    assert "Suitcase" not in _bags(removed)
    assert _bags(removed)["Toiletries bag"] is None

    day = _resync(client, day_id, "items")

    assert _bags(day)["Toiletries bag"] == "Suitcase"
    assert _items(day)["Swimsuit"]["bag_id"] == beach["suitcase"]["id"]
    # The reading now says what the household says, so it is gone.
    assert _reading(db_session, day_id, beach["toiletries"]["id"]) is None


def test_an_owner_set_before_the_removal_survives(client, db_session, beach):
    day_id = beach["day"]["id"]
    toiletries = beach["toiletries"]["id"]
    _ok(
        client.put(
            f"/api/packing-list-days/{day_id}/bags/{toiletries}",
            json={"owner_id": beach["emma"].id},
        )
    )
    _remove_bag(client, day_id, beach["suitcase"]["id"])

    day = _resync(client, day_id, "items")

    bag = next(b for b in day["bags"] if b["name"] == "Toiletries bag")
    assert (bag["owner_id"], bag["parent_bag_id"]) == (beach["emma"].id, beach["suitcase"]["id"])
    reading = _reading(db_session, day_id, toiletries)
    assert reading.removed_from_bag_id is None


@pytest.mark.parametrize("edit", ["parent", "owner"])
def test_an_edit_after_the_removal_is_left_alone(client, db_session, beach, edit):
    day_id = beach["day"]["id"]
    toiletries = beach["toiletries"]["id"]
    _remove_bag(client, day_id, beach["suitcase"]["id"])
    body = {"parent_bag_id": None} if edit == "parent" else {"owner_id": beach["emma"].id}
    _ok(client.put(f"/api/packing-list-days/{day_id}/bags/{toiletries}", json=body))
    assert _reading(db_session, day_id, toiletries).removed_from_bag_id is None

    day = _resync(client, day_id, "items")

    assert "Suitcase" in _bags(day)
    assert _bags(day)["Toiletries bag"] is None
    if edit == "owner":
        bag = next(b for b in day["bags"] if b["name"] == "Toiletries bag")
        assert bag["owner_id"] == beach["emma"].id


def test_a_removed_bag_that_does_not_come_back_keeps_its_bags_out(client, db_session, beach):
    day_id = beach["day"]["id"]
    removed = _remove_bag(client, day_id, beach["carry_on"]["id"])
    assert "Carry-on" not in _bags(removed)
    _ok(
        client.put(
            f"/api/packing-list-days/{day_id}/template-items/{beach['goggles']['id']}",
            json={"name": "Spare goggles"},
        )
    )

    day = _resync(client, day_id, "items")

    # No item came out of the carry-on, so undoing item changes brings
    # nothing back into it: it stays off the day, and the pouch stays out.
    assert "Carry-on" not in _bags(day)
    assert _bags(day)["Electronics pouch"] is None
    reading = _reading(db_session, day_id, beach["pouch"]["id"])
    assert reading.removed_from_bag_id == beach["carry_on"]["id"]


def test_a_removed_bag_brought_back_by_hand_gets_its_bags_on_resync(client, beach):
    day_id = beach["day"]["id"]
    base = f"/api/packing-list-days/{day_id}"
    _remove_bag(client, day_id, beach["suitcase"]["id"])
    back = _ok(
        client.put(
            f"{base}/template-items/{beach['towel']['id']}",
            json={"bag_id": beach["suitcase"]["id"]},
        )
    )
    # Bringing it back by hand restores nothing on its own.
    assert _bags(back)["Toiletries bag"] is None

    assert _bags(_resync(client, day_id, "items"))["Toiletries bag"] == "Suitcase"


def test_all_changes_puts_every_removed_bags_bags_back(client, beach):
    day_id = beach["day"]["id"]
    _remove_bag(client, day_id, beach["suitcase"]["id"])
    _remove_bag(client, day_id, beach["carry_on"]["id"])

    bags = _bags(_resync(client, day_id, "all"))

    assert (bags["Toiletries bag"], bags["Electronics pouch"]) == ("Suitcase", "Carry-on")


def test_a_grab_check_keeps_the_record_and_reset_drops_it(client, db_session, beach):
    day_id = beach["day"]["id"]
    toiletries = beach["toiletries"]["id"]
    _remove_bag(client, day_id, beach["suitcase"]["id"])
    _ok(client.put(f"/api/packing-list-days/{day_id}/bags/{toiletries}", json={"checked": True}))
    assert _reading(db_session, day_id, toiletries).removed_from_bag_id == beach["suitcase"]["id"]

    _ok(client.delete(f"/api/packing-list-days/{day_id}/bags/{toiletries}/reading"))
    assert _reading(db_session, day_id, toiletries) is None


def test_deleting_a_bag_clears_records_that_name_it(client, db_session, beach):
    day_id = beach["day"]["id"]
    _remove_bag(client, day_id, beach["suitcase"]["id"])
    assert client.delete(f"/api/packing-list-bags/{beach['suitcase']['id']}").status_code == 204
    reading = _reading(db_session, day_id, beach["toiletries"]["id"])
    assert (reading.parent_bag_id, reading.removed_from_bag_id) == (None, None)
