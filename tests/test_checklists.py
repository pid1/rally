"""Checklists (issue #249): reusable lists, put on a day and checked off there.

The two rules the feature exists for are pinned hardest here: an edit to the
checklist reaches every day it is on, and a check on one day reaches nothing
else. Both hold because a day stores only its checks, so most of these tests
are about the edges of that — deletes that must cascade by hand, a group that
must belong to its own checklist, counts that must not drift.
"""

from datetime import UTC, date, datetime

import pytest

from rally import checklists as logic
from rally.models import ChecklistDayCheck, ChecklistGroup, ChecklistItem

# Thursday, 1 October 2026, mid-morning in Chicago.
NOW = datetime(2026, 10, 1, 15, 0, tzinfo=UTC)
TODAY = "2026-10-01"
SATURDAY = "2026-10-03"


@pytest.fixture(autouse=True)
def _frozen(frozen_now, local_timezone):
    local_timezone("America/Chicago")
    frozen_now(NOW)


def _checklist(client, name="Swim at Nana's", **extra):
    response = client.post("/api/checklists", json={"name": name, **extra})
    assert response.status_code == 201, response.text
    return response.json()


def _group(client, checklist_id, name):
    response = client.post(f"/api/checklists/{checklist_id}/groups", json={"name": name})
    assert response.status_code == 201, response.text
    return response.json()


def _item(client, checklist_id, name, **extra):
    response = client.post(f"/api/checklists/{checklist_id}/items", json={"name": name, **extra})
    assert response.status_code == 201, response.text
    return response.json()


def _day(client, checklist_id, day=SATURDAY, **extra):
    response = client.post(
        "/api/checklist-days", json={"checklist_id": checklist_id, "date": day, **extra}
    )
    assert response.status_code == 201, response.text
    return response.json()


def _check(client, day_id, item_id, checked=True):
    response = client.put(
        f"/api/checklist-days/{day_id}/items/{item_id}", json={"checked": checked}
    )
    assert response.status_code == 200, response.text
    return response.json()


def _names(payload):
    return [i["name"] for i in payload["items"]]


# --- Checklists --------------------------------------------------------------------


def test_create_defaults_to_packing_the_day_of(client):
    created = _checklist(client, description="  ")
    assert created["pack_timing"] == "day_of"
    assert created["description"] is None
    assert created["items"] == [] and created["groups"] == []


def test_names_are_trimmed_and_unique_ignoring_case(client):
    _checklist(client, "  Beach day  ")
    assert client.get("/api/checklists").json()[0]["name"] == "Beach day"
    assert client.post("/api/checklists", json={"name": "BEACH DAY"}).status_code == 409
    assert client.post("/api/checklists", json={"name": "   "}).status_code == 422


def test_pack_timing_is_a_closed_set(client):
    response = client.post("/api/checklists", json={"name": "X", "pack_timing": "week_before"})
    assert response.status_code == 422


def test_list_is_by_name_with_counts(client):
    nana = _checklist(client, "swim at Nana's")
    _checklist(client, "Beach day")
    group = _group(client, nana["id"], "Emma")
    _item(client, nana["id"], "Goggles", group_id=group["id"])
    _item(client, nana["id"], "Sunscreen")
    _day(client, nana["id"])

    rows = client.get("/api/checklists").json()
    assert [r["name"] for r in rows] == ["Beach day", "swim at Nana's"]
    assert (rows[1]["item_count"], rows[1]["group_count"], rows[1]["upcoming_days"]) == (2, 1, 1)
    assert (rows[0]["item_count"], rows[0]["group_count"], rows[0]["day_count"]) == (0, 0, 0)


