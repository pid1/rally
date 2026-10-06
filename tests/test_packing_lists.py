"""Packing Lists (issue #249): reusable lists, put on a day and checked off there.

The two rules the feature exists for are pinned hardest here: an edit to the
packing list reaches every day it is on, and a check on one day reaches nothing
else. A day stores its checks plus its own changes (``PackingListDayItem``):
items added to that day, and that day's edits or removals of a packing list item.
Those stay on the day, and are the one exception to the first rule — a
packing list edit does not reach an item a day has edited or removed. Most of
these tests are about the edges of that: deletes that must cascade by hand, a
group that must belong to its own packing list, counts that must not drift.
"""

from datetime import UTC, date, datetime

import pytest

from rally import packing_lists as logic
from rally.models import (
    PackingListBag,
    PackingListDayCheck,
    PackingListDayItem,
    PackingListItemHistory,
    PackingListTemplateItem,
)

# Thursday, 1 October 2026, mid-morning in Chicago.
NOW = datetime(2026, 10, 1, 15, 0, tzinfo=UTC)
TODAY = "2026-10-01"
SATURDAY = "2026-10-03"


@pytest.fixture(autouse=True)
def _frozen(frozen_now, local_timezone):
    local_timezone("America/Chicago")
    frozen_now(NOW)


def _packing_list(client, name="Swim at Nana's", **extra):
    response = client.post("/api/packing-list-templates", json={"name": name, **extra})
    assert response.status_code == 201, response.text
    return response.json()


def _bag(client, name):
    response = client.post("/api/packing-list-bags", json={"name": name})
    assert response.status_code == 201, response.text
    return response.json()


