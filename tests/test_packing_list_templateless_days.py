"""Templateless days (#252): a packing list on a date with no template behind it.

Deleting a template is the one way to make one here. It must never take a
day with it: every day the template was on is copied first so it reads
exactly as it did — what was on it, what this day had changed, what was
checked, its label, its lead time and its order — and then it stands on its
own. Everything a day can do, a templateless day can do, with the few rules
that only make sense with a template switched off.
"""

from datetime import UTC, datetime

import pytest

from rally import packing_lists as logic
from rally.models import (
    PackingListDay,
    PackingListDayCheck,
    PackingListDayItem,
    PackingListItemHistory,
    PackingListTemplateItem,
)

# Thursday, 1 October 2026, mid-morning in Chicago.
NOW = datetime(2026, 10, 1, 15, 0, tzinfo=UTC)
SATURDAY = "2026-10-03"
LATER = "2026-10-10"
PAST = "2026-09-26"


@pytest.fixture(autouse=True)
def _frozen(frozen_now, local_timezone):
    local_timezone("America/Chicago")
    frozen_now(NOW)


def _template(client, name="Swim at Nana's", **extra):
    response = client.post("/api/packing-list-templates", json={"name": name, **extra})
    assert response.status_code == 201, response.text
    return response.json()


def _item(client, template_id, name, **extra):
    response = client.post(
        f"/api/packing-list-templates/{template_id}/items", json={"name": name, **extra}
    )
    assert response.status_code == 201, response.text
    return response.json()