def test_update_renames_and_changes_timing(client):
    created = _checklist(client)
    _checklist(client, "Beach day")
    response = client.put(
        f"/api/checklists/{created['id']}",
        json={"name": "Nana's", "pack_timing": "day_before", "description": "Saturdays"},
    )
    assert response.status_code == 200
    assert response.json()["pack_timing"] == "day_before"
    assert (
        client.put(f"/api/checklists/{created['id']}", json={"name": "beach DAY"}).status_code
        == 409
    )
    # Omitting description leaves it; null clears it.
    assert (
        client.put(f"/api/checklists/{created['id']}", json={}).json()["description"] == "Saturdays"
    )
    cleared = client.put(f"/api/checklists/{created['id']}", json={"description": None})
    assert cleared.json()["description"] is None


def test_deleting_a_checklist_takes_everything_with_it(client, db_session):
    checklist = _checklist(client)
    group = _group(client, checklist["id"], "Emma")
    item = _item(client, checklist["id"], "Goggles", group_id=group["id"])
    day = _day(client, checklist["id"])
    _check(client, day["id"], item["id"])
    survivor = _checklist(client, "Beach day")
    _item(client, survivor["id"], "Towels")

    assert client.delete(f"/api/checklists/{checklist['id']}").status_code == 204

    assert client.get(f"/api/checklists/{checklist['id']}").status_code == 404
    assert client.get(f"/api/checklist-days/{day['id']}").status_code == 404
    assert db_session.query(ChecklistGroup).count() == 0
    assert db_session.query(ChecklistDayCheck).count() == 0
    assert [i.name for i in db_session.query(ChecklistItem)] == ["Towels"]


def test_unknown_checklist_is_404(client):
    assert client.get("/api/checklists/99").status_code == 404
    assert client.put("/api/checklists/99", json={"name": "X"}).status_code == 404
    assert client.delete("/api/checklists/99").status_code == 404


# --- Groups ------------------------------------------------------------------------


def test_groups_append_in_creation_order(client):
    checklist = _checklist(client)
    _group(client, checklist["id"], "Jake")
    _group(client, checklist["id"], "Emma")
    groups = client.get(f"/api/checklists/{checklist['id']}").json()["groups"]
    assert [g["name"] for g in groups] == ["Jake", "Emma"]


def test_group_names_are_unique_within_a_checklist_only(client):
    nana = _checklist(client)
    beach = _checklist(client, "Beach day")
    emma = _group(client, nana["id"], "Emma")
    assert (
        client.post(f"/api/checklists/{nana['id']}/groups", json={"name": "emma"}).status_code
        == 409
    )
    _group(client, beach["id"], "Emma")
    jake = _group(client, nana["id"], "Jake")
    renamed = client.put(f"/api/checklists/{nana['id']}/groups/{jake['id']}", json={"name": "EMMA"})
    assert renamed.status_code == 409
    # Renaming a group to its own name in another case is not a clash.
    same = client.put(f"/api/checklists/{nana['id']}/groups/{emma['id']}", json={"name": "EMMA"})
    assert same.status_code == 200


def test_a_group_is_only_reachable_through_its_own_checklist(client):
    nana = _checklist(client)
    beach = _checklist(client, "Beach day")
    group = _group(client, beach["id"], "Cooler")
    assert client.delete(f"/api/checklists/{nana['id']}/groups/{group['id']}").status_code == 404
    response = client.post(
        f"/api/checklists/{nana['id']}/items", json={"name": "Ice", "group_id": group["id"]}
    )
    assert response.status_code == 422


def test_deleting_a_group_moves_its_items_to_the_bottom_of_general(client):
    checklist = _checklist(client)
    emma = _group(client, checklist["id"], "Emma")
    _item(client, checklist["id"], "Sunscreen")
    _item(client, checklist["id"], "Goggles", group_id=emma["id"])
    _item(client, checklist["id"], "Hair ties", group_id=emma["id"])

    assert (
        client.delete(f"/api/checklists/{checklist['id']}/groups/{emma['id']}").status_code == 204
    )

    body = client.get(f"/api/checklists/{checklist['id']}").json()
    assert body["groups"] == []
    assert _names(body) == ["Sunscreen", "Goggles", "Hair ties"]
    assert {i["group_id"] for i in body["items"]} == {None}


