"""A day's own order (#252): items on one day dragged into the order that suits it.

The order lives on the day (``PackingListDay.item_order``), not on its items:
a reading of a template item copies the item whole and counts as a change, so
an order kept there would mark every reordered row ``(changed)``. A move within
a group is order only; a move into another group is that group's owner or bag,
on this day only. The template's own order wins when it is rearranged, and the
stored order is read leniently, because nothing in the database checks it.
"""

from datetime import UTC, date, datetime

import pytest

from rally import packing_lists as logic
from rally.models import PackingListDay

# Thursday, 1 October 2026, mid-morning in Chicago.
NOW = datetime(2026, 10, 1, 15, 0, tzinfo=UTC)
SATURDAY = "2026-10-03"


@pytest.fixture(autouse=True)
def _frozen(frozen_now, local_timezone):
    local_timezone("America/Chicago")
    frozen_now(NOW)


@pytest.fixture
def swim(client, make_member):
    emma = make_member(name="Emma")
    jake = make_member(name="Jake")
    template = client.post("/api/packing-list-templates", json={"name": "Swim"}).json()

    def item(name, **extra):
        return client.post(
            f"/api/packing-list-templates/{template['id']}/items", json={"name": name, **extra}
        ).json()

    items = {
        "goggles": item("Goggles", owner_id=emma.id),
        "towel": item("Towel", owner_id=emma.id),
        "snacks": item("Snacks"),
        "water": item("Water"),
    }
    day = client.post(
        "/api/packing-list-days",
        json={"packing_list_template_id": template["id"], "date": SATURDAY},
    ).json()
    return {"template": template, "day": day, "emma": emma, "jake": jake, **items}


def _ref(source, item):
    return {"source": source, "id": item["id"]}


def _reorder(client, day_id, view, key, items):
    return client.post(
        f"/api/packing-list-days/{day_id}/items/reorder",
        json={"view": view, "key": key, "items": items},
    )


def _names(client, day_id):
    return [i["name"] for i in client.get(f"/api/packing-list-days/{day_id}").json()["items"]]


def test_a_move_within_a_group_is_order_only(client, swim):
    day = swim["day"]["id"]
    response = _reorder(
        client,
        day,
        "owner",
        swim["emma"].id,
        [_ref("template", swim["towel"]), _ref("template", swim["goggles"])],
    )

    assert response.status_code == 200
    body = response.json()
    assert [i["name"] for i in body["items"]] == ["Towel", "Goggles", "Snacks", "Water"]
    assert not any(i["changed"] for i in body["items"])
    assert body["changed_count"] == 0


def test_other_days_and_the_template_keep_their_order(client, swim):
    other = client.post(
        "/api/packing-list-days",
        json={"packing_list_template_id": swim["template"]["id"], "date": "2026-10-10"},
    ).json()
    _reorder(
        client,
        swim["day"]["id"],
        "owner",
        None,
        [_ref("template", swim["water"]), _ref("template", swim["snacks"])],
    )

    assert _names(client, other["id"]) == ["Goggles", "Towel", "Snacks", "Water"]
    template = client.get(f"/api/packing-list-templates/{swim['template']['id']}").json()
    assert [i["name"] for i in template["items"]] == ["Goggles", "Towel", "Snacks", "Water"]


def test_a_move_into_another_group_changes_the_owner_on_this_day_only(client, swim):
    day = swim["day"]["id"]
    body = _reorder(client, day, "owner", swim["jake"].id, [_ref("template", swim["towel"])]).json()

    towel = next(i for i in body["items"] if i["name"] == "Towel")
    assert (towel["owner_id"], towel["changed"]) == (swim["jake"].id, True)
    assert body["changed_count"] == 1
    template = client.get(f"/api/packing-list-templates/{swim['template']['id']}").json()
    assert next(i for i in template["items"] if i["name"] == "Towel")["owner_id"] == swim["emma"].id


def test_a_days_own_item_moves_among_the_templates(client, swim):
    day = swim["day"]["id"]
    hat = client.post(f"/api/packing-list-days/{day}/day-items", json={"name": "Sun hat"}).json()[
        "items"
    ][-1]
    body = _reorder(
        client,
        day,
        "owner",
        swim["emma"].id,
        [_ref("template", swim["goggles"]), _ref("day", hat), _ref("template", swim["towel"])],
    ).json()

    hat_row = next(i for i in body["items"] if i["name"] == "Sun hat")
    # Its own owner changed directly; a day's own item is never "changed".
    assert (hat_row["owner_id"], hat_row["changed"]) == (swim["emma"].id, False)