def _day(client, template_id, day=SATURDAY, **extra):
    response = client.post(
        "/api/packing-list-days",
        json={"packing_list_template_id": template_id, "date": day, **extra},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _past_day(db_session, template_id, day=PAST, **extra):
    row = PackingListDay(packing_list_template_id=template_id, date=day, **extra)
    db_session.add(row)
    db_session.commit()
    return row.id


def _read(day):
    return [(i["name"], i["owner_id"], i["bag_id"], i["checked"]) for i in day["items"]]


def _delete_template(client, template_id):
    assert client.delete(f"/api/packing-list-templates/{template_id}").status_code == 204


@pytest.fixture
def nana(client, make_member):
    """A template with three items, an owner and a bag, packed a day ahead."""
    emma = make_member(name="Emma")
    template = _template(client, description="Saturdays at Nana's pool", pack_days_before=1)
    goggles = _item(client, template["id"], "Goggles", owner_id=emma.id, bag="Pool bag")
    towel = _item(client, template["id"], "Towel", owner_id=emma.id, bag="Pool bag")
    snacks = _item(client, template["id"], "Snacks")
    return {
        "template": template,
        "emma": emma,
        "goggles": goggles,
        "towel": towel,
        "snacks": snacks,
    }


# --- Converting a template's days -------------------------------------------------


def test_every_day_reads_exactly_as_it_did(client, db_session, nana):
    template_id = nana["template"]["id"]
    day = _day(client, template_id, label="Cousins are coming too")
    base = f"/api/packing-list-days/{day['id']}"
    client.put(f"{base}/template-items/{nana['towel']['id']}", json={"name": "Beach towel"})
    client.delete(f"{base}/template-items/{nana['snacks']['id']}")
    client.put(f"{base}/template-items/{nana['goggles']['id']}", json={"checked": True})
    client.post(f"{base}/day-items", json={"name": "Sun hat"})
    client.put(base, json={"pack_days_before": 2})
    before = client.get(base).json()

    _delete_template(client, template_id)

    after = client.get(base).json()
    assert _read(after) == _read(before)
    assert (after["checked"], after["total"]) == (before["checked"], before["total"])
    assert after["packing_list_template_id"] is None
    assert (after["name"], after["description"]) == ("Swim at Nana's", "Saturdays at Nana's pool")
    assert (after["label"], after["pack_days_before"], after["pack_date"]) == (
        "Cousins are coming too",
        2,
        "2026-10-01",
    )
    # Every item is the day's own now, unmarked: nothing to differ from.
    assert {i["source"] for i in after["items"]} == {"day"}
    assert not any(i["changed"] for i in after["items"])
    assert after["changed_count"] == 0


def test_past_days_are_kept_in_the_archive(client, db_session, nana):
    template_id = nana["template"]["id"]
    past = _past_day(db_session, template_id)
    db_session.add(PackingListDayCheck(day_id=past, template_item_id=nana["goggles"]["id"]))
    db_session.commit()

    _delete_template(client, template_id)

    archive = client.get("/api/packing-list-days/previous").json()
    assert [(d["id"], d["name"], d["checked"], d["total"]) for d in archive["items"]] == [
        (past, "Swim at Nana's", 1, 3)
    ]
    # Found by the name it carries now.
    assert client.get("/api/packing-list-days/previous?search=nana").json()["total"] == 1


def test_a_lead_time_it_followed_becomes_its_own(client, nana):
    day = _day(client, nana["template"]["id"])
    assert day["pack_days_before"] == 1

    _delete_template(client, nana["template"]["id"])

    assert client.get(f"/api/packing-list-days/{day['id']}").json()["pack_days_before"] == 1


def test_a_scheduled_day_is_no_longer_the_schedules(client, db_session, nana):
    template_id = nana["template"]["id"]
    client.post(
        "/api/packing-list-template-schedules",
        json={
            "packing_list_template_id": template_id,
            "recurrence_type": "weekly",
            "recurrence_day": 5,
        },
    )
    made = client.get("/api/packing-list-days").json()
    assert made and made[0]["schedule_id"] is not None

    _delete_template(client, template_id)

    kept = client.get(f"/api/packing-list-days/{made[0]['id']}").json()
    assert kept["schedule_id"] is None


def test_a_hand_arranged_order_survives(client, nana):
    day = _day(client, nana["template"]["id"])
    base = f"/api/packing-list-days/{day['id']}"
    hat = client.post(f"{base}/day-items", json={"name": "Sun hat"}).json()["items"][-1]
    client.post(
        f"{base}/items/reorder",
        json={
            "view": "owner",
            "key": None,
            "items": [
                {"source": "day", "id": hat["id"]},
                {"source": "template", "id": nana["snacks"]["id"]},
            ],
        },
    )
    before = [i["name"] for i in client.get(base).json()["items"]]
    assert before == ["Goggles", "Towel", "Sun hat", "Snacks"]

    _delete_template(client, nana["template"]["id"])

    assert [i["name"] for i in client.get(base).json()["items"]] == before


def test_nothing_is_left_pointing_at_the_template(client, db_session, nana):
    template_id = nana["template"]["id"]
    day = _day(client, template_id)
    base = f"/api/packing-list-days/{day['id']}"
    client.put(f"{base}/template-items/{nana['towel']['id']}", json={"checked": True})
    client.put(f"{base}/template-items/{nana['goggles']['id']}", json={"note": "Blue pair"})

    _delete_template(client, template_id)

    assert db_session.query(PackingListDayCheck).count() == 0
    assert (
        db_session.query(PackingListDayItem)
        .filter(PackingListDayItem.template_item_id.isnot(None))
        .count()
        == 0
    )
    assert db_session.query(PackingListTemplateItem).count() == 0
    assert db_session.get(PackingListDay, day["id"]).item_order is None


def test_other_templates_days_and_history_are_untouched(client, db_session, nana):
    beach = _template(client, "Beach day")
    umbrella = _item(client, beach["id"], "Umbrella")
    beach_day = _day(client, beach["id"])
    client.put(
        f"/api/packing-list-days/{beach_day['id']}/template-items/{umbrella['id']}",
        json={"checked": True},
    )
    _day(client, nana["template"]["id"])
    history = sorted(
        (r.name, r.times_added, r.owner_id, r.bag_id)
        for r in db_session.query(PackingListItemHistory)
    )

    _delete_template(client, nana["template"]["id"])

    kept = client.get(f"/api/packing-list-days/{beach_day['id']}").json()
    assert kept["packing_list_template_id"] == beach["id"]
    assert [(i["name"], i["source"], i["checked"]) for i in kept["items"]] == [
        ("Umbrella", "template", True)
    ]
    db_session.expire_all()
    assert (
        sorted(
            (r.name, r.times_added, r.owner_id, r.bag_id)
            for r in db_session.query(PackingListItemHistory)
        )
        == history
    )


def test_the_confirm_count_includes_past_days(client, db_session, nana):
    """``day_count`` is what the delete confirm names: every day it is on."""
    template_id = nana["template"]["id"]
    _past_day(db_session, template_id)
    _day(client, template_id)
    row = client.get(f"/api/packing-list-templates/{template_id}").json()
    assert (row["day_count"], row["upcoming_days"]) == (2, 1)


# --- Living without a template ----------------------------------------------------


@pytest.fixture
def templateless(client, nana):
    day = _day(client, nana["template"]["id"])
    _delete_template(client, nana["template"]["id"])
    return client.get(f"/api/packing-list-days/{day['id']}").json()


def test_it_is_listed_coming_up(client, templateless):
    listed = client.get("/api/packing-list-days").json()
    assert [(d["id"], d["name"]) for d in listed] == [(templateless["id"], "Swim at Nana's")]


def test_its_own_items_are_edited_checked_and_deleted_as_any_days_are(client, templateless):
    base = f"/api/packing-list-days/{templateless['id']}"
    towel = next(i for i in templateless["items"] if i["name"] == "Towel")
    assert (
        client.put(f"{base}/day-items/{towel['id']}", json={"checked": True}).json()["checked"] == 1
    )
    added = client.post(f"{base}/day-items", json={"name": "Sun hat"}).json()
    assert [i["name"] for i in added["items"]][-1] == "Sun hat"
    assert client.post(f"{base}/check-all").json()["checked"] == 4
    assert client.post(f"{base}/reset").json()["checked"] == 0
    assert client.delete(f"{base}/day-items/{towel['id']}").json()["total"] == 3


def test_template_item_endpoints_find_nothing(client, templateless, nana):
    base = f"/api/packing-list-days/{templateless['id']}"
    item = nana["towel"]["id"]
    assert client.put(f"{base}/template-items/{item}", json={"checked": True}).status_code == 404
    assert client.delete(f"{base}/template-items/{item}").status_code == 404


def test_it_needs_its_own_lead_time(client, templateless):
    base = f"/api/packing-list-days/{templateless['id']}"
    assert client.put(base, json={"pack_days_before": None}).status_code == 422
    assert client.put(base, json={"pack_days_before": 3}).json()["pack_days_before"] == 3


def test_it_never_clashes_on_a_date(client, nana, db_session):
    template_id = nana["template"]["id"]
    first = _day(client, template_id)
    second = _day(client, template_id, LATER)
    _delete_template(client, template_id)

    moved = client.put(f"/api/packing-list-days/{second['id']}", json={"date": SATURDAY})
    assert moved.status_code == 200
    assert {d["id"] for d in client.get("/api/packing-list-days").json()} == {
        first["id"],
        second["id"],
    }


def test_taking_it_off_its_day_deletes_it(client, db_session, templateless):
    assert client.delete(f"/api/packing-list-days/{templateless['id']}").status_code == 204
    assert client.get(f"/api/packing-list-days/{templateless['id']}").status_code == 404
    assert db_session.query(PackingListDayItem).count() == 0


def test_a_past_one_is_read_only(client, db_session, nana):
    past = _past_day(db_session, nana["template"]["id"])
    _delete_template(client, nana["template"]["id"])
    own = db_session.query(PackingListDayItem).filter_by(day_id=past).first()
    base = f"/api/packing-list-days/{past}"
    assert client.put(f"{base}/day-items/{own.id}", json={"checked": True}).status_code == 403
    assert client.put(base, json={"label": "Late"}).status_code == 403
    assert client.delete(base).status_code == 403


def test_the_summary_lists_it_under_its_own_name(client, db_session, templateless):
    from datetime import date

    text = logic.summary_text(db_session, date(2026, 10, 1))
    assert text.startswith('- "Swim at Nana\'s" for Saturday, October 3, packed the day before')
    assert "Goggles" in text and "Snacks" in text