def test_group_by_person_adds_one_group_per_member_once(client, make_member):
    for name in ("Mom", "Emma", "Jake"):
        make_member(name)
    checklist = _checklist(client)
    _group(client, checklist["id"], "emma")

    first = client.post(f"/api/checklists/{checklist['id']}/groups/members")
    again = client.post(f"/api/checklists/{checklist['id']}/groups/members")

    assert first.status_code == 200
    assert [g["name"] for g in first.json()] == ["emma", "Mom", "Jake"]
    assert again.json() == first.json()


# --- Items -------------------------------------------------------------------------


def test_items_read_group_by_group_with_general_last(client):
    checklist = _checklist(client)
    emma = _group(client, checklist["id"], "Emma")
    jake = _group(client, checklist["id"], "Jake")
    _item(client, checklist["id"], "Sunscreen")
    _item(client, checklist["id"], "Rash guard", group_id=jake["id"])
    _item(client, checklist["id"], "Goggles", group_id=emma["id"])
    _item(client, checklist["id"], "Snacks")
    _item(client, checklist["id"], "Hair ties", group_id=emma["id"])

    body = client.get(f"/api/checklists/{checklist['id']}").json()
    # New items land at the bottom of their group: a list is entered in order.
    assert _names(body) == ["Goggles", "Hair ties", "Rash guard", "Sunscreen", "Snacks"]


def test_note_and_group_are_left_alone_unless_sent(client):
    checklist = _checklist(client)
    emma = _group(client, checklist["id"], "Emma")
    item = _item(client, checklist["id"], "Goggles", note="the blue ones", group_id=emma["id"])
    url = f"/api/checklists/{checklist['id']}/items/{item['id']}"

    renamed = client.put(url, json={"name": "Swim goggles"}).json()
    assert (renamed["note"], renamed["group_id"]) == ("the blue ones", emma["id"])

    cleared = client.put(url, json={"note": None, "group_id": None}).json()
    assert (cleared["note"], cleared["group_id"]) == (None, None)


def test_moving_an_item_puts_it_at_the_bottom_of_its_new_group(client):
    checklist = _checklist(client)
    emma = _group(client, checklist["id"], "Emma")
    _item(client, checklist["id"], "Goggles", group_id=emma["id"])
    towel = _item(client, checklist["id"], "Towel")
    client.put(
        f"/api/checklists/{checklist['id']}/items/{towel['id']}", json={"group_id": emma["id"]}
    )
    assert _names(client.get(f"/api/checklists/{checklist['id']}").json()) == ["Goggles", "Towel"]


def test_reorder_rewrites_a_group_and_can_move_items_into_it(client):
    checklist = _checklist(client)
    emma = _group(client, checklist["id"], "Emma")
    a = _item(client, checklist["id"], "Goggles", group_id=emma["id"])
    b = _item(client, checklist["id"], "Hair ties", group_id=emma["id"])
    c = _item(client, checklist["id"], "Towel")

    response = client.post(
        f"/api/checklists/{checklist['id']}/items/reorder",
        json={"group_id": emma["id"], "item_ids": [c["id"], b["id"], a["id"], c["id"]]},
    )

    assert response.status_code == 200
    assert [i["name"] for i in response.json()] == ["Towel", "Hair ties", "Goggles"]
    body = client.get(f"/api/checklists/{checklist['id']}").json()
    assert _names(body) == ["Towel", "Hair ties", "Goggles"]
    assert {i["group_id"] for i in body["items"]} == {emma["id"]}


def test_reorder_is_all_or_nothing(client):
    nana = _checklist(client)
    beach = _checklist(client, "Beach day")
    mine = _item(client, nana["id"], "Goggles")
    theirs = _item(client, beach["id"], "Bucket")

    response = client.post(
        f"/api/checklists/{nana['id']}/items/reorder",
        json={"group_id": None, "item_ids": [mine["id"], theirs["id"]]},
    )

    assert response.status_code == 404
    assert client.get(f"/api/checklists/{beach['id']}").json()["items"][0]["name"] == "Bucket"