def test_the_listed_items_take_the_places_they_held(client, swim):
    day = swim["day"]["id"]
    hat = client.post(f"/api/packing-list-days/{day}/day-items", json={"name": "Sun hat"}).json()[
        "items"
    ][-1]
    _reorder(
        client,
        day,
        "owner",
        swim["emma"].id,
        [_ref("template", swim["goggles"]), _ref("day", hat), _ref("template", swim["towel"])],
    )
    # Goggles, Towel held places 0 and 1; the hat (place 4) joined them, so the
    # three are dealt into 0, 1 and 4 and Snacks and Water keep 2 and 3.
    assert _names(client, day) == ["Goggles", "Sun hat", "Snacks", "Water", "Towel"]


def test_items_added_later_go_to_the_bottom(client, swim):
    day = swim["day"]["id"]
    _reorder(
        client,
        day,
        "owner",
        None,
        [_ref("template", swim["water"]), _ref("template", swim["snacks"])],
    )
    client.post(
        f"/api/packing-list-templates/{swim['template']['id']}/items", json={"name": "Floatie"}
    )
    client.post(f"/api/packing-list-days/{day}/day-items", json={"name": "Sun hat"})

    assert _names(client, day) == ["Goggles", "Towel", "Water", "Snacks", "Floatie", "Sun hat"]


def test_the_templates_order_wins_from_today_on(client, db_session, swim):
    day = swim["day"]["id"]
    past = PackingListDay(
        packing_list_template_id=swim["template"]["id"],
        date="2026-09-26",
        item_order=[f"template:{swim['water']['id']}"],
    )
    db_session.add(past)
    db_session.commit()
    _reorder(
        client,
        day,
        "owner",
        None,
        [_ref("template", swim["water"]), _ref("template", swim["snacks"])],
    )

    client.post(
        f"/api/packing-list-templates/{swim['template']['id']}/items/reorder",
        json={
            "view": "owner",
            "key": swim["emma"].id,
            "item_ids": [swim["towel"]["id"], swim["goggles"]["id"]],
        },
    )

    assert _names(client, day) == ["Towel", "Goggles", "Snacks", "Water"]
    db_session.expire_all()
    assert db_session.get(PackingListDay, past.id).item_order == [f"template:{swim['water']['id']}"]


def test_a_stale_order_is_read_leniently(client, db_session, swim):
    day = db_session.get(PackingListDay, swim["day"]["id"])
    day.item_order = [
        f"template:{swim['water']['id']}",
        "template:999",  # deleted from the template
        f"template:{swim['water']['id']}",  # listed twice
        "nonsense",
        42,
    ]
    db_session.commit()
    client.delete(f"/api/packing-list-days/{day.id}/template-items/{swim['snacks']['id']}")

    assert _names(client, day.id) == ["Water", "Goggles", "Towel"]


def test_unknown_items_change_nothing(client, swim):
    day = swim["day"]["id"]
    response = _reorder(
        client,
        day,
        "owner",
        swim["jake"].id,
        [_ref("template", swim["towel"]), {"source": "day", "id": 999}],
    )
    assert response.status_code == 404
    body = client.get(f"/api/packing-list-days/{day}").json()
    assert [i["name"] for i in body["items"]] == ["Goggles", "Towel", "Snacks", "Water"]
    assert body["changed_count"] == 0


def test_an_unknown_owner_or_bag_is_422(client, swim):
    day = swim["day"]["id"]
    towel = [_ref("template", swim["towel"])]
    assert _reorder(client, day, "owner", 999, towel).status_code == 422
    assert _reorder(client, day, "bag", 999, towel).status_code == 422


def test_duplicates_keep_their_first_mention(client, swim):
    day = swim["day"]["id"]
    _reorder(
        client,
        day,
        "owner",
        None,
        [
            _ref("template", swim["water"]),
            _ref("template", swim["snacks"]),
            _ref("template", swim["water"]),
        ],
    )
    assert _names(client, day) == ["Goggles", "Towel", "Water", "Snacks"]


def test_a_past_day_cannot_be_reordered(client, db_session, swim):
    past = PackingListDay(packing_list_template_id=swim["template"]["id"], date="2026-09-26")
    db_session.add(past)
    db_session.commit()
    response = _reorder(client, past.id, "owner", None, [_ref("template", swim["snacks"])])
    assert response.status_code == 403


def test_the_summary_reads_the_days_order(client, db_session, swim):
    _reorder(
        client,
        swim["day"]["id"],
        "owner",
        None,
        [_ref("template", swim["water"]), _ref("template", swim["snacks"])],
    )
    text = logic.summary_text(db_session, date(2026, 10, 1))
    assert text.index("Water") < text.index("Snacks")