def _item(client, packing_list_template_id, name, **extra):
    response = client.post(
        f"/api/packing-list-templates/{packing_list_template_id}/items",
        json={"name": name, **extra},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _day(client, packing_list_template_id, day=SATURDAY, **extra):
    response = client.post(
        "/api/packing-list-days",
        json={"packing_list_template_id": packing_list_template_id, "date": day, **extra},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _check(client, day_id, item_id, checked=True):
    response = client.put(
        f"/api/packing-list-days/{day_id}/template-items/{item_id}", json={"checked": checked}
    )
    assert response.status_code == 200, response.text
    return response.json()


def _names(payload):
    return [i["name"] for i in payload["items"]]


# --- Packing Lists --------------------------------------------------------------------


def test_create_defaults_to_packing_the_day_of(client):
    created = _packing_list(client, description="  ")
    assert created["pack_days_before"] == 0
    assert created["description"] is None
    assert created["items"] == []


def test_names_are_trimmed_and_unique_ignoring_case(client):
    _packing_list(client, "  Beach day  ")
    assert client.get("/api/packing-list-templates").json()[0]["name"] == "Beach day"
    assert client.post("/api/packing-list-templates", json={"name": "BEACH DAY"}).status_code == 409
    assert client.post("/api/packing-list-templates", json={"name": "   "}).status_code == 422


def test_pack_days_before_is_a_whole_number_of_zero_or_more(client):
    for value in (-1, 1.5, "day_before"):
        response = client.post(
            "/api/packing-list-templates", json={"name": "X", "pack_days_before": value}
        )
        assert response.status_code == 422, value
    # No upper limit.
    assert _packing_list(client, "Trip", pack_days_before=30)["pack_days_before"] == 30


def test_list_is_by_name_with_counts(client):
    nana = _packing_list(client, "swim at Nana's")
    _packing_list(client, "Beach day")
    _item(client, nana["id"], "Goggles", bag="Pool bag")
    _item(client, nana["id"], "Sunscreen")
    _day(client, nana["id"])

    rows = client.get("/api/packing-list-templates").json()
    assert [r["name"] for r in rows] == ["Beach day", "swim at Nana's"]
    assert (rows[1]["item_count"], rows[1]["upcoming_days"]) == (2, 1)
    assert (rows[0]["item_count"], rows[0]["day_count"]) == (0, 0)


def test_update_renames_and_changes_timing(client):
    created = _packing_list(client)
    _packing_list(client, "Beach day")
    response = client.put(
        f"/api/packing-list-templates/{created['id']}",
        json={"name": "Nana's", "pack_days_before": 1, "description": "Saturdays"},
    )
    assert response.status_code == 200
    assert response.json()["pack_days_before"] == 1
    assert (
        client.put(
            f"/api/packing-list-templates/{created['id']}", json={"name": "beach DAY"}
        ).status_code
        == 409
    )
    # Omitting description leaves it; null clears it.
    assert (
        client.put(f"/api/packing-list-templates/{created['id']}", json={}).json()["description"]
        == "Saturdays"
    )
    cleared = client.put(f"/api/packing-list-templates/{created['id']}", json={"description": None})
    assert cleared.json()["description"] is None


def test_deleting_a_template_keeps_its_days_without_it(client, db_session):
    packing_list = _packing_list(client)
    item = _item(client, packing_list["id"], "Goggles", bag="Pool bag")
    day = _day(client, packing_list["id"])
    _check(client, day["id"], item["id"])
    survivor = _packing_list(client, "Beach day")
    _item(client, survivor["id"], "Towels")

    assert client.delete(f"/api/packing-list-templates/{packing_list['id']}").status_code == 204

    assert client.get(f"/api/packing-list-templates/{packing_list['id']}").status_code == 404
    # The day stays, on its own, reading exactly as it did.
    kept = client.get(f"/api/packing-list-days/{day['id']}").json()
    assert (kept["packing_list_template_id"], kept["name"]) == (None, "Swim at Nana's")
    assert [(i["name"], i["source"], i["checked"]) for i in kept["items"]] == [
        ("Goggles", "day", True)
    ]
    assert db_session.query(PackingListDayCheck).count() == 0
    assert [i.name for i in db_session.query(PackingListTemplateItem)] == ["Towels"]
    # Bags and item history are the household's, and stay.
    assert [b.name for b in db_session.query(PackingListBag)] == ["Pool bag"]
    assert db_session.query(PackingListItemHistory).count() == 2


def test_unknown_packing_list_is_404(client):
    assert client.get("/api/packing-list-templates/99").status_code == 404
    assert client.put("/api/packing-list-templates/99", json={"name": "X"}).status_code == 404
    assert client.delete("/api/packing-list-templates/99").status_code == 404


# --- Items: order, owner and bag ------------------------------------------------


def test_items_keep_one_order_and_new_ones_go_last(client, make_member):
    emma = make_member(name="Emma")
    packing_list = _packing_list(client)
    _item(client, packing_list["id"], "Goggles", owner_id=emma.id)
    _item(client, packing_list["id"], "Sunscreen")
    _item(client, packing_list["id"], "Towel", owner_id=emma.id, bag="Pool bag")

    body = client.get(f"/api/packing-list-templates/{packing_list['id']}").json()
    assert _names(body) == ["Goggles", "Sunscreen", "Towel"]
    assert [i["owner_id"] for i in body["items"]] == [emma.id, None, emma.id]
    assert "groups" not in body


def test_a_bag_named_on_an_item_is_found_or_made_ignoring_case(client):
    packing_list = _packing_list(client)
    first = _item(client, packing_list["id"], "Towel", bag="Pool bag")
    second = _item(client, packing_list["id"], "Goggles", bag="  POOL BAG ")
    third = _item(client, packing_list["id"], "Snacks", bag="Car")

    assert first["bag_id"] == second["bag_id"] != third["bag_id"]
    assert [b["name"] for b in client.get("/api/packing-list-bags").json()] == ["Car", "Pool bag"]
    assert _item(client, packing_list["id"], "Hat", bag="   ")["bag_id"] is None


def test_a_bag_can_be_named_by_id_but_not_both_ways(client):
    packing_list = _packing_list(client)
    pool = _bag(client, "Pool bag")
    assert _item(client, packing_list["id"], "Towel", bag_id=pool["id"])["bag_id"] == pool["id"]
    for payload in (
        {"name": "X", "bag_id": 999},
        {"name": "X", "bag": "Car", "bag_id": pool["id"]},
        {"name": "X", "owner_id": 999},
    ):
        response = client.post(
            f"/api/packing-list-templates/{packing_list['id']}/items", json=payload
        )
        assert response.status_code == 422, payload


def test_note_owner_and_bag_are_left_alone_unless_sent(client, make_member):
    emma = make_member(name="Emma")
    packing_list = _packing_list(client)
    item = _item(
        client, packing_list["id"], "Goggles", note="Blue", owner_id=emma.id, bag="Pool bag"
    )
    url = f"/api/packing-list-templates/{packing_list['id']}/items/{item['id']}"

    renamed = client.put(url, json={"name": "Swim goggles"}).json()
    assert (renamed["note"], renamed["owner_id"], renamed["bag_id"]) == (
        "Blue",
        emma.id,
        item["bag_id"],
    )
    cleared = client.put(url, json={"note": None, "owner_id": None, "bag": None}).json()
    assert (cleared["note"], cleared["owner_id"], cleared["bag_id"]) == (None, None, None)
    # An edit keeps the item's place.
    assert cleared["sort_order"] == item["sort_order"]


def test_reorder_in_owner_view_reassigns_and_keeps_other_items_in_place(client, make_member):
    emma = make_member(name="Emma")
    jake = make_member(name="Jake")
    packing_list = _packing_list(client)
    a = _item(client, packing_list["id"], "A", owner_id=emma.id)
    b = _item(client, packing_list["id"], "B", owner_id=jake.id)
    c = _item(client, packing_list["id"], "C", owner_id=emma.id)
    d = _item(client, packing_list["id"], "D", owner_id=jake.id)

    # Drop D at the top of Emma's group.
    response = client.post(
        f"/api/packing-list-templates/{packing_list['id']}/items/reorder",
        json={"view": "owner", "key": emma.id, "item_ids": [d["id"], a["id"], c["id"]]},
    )
    assert response.status_code == 200
    body = client.get(f"/api/packing-list-templates/{packing_list['id']}").json()
    assert _names(body) == ["D", "B", "A", "C"]
    assert [i["owner_id"] for i in body["items"]] == [emma.id, jake.id, emma.id, emma.id]
    # Jake's group read B, D before; it reads B now — B kept its place.
    assert body["items"][1]["id"] == b["id"]


def test_reorder_in_bag_view_sets_the_bag_and_none_is_no_bag(client):
    packing_list = _packing_list(client)
    towel = _item(client, packing_list["id"], "Towel", bag="Pool bag")
    snacks = _item(client, packing_list["id"], "Snacks")
    pool = towel["bag_id"]
    url = f"/api/packing-list-templates/{packing_list['id']}/items/reorder"

    client.post(url, json={"view": "bag", "key": pool, "item_ids": [snacks["id"], towel["id"]]})
    body = client.get(f"/api/packing-list-templates/{packing_list['id']}").json()
    assert [(i["name"], i["bag_id"]) for i in body["items"]] == [("Snacks", pool), ("Towel", pool)]

    client.post(url, json={"view": "bag", "key": None, "item_ids": [towel["id"]]})
    towel_now = client.get(f"/api/packing-list-templates/{packing_list['id']}").json()["items"][1]
    assert towel_now["bag_id"] is None


def test_reorder_is_all_or_nothing(client):
    nana = _packing_list(client)
    beach = _packing_list(client, "Beach day")
    towel = _item(client, nana["id"], "Towel")
    bucket = _item(client, beach["id"], "Bucket")
    url = f"/api/packing-list-templates/{nana['id']}/items/reorder"
    response = client.post(
        url, json={"view": "bag", "key": None, "item_ids": [towel["id"], bucket["id"]]}
    )
    assert response.status_code == 404
    assert client.post(url, json={"view": "owner", "key": 999, "item_ids": []}).status_code == 422
    assert client.post(url, json={"view": "bag", "key": 999, "item_ids": []}).status_code == 422
    assert client.post(url, json={"view": "group", "item_ids": []}).status_code == 422


# --- Bags ----------------------------------------------------------------------------


def test_bags_are_a_household_list_a_to_z_with_counts(client):
    nana = _packing_list(client)
    beach = _packing_list(client, "Beach day")
    _item(client, nana["id"], "Towel", bag="Pool bag")
    _item(client, beach["id"], "Bucket", bag="Beach tote")
    _item(client, beach["id"], "Towels", bag="pool bag")

    bags = client.get("/api/packing-list-bags").json()
    assert [(b["name"], b["item_count"]) for b in bags] == [("Beach tote", 1), ("Pool bag", 2)]
    assert client.post("/api/packing-list-bags", json={"name": "BEACH TOTE"}).status_code == 409


def test_renaming_a_bag_renames_it_everywhere(client):
    packing_list = _packing_list(client)
    item = _item(client, packing_list["id"], "Towel", bag="Pool bag")
    other = _bag(client, "Car")
    url = f"/api/packing-list-bags/{item['bag_id']}"
    assert client.put(url, json={"name": "car"}).status_code == 409
    assert client.put(url, json={"name": "Swim bag"}).json()["name"] == "Swim bag"
    assert (
        client.put(f"/api/packing-list-bags/{other['id']}", json={"name": "Car"}).status_code == 200
    )


def test_deleting_a_bag_moves_what_was_in_it_to_no_bag(client, db_session):
    packing_list = _packing_list(client)
    towel = _item(client, packing_list["id"], "Towel", bag="Pool bag")
    day = _day(client, packing_list["id"])
    client.post(
        f"/api/packing-list-days/{day['id']}/day-items", json={"name": "Hat", "bag": "Pool bag"}
    )

    assert client.delete(f"/api/packing-list-bags/{towel['bag_id']}").status_code == 204

    assert (
        client.get(f"/api/packing-list-templates/{packing_list['id']}").json()["items"][0]["bag_id"]
        is None
    )
    assert {
        i["bag_id"] for i in client.get(f"/api/packing-list-days/{day['id']}").json()["items"]
    } == {None}
    assert db_session.query(PackingListItemHistory).filter_by(bag_id=towel["bag_id"]).count() == 0
    assert client.delete(f"/api/packing-list-bags/{towel['bag_id']}").status_code == 404


def test_deleting_a_family_member_makes_their_items_everyones(client, make_member):
    emma = make_member(name="Emma")
    packing_list = _packing_list(client)
    _item(client, packing_list["id"], "Goggles", owner_id=emma.id)

    assert client.delete(f"/api/family/{emma.id}").status_code in (200, 204)

    assert (
        client.get(f"/api/packing-list-templates/{packing_list['id']}").json()["items"][0][
            "owner_id"
        ]
        is None
    )


# --- Item history and autocomplete --------------------------------------------------


def test_every_typed_name_is_remembered_once_and_starts_uncounted(client, db_session, make_member):
    emma = make_member(name="Emma")
    nana = _packing_list(client)
    beach = _packing_list(client, "Beach day")
    _item(client, nana["id"], "Towel", owner_id=emma.id, bag="Pool bag")
    _item(client, beach["id"], "  towel ", bag="Beach tote")
    day = _day(client, beach["id"])
    client.post(f"/api/packing-list-days/{day['id']}/day-items", json={"name": "Sun hat"})

    rows = {r.name_key: r for r in db_session.query(PackingListItemHistory)}
    assert set(rows) == {"towel", "sun hat"}
    # The newest casing, owner and bag. Typing a name is not packing it, so
    # its count waits for a day it is on to be over (count_packed_days).
    assert (rows["towel"].name, rows["towel"].times_added, rows["towel"].owner_id) == (
        "towel",
        0,
        None,
    )


def test_renaming_an_item_remembers_the_new_name(client, db_session):
    packing_list = _packing_list(client)
    item = _item(client, packing_list["id"], "Towle")
    url = f"/api/packing-list-templates/{packing_list['id']}/items/{item['id']}"
    client.put(url, json={"name": "Towel"})
    # A change of case or spacing is the same name, so it adds nothing.
    client.put(url, json={"name": " TOWEL "})

    rows = db_session.query(PackingListItemHistory).order_by(PackingListItemHistory.id).all()
    # The old name stays; renaming counts nothing.
    assert [(r.name, r.times_added) for r in rows] == [("Towle", 0), ("Towel", 0)]


def test_a_rename_on_one_day_is_remembered_too(client, db_session):
    packing_list = _packing_list(client)
    item = _item(client, packing_list["id"], "Goggles")
    day = _day(client, packing_list["id"])
    base = f"/api/packing-list-days/{day['id']}"
    client.put(f"{base}/template-items/{item['id']}", json={"name": "Blue goggles"})
    own = client.post(f"{base}/day-items", json={"name": "Sun hat"}).json()["items"][-1]
    client.put(f"{base}/day-items/{own['id']}", json={"name": "Visor"})

    assert {r.name for r in db_session.query(PackingListItemHistory)} == {
        "Goggles",
        "Blue goggles",
        "Sun hat",
        "Visor",
    }


def test_suggestions_rank_prefix_matches_then_use(client):
    packing_list = _packing_list(client)
    for name in ("Beach towel", "Towel", "Towel", "Tote bag"):
        _item(client, packing_list["id"], name, bag="Pool bag" if name == "Towel" else None)

    found = client.get("/api/packing-list-items/suggestions?q=tow").json()
    assert [s["name"] for s in found] == ["Towel", "Beach towel"]
    assert found[0]["bag_name"] == "Pool bag"
    assert client.get("/api/packing-list-items/suggestions?q=100%").json() == []
    assert len(client.get("/api/packing-list-items/suggestions?limit=99").json()) == 3


def test_forgetting_a_suggestion_leaves_packing_lists_alone(client):
    packing_list = _packing_list(client)
    _item(client, packing_list["id"], "Mikl")
    suggestion = client.get("/api/packing-list-items/suggestions?q=mik").json()[0]

    url = f"/api/packing-list-items/suggestions/{suggestion['id']}"
    assert client.delete(url).status_code == 204
    assert client.get("/api/packing-list-items/suggestions?q=mik").json() == []
    assert _names(client.get(f"/api/packing-list-templates/{packing_list['id']}").json()) == [
        "Mikl"
    ]
    assert client.delete(url).status_code == 404


# --- Days ----------------------------------------------------------------------------


def test_adding_to_a_day_starts_unchecked(client):
    packing_list = _packing_list(client, pack_days_before=1)
    _item(client, packing_list["id"], "Goggles")
    day = _day(client, packing_list["id"], label="  Swim  ")

    assert day["date"] == SATURDAY
    assert day["label"] == "Swim"
    assert day["pack_date"] == "2026-10-02"
    assert (day["total"], day["checked"]) == (1, 0)
    assert day["items"][0]["checked"] is False


def test_a_day_is_today_or_later(client):
    packing_list = _packing_list(client)
    assert _day(client, packing_list["id"], TODAY)["date"] == TODAY
    response = client.post(
        "/api/packing-list-days",
        json={"packing_list_template_id": packing_list["id"], "date": "2026-09-30"},
    )
    assert response.status_code == 422


def test_once_per_day_and_the_clash_names_the_existing_day(client):
    packing_list = _packing_list(client)
    day = _day(client, packing_list["id"])
    response = client.post(
        "/api/packing-list-days",
        json={"packing_list_template_id": packing_list["id"], "date": SATURDAY},
    )
    assert response.status_code == 409
    assert response.json()["detail"]["id"] == day["id"]
    # Another packing list on the same day is fine.
    _day(client, _packing_list(client, "Beach day")["id"])


def test_unknown_packing_list_or_bad_date_is_422(client):
    assert (
        client.post(
            "/api/packing-list-days", json={"packing_list_template_id": 9, "date": SATURDAY}
        ).status_code
        == 422
    )
    packing_list = _packing_list(client)
    response = client.post(
        "/api/packing-list-days",
        json={"packing_list_template_id": packing_list["id"], "date": "10/3/2026"},
    )
    assert response.status_code == 422


def test_moving_a_day_keeps_its_checks_and_honors_the_clash_rule(client):
    packing_list = _packing_list(client)
    item = _item(client, packing_list["id"], "Goggles")
    first = _day(client, packing_list["id"])
    _day(client, packing_list["id"], "2026-10-10")
    _check(client, first["id"], item["id"])

    moved = client.put(f"/api/packing-list-days/{first['id']}", json={"date": "2026-10-04"})
    assert moved.status_code == 200
    assert moved.json()["checked"] == 1
    clash = client.put(f"/api/packing-list-days/{first['id']}", json={"date": "2026-10-10"})
    assert clash.status_code == 409
    past = client.put(f"/api/packing-list-days/{first['id']}", json={"date": "2026-09-01"})
    assert past.status_code == 422


def test_upcoming_is_soonest_first_with_items(client, db_session):
    from rally.models import PackingListDay

    nana = _packing_list(client)
    _item(client, nana["id"], "Goggles")
    beach = _packing_list(client, "Beach day")
    _day(client, beach["id"], "2026-10-10")
    _day(client, nana["id"], SATURDAY)
    _day(client, nana["id"], TODAY)
    db_session.add(PackingListDay(packing_list_template_id=nana["id"], date="2026-09-30"))
    db_session.commit()

    upcoming = client.get("/api/packing-list-days").json()
    assert [(d["date"], d["name"]) for d in upcoming] == [
        (TODAY, "Swim at Nana's"),
        (SATURDAY, "Swim at Nana's"),
        ("2026-10-10", "Beach day"),
    ]
    # The card expands in place, so the items travel with the listing.
    assert _names(upcoming[0]) == ["Goggles"]
    assert upcoming[2]["items"] == []


def test_previous_is_newest_first_searchable_and_paged(client, db_session):
    from rally.models import PackingListDay

    nana = _packing_list(client)
    beach = _packing_list(client, "Beach day")
    _day(client, nana["id"], TODAY)
    for offset in range(12):
        db_session.add(
            PackingListDay(packing_list_template_id=nana["id"], date=f"2026-09-{offset + 10:02d}")
        )
    db_session.add(
        PackingListDay(packing_list_template_id=beach["id"], date="2026-08-01", label="Galveston")
    )
    db_session.commit()

    page = client.get("/api/packing-list-days/previous?limit=5").json()
    assert page["total"] == 13
    assert page["has_more"] is True
    assert [d["date"] for d in page["items"]][:2] == ["2026-09-21", "2026-09-20"]
    # Today is current, never archived: the two lists partition every day.
    assert TODAY not in [d["date"] for d in page["items"]]

    last = client.get("/api/packing-list-days/previous?limit=5&offset=10").json()
    assert len(last["items"]) == 3
    assert last["has_more"] is False

    by_label = client.get("/api/packing-list-days/previous?search=galv").json()
    assert [d["name"] for d in by_label["items"]] == ["Beach day"]
    by_name = client.get("/api/packing-list-days/previous?search=BEACH").json()
    assert by_name["total"] == 1


def test_past_days_are_read_only(client, db_session):
    from rally.models import PackingListDay

    packing_list = _packing_list(client)
    item = _item(client, packing_list["id"], "Goggles")
    past = PackingListDay(packing_list_template_id=packing_list["id"], date="2026-09-30")
    db_session.add(past)
    db_session.commit()
    base = f"/api/packing-list-days/{past.id}"

    assert (
        client.put(f"{base}/template-items/{item['id']}", json={"checked": True}).status_code == 403
    )
    assert client.post(f"{base}/reset").status_code == 403
    assert client.put(base, json={"label": "Late"}).status_code == 403
    assert client.delete(base).status_code == 403
    # Reading it is the point of the archive.
    assert client.get(base).status_code == 200


def test_removing_a_day_leaves_the_packing_list(client, db_session):
    packing_list = _packing_list(client)
    item = _item(client, packing_list["id"], "Goggles")
    day = _day(client, packing_list["id"])
    _check(client, day["id"], item["id"])

    assert client.delete(f"/api/packing-list-days/{day['id']}").status_code == 204

    assert db_session.query(PackingListDayCheck).count() == 0
    assert _names(client.get(f"/api/packing-list-templates/{packing_list['id']}").json()) == [
        "Goggles"
    ]


# --- The two rules -------------------------------------------------------------------


def test_checking_off_on_a_day_touches_neither_the_packing_list_nor_another_day(client):
    packing_list = _packing_list(client)
    goggles = _item(client, packing_list["id"], "Goggles")
    _item(client, packing_list["id"], "Towel")
    saturday = _day(client, packing_list["id"])
    next_saturday = _day(client, packing_list["id"], "2026-10-10")

    after = _check(client, saturday["id"], goggles["id"])

    assert (after["checked"], after["total"]) == (1, 2)
    assert [i["checked"] for i in after["items"]] == [True, False]
    other = client.get(f"/api/packing-list-days/{next_saturday['id']}").json()
    assert other["checked"] == 0
    master = client.get(f"/api/packing-list-templates/{packing_list['id']}").json()
    assert all("checked" not in i for i in master["items"])


def test_checking_is_idempotent_and_unchecking_removes_the_check(client):
    packing_list = _packing_list(client)
    item = _item(client, packing_list["id"], "Goggles")
    day = _day(client, packing_list["id"])

    _check(client, day["id"], item["id"])
    assert _check(client, day["id"], item["id"])["checked"] == 1
    assert _check(client, day["id"], item["id"], checked=False)["checked"] == 0
    assert _check(client, day["id"], item["id"], checked=False)["checked"] == 0


def test_an_item_from_another_packing_list_cannot_be_checked(client):
    nana = _packing_list(client)
    beach = _packing_list(client, "Beach day")
    bucket = _item(client, beach["id"], "Bucket")
    day = _day(client, nana["id"])
    response = client.put(
        f"/api/packing-list-days/{day['id']}/template-items/{bucket['id']}", json={"checked": True}
    )
    assert response.status_code == 404


def test_edits_to_the_packing_list_reach_every_day_it_is_on(client, db_session, make_member):
    emma = make_member(name="Emma")
    packing_list = _packing_list(client)
    goggles = _item(client, packing_list["id"], "Goggles")
    towel = _item(client, packing_list["id"], "Towel")
    saturday = _day(client, packing_list["id"])
    later = _day(client, packing_list["id"], "2026-10-10")
    _check(client, saturday["id"], goggles["id"])
    _check(client, saturday["id"], towel["id"])

    base = f"/api/packing-list-templates/{packing_list['id']}/items"
    client.put(
        f"{base}/{goggles['id']}",
        json={"name": "Swim goggles", "owner_id": emma.id, "bag": "Pool bag"},
    )
    client.delete(f"{base}/{towel['id']}")
    _item(client, packing_list["id"], "Wet bag")

    for day_id, checked in ((saturday["id"], [True, False]), (later["id"], [False, False])):
        day = client.get(f"/api/packing-list-days/{day_id}").json()
        assert _names(day) == ["Swim goggles", "Wet bag"]
        assert [i["owner_id"] for i in day["items"]] == [emma.id, None]
        assert day["items"][0]["bag_id"] is not None
        assert [i["checked"] for i in day["items"]] == checked
        assert day["total"] == 2
    assert client.get(f"/api/packing-list-days/{saturday['id']}").json()["checked"] == 1
    # Deleting an item deletes its checks too, rather than leaving them for the
    # progress count's join to hide.
    assert (
        db_session.query(PackingListDayCheck).filter_by(template_item_id=towel["id"]).count() == 0
    )


def test_uncheck_all_resets_one_day_only(client):
    packing_list = _packing_list(client)
    item = _item(client, packing_list["id"], "Goggles")
    saturday = _day(client, packing_list["id"])
    later = _day(client, packing_list["id"], "2026-10-10")
    _check(client, saturday["id"], item["id"])
    _check(client, later["id"], item["id"])

    reset = client.post(f"/api/packing-list-days/{saturday['id']}/reset")

    assert reset.json()["checked"] == 0
    assert client.get(f"/api/packing-list-days/{later['id']}").json()["checked"] == 1


def test_check_all_checks_one_day_as_it_reads(client):
    packing_list = _packing_list(client)
    goggles = _item(client, packing_list["id"], "Goggles")
    towel = _item(client, packing_list["id"], "Towel")
    saturday = _day(client, packing_list["id"])
    later = _day(client, packing_list["id"], "2026-10-10")
    base = f"/api/packing-list-days/{saturday['id']}"
    client.post(f"{base}/day-items", json={"name": "Sun hat"})
    client.delete(f"{base}/template-items/{towel['id']}")
    _check(client, saturday["id"], goggles["id"])

    done = client.post(f"{base}/check-all").json()
    assert (done["checked"], done["total"]) == (2, 2)
    assert all(i["checked"] for i in done["items"])
    # Idempotent, and the removed item and the other day are untouched.
    assert client.post(f"{base}/check-all").json()["checked"] == 2
    assert client.get(f"/api/packing-list-days/{later['id']}").json()["checked"] == 0


def test_check_all_checks_the_packing_lists_own_items(client):
    """The main case: template items nobody has ticked yet, checked in one go."""
    packing_list = _packing_list(client)
    for name in ("Goggles", "Towel", "Sunscreen"):
        _item(client, packing_list["id"], name)
    saturday = _day(client, packing_list["id"])
    later = _day(client, packing_list["id"], "2026-10-10")

    done = client.post(f"/api/packing-list-days/{saturday['id']}/check-all").json()

    assert (done["checked"], done["total"]) == (3, 3)
    assert all(i["checked"] for i in done["items"])
    assert client.get(f"/api/packing-list-days/{later['id']}").json()["checked"] == 0
    assert all(
        "checked" not in i
        for i in client.get(f"/api/packing-list-templates/{packing_list['id']}").json()["items"]
    )


def test_check_all_is_refused_on_a_past_day(client, db_session):
    from rally.models import PackingListDay

    packing_list = _packing_list(client)
    past = PackingListDay(packing_list_template_id=packing_list["id"], date="2026-09-30")
    db_session.add(past)
    db_session.commit()
    assert client.post(f"/api/packing-list-days/{past.id}/check-all").status_code == 403


def test_a_stray_check_never_inflates_progress(client, db_session):
    packing_list = _packing_list(client)
    _item(client, packing_list["id"], "Goggles")
    day = _day(client, packing_list["id"])
    db_session.add(PackingListDayCheck(day_id=day["id"], template_item_id=999))
    db_session.commit()

    assert client.get(f"/api/packing-list-days/{day['id']}").json()["checked"] == 0


# --- A day's own changes -------------------------------------------------------------


def _day_names(client, day_id):
    return _names(client.get(f"/api/packing-list-days/{day_id}").json())


def test_an_edit_on_a_day_stays_on_that_day(client):
    packing_list = _packing_list(client)
    folder = _item(client, packing_list["id"], "Homework folder", note="Blue")
    thursday = _day(client, packing_list["id"], TODAY)
    friday = _day(client, packing_list["id"], "2026-10-02")

    edited = client.put(
        f"/api/packing-list-days/{thursday['id']}/template-items/{folder['id']}",
        json={"name": "Homework folder and permission slip", "note": None},
    ).json()

    item = edited["items"][0]
    assert (item["name"], item["note"], item["source"], item["changed"]) == (
        "Homework folder and permission slip",
        None,
        "template",
        True,
    )
    assert _day_names(client, friday["id"]) == ["Homework folder"]
    master = client.get(f"/api/packing-list-templates/{packing_list['id']}").json()
    assert (master["items"][0]["name"], master["items"][0]["note"]) == ("Homework folder", "Blue")


def test_a_packing_list_edit_does_not_reach_an_item_a_day_changed(client):
    packing_list = _packing_list(client)
    folder = _item(client, packing_list["id"], "Homework folder")
    changed = _day(client, packing_list["id"], TODAY)
    untouched = _day(client, packing_list["id"], "2026-10-02")
    client.put(
        f"/api/packing-list-days/{changed['id']}/template-items/{folder['id']}",
        json={"name": "Red folder"},
    )

    client.put(
        f"/api/packing-list-templates/{packing_list['id']}/items/{folder['id']}",
        json={"name": "Folder"},
    )
    _item(client, packing_list["id"], "Lunchbox")

    # The new item reaches both days; the rename reaches only the day that
    # had not changed the folder itself.
    assert _day_names(client, changed["id"]) == ["Red folder", "Lunchbox"]
    assert _day_names(client, untouched["id"]) == ["Folder", "Lunchbox"]


def test_editing_keeps_the_check_and_checking_keeps_the_edit(client):
    packing_list = _packing_list(client)
    item = _item(client, packing_list["id"], "Goggles")
    day = _day(client, packing_list["id"])
    url = f"/api/packing-list-days/{day['id']}/template-items/{item['id']}"

    client.put(url, json={"checked": True})
    client.put(url, json={"name": "Blue goggles"})
    after = client.put(url, json={"checked": False}).json()["items"][0]
    assert (after["name"], after["checked"]) == ("Blue goggles", False)
    assert client.put(url, json={"checked": True}).json()["items"][0]["checked"] is True


def test_a_day_can_change_an_items_owner_and_bag_for_itself(client, make_member):
    emma = make_member(name="Emma")
    jake = make_member(name="Jake")
    packing_list = _packing_list(client)
    hair = _item(client, packing_list["id"], "Hair ties", owner_id=emma.id)
    _item(client, packing_list["id"], "Swimsuit", owner_id=jake.id)
    saturday = _day(client, packing_list["id"])
    later = _day(client, packing_list["id"], "2026-10-10")

    moved = client.put(
        f"/api/packing-list-days/{saturday['id']}/template-items/{hair['id']}",
        json={"owner_id": jake.id, "bag": "Pool bag"},
    ).json()

    item = moved["items"][0]
    # It keeps its place in the order; only who and where changed.
    assert (item["name"], item["owner_id"], item["changed"]) == ("Hair ties", jake.id, True)
    assert item["bag_id"] is not None
    other = client.get(f"/api/packing-list-days/{later['id']}").json()["items"][0]
    assert (other["owner_id"], other["bag_id"]) == (emma.id, None)
    # The day keeps its owner when the template later changes the item's.
    client.put(
        f"/api/packing-list-templates/{packing_list['id']}/items/{hair['id']}",
        json={"owner_id": None},
    )
    day = client.get(f"/api/packing-list-days/{saturday['id']}").json()
    assert next(i for i in day["items"] if i["name"] == "Hair ties")["owner_id"] == jake.id


def test_a_day_edit_copies_the_items_owner_and_bag_so_later_changes_do_not_reach_it(
    client, make_member
):
    emma = make_member(name="Emma")
    packing_list = _packing_list(client)
    hair = _item(client, packing_list["id"], "Hair ties", owner_id=emma.id, bag="Pool bag")
    day = _day(client, packing_list["id"])
    client.put(
        f"/api/packing-list-days/{day['id']}/template-items/{hair['id']}",
        json={"name": "Hair clips"},
    )

    client.put(
        f"/api/packing-list-templates/{packing_list['id']}/items/{hair['id']}",
        json={"owner_id": None, "bag": None},
    )

    item = client.get(f"/api/packing-list-days/{day['id']}").json()["items"][0]
    assert (item["name"], item["owner_id"], item["bag_id"]) == (
        "Hair clips",
        emma.id,
        hair["bag_id"],
    )


def test_a_days_owner_must_be_a_family_member(client):
    packing_list = _packing_list(client)
    item = _item(client, packing_list["id"], "Goggles")
    day = _day(client, packing_list["id"])
    response = client.put(
        f"/api/packing-list-days/{day['id']}/template-items/{item['id']}", json={"owner_id": 999}
    )
    assert response.status_code == 422


def test_an_item_added_to_a_day_comes_after_the_templates_on_that_day_only(client, make_member):
    emma = make_member(name="Emma")
    packing_list = _packing_list(client)
    _item(client, packing_list["id"], "Swimsuit", owner_id=emma.id)
    _item(client, packing_list["id"], "Snacks")
    saturday = _day(client, packing_list["id"])
    later = _day(client, packing_list["id"], "2026-10-10")
    base = f"/api/packing-list-days/{saturday['id']}/day-items"

    client.post(base, json={"name": "Cousin's floatie", "owner_id": emma.id, "bag": "Pool bag"})
    added = client.post(base, json={"name": "Sun hat", "note": "The wide one"})
    assert added.status_code == 201

    day = added.json()
    assert _names(day) == ["Swimsuit", "Snacks", "Cousin's floatie", "Sun hat"]
    assert [i["source"] for i in day["items"]] == ["template", "template", "day", "day"]
    assert day["items"][2]["owner_id"] == emma.id and day["items"][2]["bag_id"] is not None
    assert day["total"] == 4
    assert _day_names(client, later["id"]) == ["Swimsuit", "Snacks"]
    assert _names(client.get(f"/api/packing-list-templates/{packing_list['id']}").json()) == [
        "Swimsuit",
        "Snacks",
    ]


def test_a_days_own_item_is_checked_edited_and_deleted_on_its_own(client):
    packing_list = _packing_list(client)
    _item(client, packing_list["id"], "Goggles")
    day = _day(client, packing_list["id"])
    own = client.post(
        f"/api/packing-list-days/{day['id']}/day-items", json={"name": "Field trip form"}
    ).json()["items"][1]
    url = f"/api/packing-list-days/{day['id']}/day-items/{own['id']}"

    checked = client.put(url, json={"checked": True}).json()
    assert (checked["checked"], checked["total"]) == (1, 2)
    renamed = client.put(url, json={"name": "Signed field trip form", "note": "In the folder"})
    assert renamed.json()["items"][1]["name"] == "Signed field trip form"
    assert renamed.json()["items"][1]["checked"] is True

    reset = client.post(f"/api/packing-list-days/{day['id']}/reset").json()
    assert reset["checked"] == 0

    deleted = client.delete(url)
    assert deleted.status_code == 200
    assert _names(deleted.json()) == ["Goggles"]
    assert client.delete(url).status_code == 404


def test_removing_a_packing_list_item_from_a_day_leaves_the_packing_list_and_other_days(client):
    packing_list = _packing_list(client)
    shoes = _item(client, packing_list["id"], "PE shoes")
    _item(client, packing_list["id"], "Lunchbox")
    wednesday = _day(client, packing_list["id"], TODAY)
    thursday = _day(client, packing_list["id"], "2026-10-02")
    _check(client, wednesday["id"], shoes["id"])

    removed = client.delete(
        f"/api/packing-list-days/{wednesday['id']}/template-items/{shoes['id']}"
    ).json()

    assert _names(removed) == ["Lunchbox"]
    assert (removed["checked"], removed["total"]) == (0, 1)
    assert _day_names(client, thursday["id"]) == ["PE shoes", "Lunchbox"]
    # Packing list edits to it no longer reach the day that removed it.
    client.put(
        f"/api/packing-list-templates/{packing_list['id']}/items/{shoes['id']}",
        json={"name": "Sneakers"},
    )
    assert _day_names(client, wednesday["id"]) == ["Lunchbox"]


def test_a_day_counts_the_items_it_changed_added_or_removed(client):
    packing_list = _packing_list(client)
    goggles = _item(client, packing_list["id"], "Goggles")
    towel = _item(client, packing_list["id"], "Towel")
    _item(client, packing_list["id"], "Snacks")
    day = _day(client, packing_list["id"])
    other = _day(client, packing_list["id"], "2026-10-10")
    base = f"/api/packing-list-days/{day['id']}"
    assert day["changed_count"] == 0

    client.put(f"{base}/template-items/{goggles['id']}", json={"name": "Blue goggles"})
    client.post(f"{base}/day-items", json={"name": "Sun hat"})
    removed = client.delete(f"{base}/template-items/{towel['id']}").json()
    assert removed["changed_count"] == 3

    # A check is not a change, and the other day is untouched.
    checked = client.put(f"{base}/template-items/{goggles['id']}", json={"checked": True}).json()
    assert checked["changed_count"] == 3
    assert client.get(f"/api/packing-list-days/{other['id']}").json()["changed_count"] == 0
    # Deleting an item from the template takes that day's edit of it along.
    client.delete(f"/api/packing-list-templates/{packing_list['id']}/items/{goggles['id']}")
    assert client.get(base).json()["changed_count"] == 2


def test_a_day_item_needs_a_real_owner_and_bag(client):
    packing_list = _packing_list(client)
    day = _day(client, packing_list["id"])
    base = f"/api/packing-list-days/{day['id']}/day-items"
    assert client.post(base, json={"name": "Ice", "owner_id": 999}).status_code == 422
    assert client.post(base, json={"name": "Ice", "bag_id": 999}).status_code == 422


def test_deleting_a_packing_list_item_drops_every_days_reading_of_it(client, db_session):
    packing_list = _packing_list(client)
    item = _item(client, packing_list["id"], "Goggles")
    day = _day(client, packing_list["id"])
    client.put(
        f"/api/packing-list-days/{day['id']}/template-items/{item['id']}",
        json={"name": "Blue goggles"},
    )

    client.delete(f"/api/packing-list-templates/{packing_list['id']}/items/{item['id']}")

    assert db_session.query(PackingListDayItem).count() == 0
    assert _day_names(client, day["id"]) == []


def test_a_days_own_item_can_change_owner_and_bag(client, make_member):
    emma = make_member(name="Emma")
    packing_list = _packing_list(client)
    day = _day(client, packing_list["id"])
    own = client.post(
        f"/api/packing-list-days/{day['id']}/day-items", json={"name": "Floatie"}
    ).json()["items"][0]

    edited = client.put(
        f"/api/packing-list-days/{day['id']}/day-items/{own['id']}",
        json={"owner_id": emma.id, "bag": "Pool bag"},
    ).json()["items"][0]
    assert edited["owner_id"] == emma.id and edited["bag_id"] is not None


def test_removing_a_day_takes_its_own_items_and_a_template_delete_keeps_them(client, db_session):
    packing_list = _packing_list(client)
    item = _item(client, packing_list["id"], "Goggles")
    saturday = _day(client, packing_list["id"])
    later = _day(client, packing_list["id"], "2026-10-10")
    for day in (saturday, later):
        client.post(f"/api/packing-list-days/{day['id']}/day-items", json={"name": "Sun hat"})
        client.delete(f"/api/packing-list-days/{day['id']}/template-items/{item['id']}")
    assert db_session.query(PackingListDayItem).count() == 4

    client.delete(f"/api/packing-list-days/{saturday['id']}")
    assert db_session.query(PackingListDayItem).count() == 2
    # Deleting the template keeps the later day as it reads: the sun hat, and
    # the goggles still off it.
    client.delete(f"/api/packing-list-templates/{packing_list['id']}")
    assert [i.name for i in db_session.query(PackingListDayItem)] == ["Sun hat"]


def test_a_past_days_items_are_read_only(client, db_session):
    from rally.models import PackingListDay

    packing_list = _packing_list(client)
    item = _item(client, packing_list["id"], "Goggles")
    past = PackingListDay(packing_list_template_id=packing_list["id"], date="2026-09-30")
    db_session.add(past)
    db_session.flush()
    own = PackingListDayItem(day_id=past.id, name="Sun hat")
    db_session.add(own)
    db_session.commit()
    base = f"/api/packing-list-days/{past.id}"

    assert client.put(f"{base}/template-items/{item['id']}", json={"name": "x"}).status_code == 403
    assert client.delete(f"{base}/template-items/{item['id']}").status_code == 403
    assert client.post(f"{base}/day-items", json={"name": "x"}).status_code == 403
    assert client.put(f"{base}/day-items/{own.id}", json={"checked": True}).status_code == 403
    assert client.delete(f"{base}/day-items/{own.id}").status_code == 403


def test_the_listing_carries_each_packing_list_whole(client):
    packing_list = _packing_list(client)
    _item(client, packing_list["id"], "Goggles", bag="Pool bag")

    row = client.get("/api/packing-list-templates").json()[0]
    assert _names(row) == ["Goggles"]
    assert row["items"][0]["bag_id"] is not None
    assert row["schedule"] is None


def test_pack_date():
    assert logic.pack_date(SATURDAY, 0) == SATURDAY
    assert logic.pack_date(SATURDAY, 1) == "2026-10-02"
    assert logic.pack_date(SATURDAY, 5) == "2026-09-28"
    assert logic.pack_date("2026-03-01", 1) == "2026-02-28"


# --- Editing one day: its date, label and lead time ----------------------------------


def test_a_day_takes_its_lead_time_from_the_template_until_it_sets_its_own(client):
    packing_list = _packing_list(client, pack_days_before=1)
    day = _day(client, packing_list["id"])
    assert (day["pack_days_before"], day["pack_date"]) == (1, "2026-10-02")

    edited = client.put(f"/api/packing-list-days/{day['id']}", json={"pack_days_before": 3}).json()
    assert (edited["pack_days_before"], edited["pack_date"]) == (3, "2026-09-30")

    # The template's own number no longer reaches a day that set one...
    client.put(f"/api/packing-list-templates/{packing_list['id']}", json={"pack_days_before": 2})
    assert client.get(f"/api/packing-list-days/{day['id']}").json()["pack_days_before"] == 3
    # ...until the day is told to follow it again.
    back = client.put(f"/api/packing-list-days/{day['id']}", json={"pack_days_before": None}).json()
    assert back["pack_days_before"] == 2

    assert (
        client.put(f"/api/packing-list-days/{day['id']}", json={"pack_days_before": -1}).status_code
        == 422
    )


def test_a_list_packed_days_ahead_stays_pack_today_until_its_day(client, db_session):
    packing_list = _packing_list(client, "Camp", pack_days_before=3)
    _item(client, packing_list["id"], "Sleeping bag")
    _day(client, packing_list["id"], "2026-10-05")

    def status(today):
        return logic.summary_text(db_session, today).splitlines()[0]

    assert "UPCOMING (in 5 days)" in status(date(2026, 9, 30))
    for today in (date(2026, 10, 2), date(2026, 10, 3), date(2026, 10, 4)):
        assert "— PACK TODAY." in status(today), today
    assert "— TODAY." in status(date(2026, 10, 5))
    assert "packed 3 days before (Friday, October 2)" in status(date(2026, 10, 2))


def test_a_day_carries_its_templates_description(client):
    packing_list = _packing_list(client, description="Saturdays at Nana's pool")
    day = _day(client, packing_list["id"])
    assert day["description"] == "Saturdays at Nana's pool"


def test_a_hand_edited_label_survives_a_schedule_relabel(client):
    packing_list = _packing_list(client)
    schedule = client.post(
        "/api/packing-list-template-schedules",
        json={
            "packing_list_template_id": packing_list["id"],
            "recurrence_type": "daily",
            "label": "Emma",
        },
    ).json()
    days = client.get("/api/packing-list-days").json()
    edited, other = days[0], days[1]
    client.put(f"/api/packing-list-days/{edited['id']}", json={"label": "Emma — field trip"})

    client.put(
        f"/api/packing-list-template-schedules/{schedule['id']}", json={"label": "Emma and Jake"}
    )

    labels = {d["id"]: d["label"] for d in client.get("/api/packing-list-days").json()}
    assert labels[edited["id"]] == "Emma — field trip"
    assert labels[other["id"]] == "Emma and Jake"


def _summary(db_session, today=date(2026, 10, 2)):
    return logic.summary_text(db_session, today)


def test_summary_is_empty_without_days(client, db_session):
    _item(client, _packing_list(client)["id"], "Goggles")
    assert _summary(db_session) == ""


def test_summary_lists_unchecked_items_by_owner_with_their_bag(client, db_session, make_member):
    emma = make_member(name="Emma")
    make_member(name="Jake")
    packing_list = _packing_list(client, pack_days_before=1)
    goggles = _item(client, packing_list["id"], "Goggles", owner_id=emma.id)
    _item(client, packing_list["id"], "Hair ties", owner_id=emma.id, bag="Pool bag")
    _item(client, packing_list["id"], "Sunscreen", note="bottle is nearly empty")
    day = _day(client, packing_list["id"], label="Swim")
    _check(client, day["id"], goggles["id"])

    text = _summary(db_session)

    # Jake owns nothing here, so has no heading; Everyone comes last.
    assert text.splitlines() == [
        '- "Swim at Nana\'s" (Swim) for Saturday, October 3, packed the day before '
        "(Friday, October 2) — PACK TODAY. 1 of 3 packed.",
        "  Emma:",
        "    - Hair ties (in Pool bag)",
        "  Everyone:",
        "    - Sunscreen — bottle is nearly empty",
    ]


def test_summary_statuses(client, db_session):
    today_list = _packing_list(client, "School backpack")
    _item(client, today_list["id"], "Lunch")
    _day(client, today_list["id"], "2026-10-02")
    upcoming = _packing_list(client, "Beach day")
    _item(client, upcoming["id"], "Towels")
    _day(client, upcoming["id"], "2026-10-07")

    lines = _summary(db_session).splitlines()

    assert lines[0].endswith("— TODAY. 0 of 1 packed.")
    assert lines[0].startswith('- "School backpack" for Friday, October 2')
    # Headed even when Everyone is the only group, as the page heads it.
    assert lines[1:3] == ["  Everyone:", "    - Lunch"]
    assert "UPCOMING (in 5 days)" in lines[3]


def test_summary_skips_packed_lists_and_days_outside_the_window(client, db_session):
    done = _packing_list(client, "Done")
    item = _item(client, done["id"], "Goggles")
    day = _day(client, done["id"])
    _check(client, day["id"], item["id"])
    far = _packing_list(client, "Far")
    _item(client, far["id"], "Passport")
    _day(client, far["id"], "2026-10-10")  # 8 days after Oct 2
    empty = _packing_list(client, "Empty")
    _day(client, empty["id"])

    assert _summary(db_session) == ""


def test_summary_marks_items_already_on_the_shopping_list(client, db_session, make_shopping_item):
    make_shopping_item("  sunscreen ", completed=False)
    make_shopping_item("Snacks", completed=True)
    packing_list = _packing_list(client)
    _item(client, packing_list["id"], "Sunscreen")
    _item(client, packing_list["id"], "Snacks")
    _day(client, packing_list["id"])

    text = _summary(db_session)

    assert "- Sunscreen (already on the shopping list)" in text
    assert "- Snacks\n" in text + "\n"


def test_summary_reads_a_day_with_its_own_changes(client, db_session):
    packing_list = _packing_list(client)
    shoes = _item(client, packing_list["id"], "PE shoes")
    folder = _item(client, packing_list["id"], "Homework folder")
    day = _day(client, packing_list["id"])
    base = f"/api/packing-list-days/{day['id']}"
    client.delete(f"{base}/template-items/{shoes['id']}")
    client.put(f"{base}/template-items/{folder['id']}", json={"name": "Red homework folder"})
    client.post(f"{base}/day-items", json={"name": "Field trip form"})

    text = _summary(db_session)
    assert "PE shoes" not in text
    assert "Red homework folder" in text
    assert "Field trip form" in text
    assert "0 of 2 packed" in text


def test_a_template_kept_in_sync_follows_its_lead_time_unless_given_one(client):
    template = _packing_list(client, pack_days_before=2)
    follows = _day(client, template["id"])
    own = _day(client, template["id"], "2026-10-10", pack_days_before=0)
    assert (follows["pack_days_before"], own["pack_days_before"]) == (2, 0)

    client.put(f"/api/packing-list-templates/{template['id']}", json={"pack_days_before": 3})

    assert client.get(f"/api/packing-list-days/{follows['id']}").json()["pack_days_before"] == 3
    assert client.get(f"/api/packing-list-days/{own['id']}").json()["pack_days_before"] == 0


# --- Starting a template from a copy of another (#260) -------------------------------


@pytest.fixture
def nana(client, make_member):
    """A template with three items, an owner and a bag, a description and a lead time."""
    emma = make_member(name="Emma")
    template = _packing_list(client, description="Saturdays at Nana's pool", pack_days_before=1)
    goggles = _item(client, template["id"], "Goggles", owner_id=emma.id, bag="Pool bag")
    towel = _item(client, template["id"], "Towel", note="The big one", owner_id=emma.id)
    snacks = _item(client, template["id"], "Snacks")
    return {
        "template": template,
        "emma": emma,
        "goggles": goggles,
        "towel": towel,
        "snacks": snacks,
    }


def _copy(client, source_id, name="Sleepover at Nana's", **extra):
    return client.post(
        "/api/packing-list-templates",
        json={"name": name, "copy_from_template_id": source_id, **extra},
    )


def _read_items(payload):
    return [(i["name"], i["note"], i["owner_id"], i["bag_id"]) for i in payload["items"]]


def test_a_copy_takes_the_items_whole_and_in_order(client, nana):
    response = _copy(client, nana["template"]["id"])

    assert response.status_code == 201, response.text
    copy = response.json()
    assert copy["id"] != nana["template"]["id"]
    assert copy["item_count"] == 3
    assert _read_items(copy) == _read_items(
        client.get(f"/api/packing-list-templates/{nana['template']['id']}").json()
    )
    assert [i["sort_order"] for i in copy["items"]] == [0, 1, 2]
    # New rows, not the source's.
    assert not {i["id"] for i in copy["items"]} & {
        i["id"]
        for i in client.get(f"/api/packing-list-templates/{nana['template']['id']}").json()["items"]
    }


def test_a_copy_takes_only_the_items(client, nana):
    template_id = nana["template"]["id"]
    client.post(
        "/api/packing-list-template-schedules",
        json={"packing_list_template_id": template_id, "recurrence_type": "daily"},
    )

    copy = _copy(client, template_id).json()

    # The body's own name, an empty description and the default lead time.
    assert (copy["name"], copy["description"], copy["pack_days_before"]) == (
        "Sleepover at Nana's",
        None,
        0,
    )
    assert copy["schedule"] is None
    assert copy["day_count"] == 0


def test_a_copy_uses_the_description_and_lead_time_it_is_sent(client, nana):
    copy = _copy(client, nana["template"]["id"], description="Overnight", pack_days_before=2).json()

    assert (copy["description"], copy["pack_days_before"]) == ("Overnight", 2)


def test_the_source_is_left_alone(client, nana):
    template_id = nana["template"]["id"]
    before = client.get(f"/api/packing-list-templates/{template_id}").json()

    _copy(client, template_id)

    assert client.get(f"/api/packing-list-templates/{template_id}").json() == before


def test_a_copy_has_no_link_back(client, nana):
    template_id = nana["template"]["id"]
    copy = _copy(client, template_id).json()

    client.post(f"/api/packing-list-templates/{template_id}/items", json={"name": "Floatie"})
    client.put(
        f"/api/packing-list-templates/{template_id}/items/{nana['towel']['id']}",
        json={"name": "Beach towel"},
    )
    client.put(
        f"/api/packing-list-templates/{copy['id']}/items/{copy['items'][2]['id']}",
        json={"note": "For the drive"},
    )
    assert (
        client.get(f"/api/packing-list-templates/{template_id}").json()["items"][2]["note"] is None
    )

    client.delete(f"/api/packing-list-templates/{template_id}")

    kept = client.get(f"/api/packing-list-templates/{copy['id']}").json()
    assert _names(kept) == ["Goggles", "Towel", "Snacks"]


def test_a_copy_shares_the_household_bags(client, db_session, nana):
    bags = db_session.query(PackingListBag).count()
    copy = _copy(client, nana["template"]["id"]).json()

    assert db_session.query(PackingListBag).count() == bags
    assert copy["items"][0]["bag_id"] == nana["goggles"]["bag_id"]


def test_a_copy_does_not_touch_item_history(client, db_session, nana):
    def history():
        return sorted(
            (r.name, r.times_added, r.owner_id, r.bag_id, r.last_added_at)
            for r in db_session.query(PackingListItemHistory)
        )

    suggestion = client.get("/api/packing-list-items/suggestions?q=snacks").json()[0]
    client.delete(f"/api/packing-list-items/suggestions/{suggestion['id']}")
    db_session.expire_all()
    before = history()

    _copy(client, nana["template"]["id"])

    db_session.expire_all()
    assert history() == before
    assert "Snacks" not in {name for name, *_ in history()}


def test_a_copy_of_an_empty_template_is_an_empty_template(client):
    source = _packing_list(client)

    response = _copy(client, source["id"])

    assert response.status_code == 201
    assert response.json()["items"] == []


def test_a_copy_puts_itself_on_no_day(client, nana):
    _day(client, nana["template"]["id"])

    copy = _copy(client, nana["template"]["id"]).json()

    assert (copy["day_count"], copy["upcoming_days"]) == (0, 0)
    assert [d["packing_list_template_id"] for d in client.get("/api/packing-list-days").json()] == [
        nana["template"]["id"]
    ]


def test_an_unknown_source_is_422_and_creates_nothing(client, nana):
    response = _copy(client, 999)

    assert response.status_code == 422
    assert [t["name"] for t in client.get("/api/packing-list-templates").json()] == [
        "Swim at Nana's"
    ]


def test_a_taken_name_is_409_and_copies_nothing(client, db_session, nana):
    items = db_session.query(PackingListTemplateItem).count()

    response = _copy(client, nana["template"]["id"], name="  swim at nana's ")

    assert response.status_code == 409
    assert db_session.query(PackingListTemplateItem).count() == items


def test_a_template_without_a_source_is_made_as_before(client, nana):
    response = client.post("/api/packing-list-templates", json={"name": "Beach day"})

    assert response.status_code == 201
    assert response.json()["items"] == []