def test_an_item_whose_group_vanished_reads_as_general(client, db_session):
    """A stray group id is filed under General rather than dropped from every day."""
    checklist = _checklist(client)
    item = _item(client, checklist["id"], "Goggles")
    db_session.query(ChecklistItem).filter_by(id=item["id"]).update({"group_id": 999})
    db_session.commit()

    body = client.get(f"/api/checklists/{checklist['id']}").json()
    assert body["items"][0]["group_id"] is None


# --- Days ----------------------------------------------------------------------------


def test_adding_to_a_day_starts_unchecked(client):
    checklist = _checklist(client, pack_timing="day_before")
    _item(client, checklist["id"], "Goggles")
    day = _day(client, checklist["id"], label="  Swim  ")

    assert day["date"] == SATURDAY
    assert day["label"] == "Swim"
    assert day["pack_date"] == "2026-10-02"
    assert (day["total"], day["checked"]) == (1, 0)
    assert day["items"][0]["checked"] is False


def test_a_day_is_today_or_later(client):
    checklist = _checklist(client)
    assert _day(client, checklist["id"], TODAY)["date"] == TODAY
    response = client.post(
        "/api/checklist-days", json={"checklist_id": checklist["id"], "date": "2026-09-30"}
    )
    assert response.status_code == 422


def test_once_per_day_and_the_clash_names_the_existing_day(client):
    checklist = _checklist(client)
    day = _day(client, checklist["id"])
    response = client.post(
        "/api/checklist-days", json={"checklist_id": checklist["id"], "date": SATURDAY}
    )
    assert response.status_code == 409
    assert response.json()["detail"]["id"] == day["id"]
    # Another checklist on the same day is fine.
    _day(client, _checklist(client, "Beach day")["id"])


def test_unknown_checklist_or_bad_date_is_422(client):
    assert (
        client.post("/api/checklist-days", json={"checklist_id": 9, "date": SATURDAY}).status_code
        == 422
    )
    checklist = _checklist(client)
    response = client.post(
        "/api/checklist-days", json={"checklist_id": checklist["id"], "date": "10/3/2026"}
    )
    assert response.status_code == 422


def test_moving_a_day_keeps_its_checks_and_honors_the_clash_rule(client):
    checklist = _checklist(client)
    item = _item(client, checklist["id"], "Goggles")
    first = _day(client, checklist["id"])
    _day(client, checklist["id"], "2026-10-10")
    _check(client, first["id"], item["id"])

    moved = client.put(f"/api/checklist-days/{first['id']}", json={"date": "2026-10-04"})
    assert moved.status_code == 200
    assert moved.json()["checked"] == 1
    clash = client.put(f"/api/checklist-days/{first['id']}", json={"date": "2026-10-10"})
    assert clash.status_code == 409
    past = client.put(f"/api/checklist-days/{first['id']}", json={"date": "2026-09-01"})
    assert past.status_code == 422


def test_upcoming_is_soonest_first_and_past_is_newest_first(client, db_session):
    from rally.models import ChecklistDay

    nana = _checklist(client)
    beach = _checklist(client, "Beach day")
    _day(client, beach["id"], "2026-10-10")
    _day(client, nana["id"], SATURDAY)
    _day(client, nana["id"], TODAY)
    for offset in range(12):
        db_session.add(ChecklistDay(checklist_id=nana["id"], date=f"2026-09-{offset + 10:02d}"))
    db_session.commit()

    upcoming = client.get("/api/checklist-days").json()
    assert [(d["date"], d["checklist_name"]) for d in upcoming] == [
        (TODAY, "Swim at Nana's"),
        (SATURDAY, "Swim at Nana's"),
        ("2026-10-10", "Beach day"),
    ]
    past = client.get("/api/checklist-days?when=past").json()
    assert len(past) == 10
    assert past[0]["date"] == "2026-09-21"
    assert len(client.get("/api/checklist-days?when=past&limit=3").json()) == 3
    assert client.get("/api/checklist-days?when=later").status_code == 422


def test_removing_a_day_leaves_the_checklist(client, db_session):
    checklist = _checklist(client)
    item = _item(client, checklist["id"], "Goggles")
    day = _day(client, checklist["id"])
    _check(client, day["id"], item["id"])

    assert client.delete(f"/api/checklist-days/{day['id']}").status_code == 204

    assert db_session.query(ChecklistDayCheck).count() == 0
    assert _names(client.get(f"/api/checklists/{checklist['id']}").json()) == ["Goggles"]


# --- The two rules -------------------------------------------------------------------


def test_checking_off_on_a_day_touches_neither_the_checklist_nor_another_day(client):
    checklist = _checklist(client)
    goggles = _item(client, checklist["id"], "Goggles")
    _item(client, checklist["id"], "Towel")
    saturday = _day(client, checklist["id"])
    next_saturday = _day(client, checklist["id"], "2026-10-10")

    after = _check(client, saturday["id"], goggles["id"])

    assert (after["checked"], after["total"]) == (1, 2)
    assert [i["checked"] for i in after["items"]] == [True, False]
    other = client.get(f"/api/checklist-days/{next_saturday['id']}").json()
    assert other["checked"] == 0
    master = client.get(f"/api/checklists/{checklist['id']}").json()
    assert all("checked" not in i for i in master["items"])


def test_checking_is_idempotent_and_unchecking_removes_the_check(client):
    checklist = _checklist(client)
    item = _item(client, checklist["id"], "Goggles")
    day = _day(client, checklist["id"])

    _check(client, day["id"], item["id"])
    assert _check(client, day["id"], item["id"])["checked"] == 1
    assert _check(client, day["id"], item["id"], checked=False)["checked"] == 0
    assert _check(client, day["id"], item["id"], checked=False)["checked"] == 0


def test_an_item_from_another_checklist_cannot_be_checked(client):
    nana = _checklist(client)
    beach = _checklist(client, "Beach day")
    bucket = _item(client, beach["id"], "Bucket")
    day = _day(client, nana["id"])
    response = client.put(
        f"/api/checklist-days/{day['id']}/items/{bucket['id']}", json={"checked": True}
    )
    assert response.status_code == 404


def test_edits_to_the_checklist_reach_every_day_it_is_on(client, db_session):
    checklist = _checklist(client)
    emma = _group(client, checklist["id"], "Emma")
    goggles = _item(client, checklist["id"], "Goggles")
    towel = _item(client, checklist["id"], "Towel")
    saturday = _day(client, checklist["id"])
    later = _day(client, checklist["id"], "2026-10-10")
    _check(client, saturday["id"], goggles["id"])
    _check(client, saturday["id"], towel["id"])

    base = f"/api/checklists/{checklist['id']}/items"
    client.put(f"{base}/{goggles['id']}", json={"name": "Swim goggles", "group_id": emma["id"]})
    client.delete(f"{base}/{towel['id']}")
    _item(client, checklist["id"], "Wet bag")

    for day_id, checked in ((saturday["id"], [True, False]), (later["id"], [False, False])):
        day = client.get(f"/api/checklist-days/{day_id}").json()
        assert _names(day) == ["Swim goggles", "Wet bag"]
        assert [i["group_id"] for i in day["items"]] == [emma["id"], None]
        assert [i["checked"] for i in day["items"]] == checked
        assert day["total"] == 2
    assert client.get(f"/api/checklist-days/{saturday['id']}").json()["checked"] == 1
    # Deleting an item deletes its checks too, rather than leaving them for the
    # progress count's join to hide.
    assert db_session.query(ChecklistDayCheck).filter_by(item_id=towel["id"]).count() == 0


def test_uncheck_all_resets_one_day_only(client):
    checklist = _checklist(client)
    item = _item(client, checklist["id"], "Goggles")
    saturday = _day(client, checklist["id"])
    later = _day(client, checklist["id"], "2026-10-10")
    _check(client, saturday["id"], item["id"])
    _check(client, later["id"], item["id"])

    reset = client.post(f"/api/checklist-days/{saturday['id']}/reset")

    assert reset.json()["checked"] == 0
    assert client.get(f"/api/checklist-days/{later['id']}").json()["checked"] == 1


def test_a_stray_check_never_inflates_progress(client, db_session):
    checklist = _checklist(client)
    _item(client, checklist["id"], "Goggles")
    day = _day(client, checklist["id"])
    db_session.add(ChecklistDayCheck(day_id=day["id"], item_id=999))
    db_session.commit()

    assert client.get(f"/api/checklist-days/{day['id']}").json()["checked"] == 0


# --- Pack dates and the summary ------------------------------------------------------


def test_pack_date():
    assert logic.pack_date(SATURDAY, "day_of") == SATURDAY
    assert logic.pack_date(SATURDAY, "day_before") == "2026-10-02"
    assert logic.pack_date("2026-03-01", "day_before") == "2026-02-28"


def _summary(db_session, today=date(2026, 10, 2)):
    return logic.summary_text(db_session, today)


def test_summary_is_empty_without_days(client, db_session):
    _item(client, _checklist(client)["id"], "Goggles")
    assert _summary(db_session) == ""


def test_summary_lists_only_unchecked_items_under_their_groups(client, db_session):
    checklist = _checklist(client, pack_timing="day_before")
    emma = _group(client, checklist["id"], "Emma")
    goggles = _item(client, checklist["id"], "Goggles", group_id=emma["id"])
    _item(client, checklist["id"], "Hair ties", group_id=emma["id"])
    _item(client, checklist["id"], "Sunscreen", note="bottle is nearly empty")
    day = _day(client, checklist["id"], label="Swim")
    _check(client, day["id"], goggles["id"])

    text = _summary(db_session)

    assert text.splitlines() == [
        '- "Swim at Nana\'s" (Swim) for Saturday, October 3, packed the day before '
        "(Friday, October 2) — PACK TODAY. 1 of 3 packed.",
        "  Emma:",
        "    - Hair ties",
        "  General:",
        "    - Sunscreen — bottle is nearly empty",
    ]


def test_summary_statuses(client, db_session):
    today_list = _checklist(client, "School backpack")
    _item(client, today_list["id"], "Lunch")
    _day(client, today_list["id"], "2026-10-02")
    upcoming = _checklist(client, "Beach day")
    _item(client, upcoming["id"], "Towels")
    _day(client, upcoming["id"], "2026-10-07")

    lines = _summary(db_session).splitlines()

    assert lines[0].endswith("— TODAY. 0 of 1 packed.")
    assert lines[0].startswith('- "School backpack" for Friday, October 2')
    # A list with no groups is not given a General heading.
    assert lines[1] == "  - Lunch"
    assert "UPCOMING (in 5 days)" in lines[2]


def test_summary_skips_packed_lists_and_days_outside_the_window(client, db_session):
    done = _checklist(client, "Done")
    item = _item(client, done["id"], "Goggles")
    day = _day(client, done["id"])
    _check(client, day["id"], item["id"])
    far = _checklist(client, "Far")
    _item(client, far["id"], "Passport")
    _day(client, far["id"], "2026-10-10")  # 8 days after Oct 2
    empty = _checklist(client, "Empty")
    _day(client, empty["id"])

    assert _summary(db_session) == ""


def test_summary_marks_items_already_on_the_shopping_list(client, db_session, make_shopping_item):
    make_shopping_item("  sunscreen ", completed=False)
    make_shopping_item("Snacks", completed=True)
    checklist = _checklist(client)
    _item(client, checklist["id"], "Sunscreen")
    _item(client, checklist["id"], "Snacks")
    _day(client, checklist["id"])

    text = _summary(db_session)

    assert "- Sunscreen (already on the shopping list)" in text
    assert "- Snacks\n" in text + "\n"
