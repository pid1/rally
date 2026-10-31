"""Packing lists with their own name that include live templates (#281).

A packing list has its own name and its own items, and includes any number
of templates, each read live. One test per Acceptance bullet of the issue,
then the Decisions it rests on: no deduplication, the lead time, deleting a
template, one template on one date on two lists but once per list, Edit adding
and removing templates, Resync across every template, the summary, and a
schedule still putting a single template on a day.
"""

from datetime import UTC, datetime

import pytest

from rally import packing_lists as logic
from rally.models import PackingListDay, PackingListDayTemplate

# Thursday, 8 October 2026, mid-morning in Chicago. The lookahead runs through
# Thursday the 15th.
NOW = datetime(2026, 10, 8, 15, 0, tzinfo=UTC)
TODAY = "2026-10-08"
THURSDAY = "2026-10-15"
SATURDAY = "2026-10-10"
SUNDAY = "2026-10-11"
MONDAY = "2026-10-12"
TRIP = "Dad Goes to South Carolina"


@pytest.fixture(autouse=True)
def _frozen(frozen_now, local_timezone):
    local_timezone("America/Chicago")
    frozen_now(NOW)


def _ok(response, status=200):
    assert response.status_code == status, response.text
    return response.json()


def _template(client, name, items=(), **extra):
    template = _ok(client.post("/api/packing-list-templates", json={"name": name, **extra}), 201)
    for item in items:
        _ok(
            client.post(f"/api/packing-list-templates/{template['id']}/items", json={"name": item}),
            201,
        )
    return _ok(client.get(f"/api/packing-list-templates/{template['id']}"))


def _list(client, name, template_ids=(), day=THURSDAY, **extra):
    return _ok(
        client.post(
            "/api/packing-list-days",
            json={
                "name": name,
                "packing_list_template_ids": list(template_ids),
                "date": day,
                **extra,
            },
        ),
        201,
    )


def _own(client, day_id, name):
    return _ok(client.post(f"/api/packing-list-days/{day_id}/day-items", json={"name": name}), 201)


def _get(client, day_id):
    return _ok(client.get(f"/api/packing-list-days/{day_id}"))


def _names(day):
    return [i["name"] for i in day["items"]]


def _days(client):
    return _ok(client.get("/api/packing-list-days"))


@pytest.fixture
def overnight(client):
    return _template(
        client,
        "Dad's Overnight Bag",
        ["Toothbrush", "Phone charger", "Pajamas"],
        pack_days_before=1,
    )


@pytest.fixture
def work_kit(client):
    return _template(client, "Dad's Work Kit", ["Laptop", "Phone charger"], pack_days_before=2)


# --- Acceptance ------------------------------------------------------------------


def test_an_event_gives_its_trip_a_named_list_with_a_template_and_its_own_items(client, overnight):
    """I create the event *Dad flies to South Carolina* and give it the packing
    list *Dad Goes to South Carolina*, which includes *Dad's Overnight Bag*
    and has two trip-only items."""
    event = _ok(
        client.post(
            "/api/events",
            json={
                "title": "Dad flies to South Carolina",
                "all_day": True,
                "start": THURSDAY,
                "end": THURSDAY,
                "packing_lists": [{"name": TRIP, "packing_list_template_ids": [overnight["id"]]}],
            },
        ),
        201,
    )
    assert event["packing_lists"] == [
        {"key": logic.list_key(TRIP), "name": TRIP, "packing_list_template_ids": [overnight["id"]]}
    ]
    (day,) = _days(client)
    assert (day["name"], day["date"]) == (TRIP, THURSDAY)
    assert [t["name"] for t in day["templates"]] == ["Dad's Overnight Bag"]
    assert [e["title"] for e in day["events"]] == ["Dad flies to South Carolina"]

    _own(client, day["id"], "South Carolina client folder")
    day = _own(client, day["id"], "Rain jacket")

    # One list: the template's items, then its own, with where each came from.
    assert [(i["name"], i["source"], i["packing_list_template_id"]) for i in day["items"]] == [
        ("Toothbrush", "template", overnight["id"]),
        ("Phone charger", "template", overnight["id"]),
        ("Pajamas", "template", overnight["id"]),
        ("South Carolina client folder", "day", None),
        ("Rain jacket", "day", None),
    ]
    assert (day["checked"], day["total"], day["changed_count"]) == (0, 5, 0)
    # The occurrence names the list by its own name.
    occurrence = _ok(client.get("/api/events", params={"start": THURSDAY, "end": THURSDAY}))[
        "occurrences"
    ][0]
    assert [(p["name"], p["day_ids"]) for p in occurrence["packing_lists"]] == [(TRIP, [day["id"]])]


def test_an_item_added_to_the_template_before_the_trip_shows_up_on_the_list(client, overnight):
    """I add *Travel pillow* to *Dad's Overnight Bag* before the trip, and it
    appears on *Dad Goes to South Carolina*."""
    day = _list(client, TRIP, [overnight["id"]])
    _own(client, day["id"], "Rain jacket")

    _ok(
        client.post(
            f"/api/packing-list-templates/{overnight['id']}/items", json={"name": "Travel pillow"}
        ),
        201,
    )
    toothbrush = overnight["items"][0]
    _ok(
        client.put(
            f"/api/packing-list-templates/{overnight['id']}/items/{toothbrush['id']}",
            json={"name": "Electric toothbrush"},
        )
    )
    pajamas = overnight["items"][2]
    client.delete(f"/api/packing-list-templates/{overnight['id']}/items/{pajamas['id']}")

    # Additions show up, edits carry through, deleted items disappear.
    assert _names(_get(client, day["id"])) == [
        "Electric toothbrush",
        "Phone charger",
        "Travel pillow",
        "Rain jacket",
    ]


def test_trip_only_items_never_reach_the_template_or_another_day(client, overnight):
    """The two trip-only items never appear on the template or on any other
    day."""
    trip = _list(client, TRIP, [overnight["id"]])
    other = _list(client, "Nana's", [overnight["id"]], day=SATURDAY)
    _own(client, trip["id"], "South Carolina client folder")
    _own(client, trip["id"], "Beach shoes")

    template = _ok(client.get(f"/api/packing-list-templates/{overnight['id']}"))
    assert [i["name"] for i in template["items"]] == ["Toothbrush", "Phone charger", "Pajamas"]
    assert _names(_get(client, other["id"])) == ["Toothbrush", "Phone charger", "Pajamas"]


def test_two_templates_with_one_item_name_show_it_twice(client, overnight, work_kit):
    """A list with *Dad's Overnight Bag* and *Dad's Work Kit*, both containing
    *Phone charger*, shows two chargers."""
    day = _list(client, TRIP, [overnight["id"], work_kit["id"]])
    assert _names(day) == ["Toothbrush", "Phone charger", "Pajamas", "Laptop", "Phone charger"]
    chargers = [i for i in day["items"] if i["name"] == "Phone charger"]
    assert {i["packing_list_template_id"] for i in chargers} == {overnight["id"], work_kit["id"]}
    # Each is its own item: checking one leaves the other.
    checked = _ok(
        client.put(
            f"/api/packing-list-days/{day['id']}/template-items/{chargers[0]['id']}",
            json={"checked": True},
        )
    )
    assert [i["checked"] for i in checked["items"] if i["name"] == "Phone charger"] == [
        True,
        False,
    ]


def test_one_template_on_two_lists_on_one_date_each_with_its_own_checks(client, overnight):
    """*Dad's Overnight Bag* can be on a morning list (Nana's) and an afternoon
    list (South Carolina) on the same date, each with its own checks."""
    morning = _list(client, "Nana's", [overnight["id"]], label="Morning")
    afternoon = _list(client, TRIP, [overnight["id"]], label="Afternoon")
    toothbrush = overnight["items"][0]["id"]
    _ok(
        client.put(
            f"/api/packing-list-days/{morning['id']}/template-items/{toothbrush}",
            json={"checked": True},
        )
    )
    assert _get(client, morning["id"])["checked"] == 1
    assert _get(client, afternoon["id"])["checked"] == 0
    assert sorted(d["name"] for d in _days(client) if d["date"] == THURSDAY) == [TRIP, "Nana's"]


def test_the_same_list_from_add_packing_list_with_no_event(client, overnight):
    """I can make the same list from `Add Packing List` with no event."""
    day = _list(client, TRIP, [overnight["id"]], label="Business trip")
    day = _own(client, day["id"], "South Carolina client folder")
    assert (day["name"], day["label"], day["events"]) == (TRIP, "Business trip", [])
    assert [t["id"] for t in day["templates"]] == [overnight["id"]]
    assert _names(day)[-1] == "South Carolina client folder"


def test_a_three_day_event_puts_its_list_on_its_first_day_only(client, overnight):
    """A three-day event puts its list on the first day only."""
    _ok(
        client.post(
            "/api/events",
            json={
                "title": "Dad flies to South Carolina",
                "all_day": True,
                "start": SATURDAY,
                "end": MONDAY,
                "packing_lists": [{"name": TRIP, "packing_list_template_ids": [overnight["id"]]}],
            },
        ),
        201,
    )
    assert [(d["date"], d["name"]) for d in _days(client)] == [(SATURDAY, TRIP)]


def test_deleting_the_template_leaves_its_items_on_the_list_as_its_own(client, overnight, work_kit):
    """Deleting *Dad's Overnight Bag* leaves its items on *Dad Goes to South
    Carolina* as the list's own."""
    day = _list(client, TRIP, [overnight["id"], work_kit["id"]])
    toothbrush = overnight["items"][0]["id"]
    base = f"/api/packing-list-days/{day['id']}"
    _ok(client.put(f"{base}/template-items/{toothbrush}", json={"checked": True}))
    _ok(client.put(f"{base}/template-items/{overnight['items'][1]['id']}", json={"name": "Cable"}))
    _own(client, day["id"], "Rain jacket")
    before = [(i["name"], i["checked"]) for i in _get(client, day["id"])["items"]]

    assert client.delete(f"/api/packing-list-templates/{overnight['id']}").status_code == 204

    after = _get(client, day["id"])
    # It reads exactly as it did, in the same order, checks and edits kept.
    assert [(i["name"], i["checked"]) for i in after["items"]] == before
    assert [(i["name"], i["source"]) for i in after["items"]] == [
        ("Toothbrush", "day"),
        ("Cable", "day"),
        ("Pajamas", "day"),
        ("Laptop", "template"),
        ("Phone charger", "template"),
        ("Rain jacket", "day"),
    ]
    assert [t["name"] for t in after["templates"]] == ["Dad's Work Kit"]
    assert after["name"] == TRIP
    assert after["changed_count"] == 0


def test_moving_or_deleting_the_event_moves_or_removes_the_list(client, overnight):
    """Moving or deleting the event moves or removes the list, following
    #270's rules."""
    event = _ok(
        client.post(
            "/api/events",
            json={
                "title": "Dad flies to South Carolina",
                "all_day": True,
                "start": SATURDAY,
                "end": SATURDAY,
                "packing_lists": [{"name": TRIP, "packing_list_template_ids": [overnight["id"]]}],
            },
        ),
        201,
    )
    (day,) = _days(client)
    _own(client, day["id"], "Rain jacket")
    _ok(
        client.put(
            f"/api/packing-list-days/{day['id']}/template-items/{overnight['items'][0]['id']}",
            json={"checked": True},
        )
    )

    _ok(client.put(f"/api/events/{event['id']}", json={"start": SUNDAY, "end": SUNDAY}))
    (moved,) = _days(client)
    # The same list, moved with its checks and its own items.
    assert (moved["id"], moved["date"], moved["checked"]) == (day["id"], SUNDAY, 1)
    assert "Rain jacket" in _names(moved)

    response = client.delete(f"/api/events/{event['id']}", params={"release": "remove"})
    assert response.status_code == 204
    assert _days(client) == []


# --- Decisions --------------------------------------------------------------------


def test_the_lead_time_is_the_longest_of_its_templates_and_can_be_overridden(
    client, overnight, work_kit
):
    day = _list(client, TRIP, [overnight["id"], work_kit["id"]])
    assert (day["pack_days_before"], day["pack_days_before_own"]) == (2, None)
    assert day["pack_date"] == "2026-10-13"

    # The templates change: the list follows.
    _ok(client.put(f"/api/packing-list-templates/{overnight['id']}", json={"pack_days_before": 4}))
    assert _get(client, day["id"])["pack_days_before"] == 4

    # Edit overrides it; clearing the override goes back to the longest.
    own = _ok(client.put(f"/api/packing-list-days/{day['id']}", json={"pack_days_before": 0}))
    assert (own["pack_days_before"], own["pack_days_before_own"]) == (0, 0)
    back = _ok(client.put(f"/api/packing-list-days/{day['id']}", json={"pack_days_before": None}))
    assert (back["pack_days_before"], back["pack_days_before_own"]) == (4, None)


def test_a_list_with_no_template_needs_its_own_lead_time(client):
    response = client.post("/api/packing-list-days", json={"name": "Concert", "date": THURSDAY})
    assert response.status_code == 422
    day = _list(client, "Concert", pack_days_before=1)
    assert (
        client.put(
            f"/api/packing-list-days/{day['id']}", json={"pack_days_before": None}
        ).status_code
        == 422
    )


def test_a_template_is_on_a_list_once(client, overnight):
    day = _list(client, TRIP, [overnight["id"], overnight["id"]])
    assert [t["id"] for t in day["templates"]] == [overnight["id"]]
    again = _ok(
        client.put(
            f"/api/packing-list-days/{day['id']}",
            json={"packing_list_template_ids": [overnight["id"], overnight["id"]]},
        )
    )
    assert [t["id"] for t in again["templates"]] == [overnight["id"]]
    assert len(_names(again)) == 3


def test_edit_adds_and_removes_included_templates(client, db_session, overnight, work_kit):
    day = _list(client, TRIP, [overnight["id"]])
    _own(client, day["id"], "Rain jacket")
    base = f"/api/packing-list-days/{day['id']}"
    toothbrush = overnight["items"][0]["id"]
    _ok(client.put(f"{base}/template-items/{toothbrush}", json={"checked": True}))

    added = _ok(
        client.put(base, json={"packing_list_template_ids": [overnight["id"], work_kit["id"]]})
    )
    assert [t["name"] for t in added["templates"]] == ["Dad's Overnight Bag", "Dad's Work Kit"]
    assert "Laptop" in _names(added)
    assert added["checked"] == 1

    removed = _ok(client.put(base, json={"packing_list_template_ids": [work_kit["id"]]}))
    # The Overnight Bag's items leave with their checks; the list's own stay.
    assert _names(removed) == ["Laptop", "Phone charger", "Rain jacket"]
    assert removed["checked"] == 0
    assert (removed["pack_days_before"], removed["pack_days_before_own"]) == (2, None)
    # The template itself is untouched.
    assert len(_ok(client.get(f"/api/packing-list-templates/{overnight['id']}"))["items"]) == 3

    # Taking off its last template keeps the lead time it had, as its own.
    alone = _ok(client.put(base, json={"packing_list_template_ids": []}))
    assert (alone["templates"], alone["pack_days_before"], alone["pack_days_before_own"]) == (
        [],
        2,
        2,
    )
    assert _names(alone) == ["Rain jacket"]


def test_an_unknown_template_is_refused(client):
    response = client.post(
        "/api/packing-list-days",
        json={"name": TRIP, "packing_list_template_ids": [99], "date": THURSDAY},
    )
    assert response.status_code == 422


def test_a_bare_template_makes_a_list_named_after_it(client, overnight):
    day = _ok(
        client.post(
            "/api/packing-list-days",
            json={"packing_list_template_id": overnight["id"], "date": THURSDAY},
        ),
        201,
    )
    assert (day["name"], [t["id"] for t in day["templates"]]) == (
        "Dad's Overnight Bag",
        [overnight["id"]],
    )


def test_renaming_a_template_renames_lists_named_after_it_only(client, overnight):
    named_after = _list(client, "Dad's Overnight Bag", [overnight["id"]])
    own_name = _list(client, TRIP, [overnight["id"]])
    _ok(
        client.put(
            f"/api/packing-list-templates/{overnight['id']}", json={"name": "Dad's Weekender"}
        )
    )
    assert _get(client, named_after["id"])["name"] == "Dad's Weekender"
    assert _get(client, own_name["id"])["name"] == TRIP


def test_per_trip_edits_to_template_items_and_resync_across_every_template(
    client, overnight, work_kit
):
    day = _list(client, TRIP, [overnight["id"], work_kit["id"]])
    base = f"/api/packing-list-days/{day['id']}"
    _own(client, day["id"], "Rain jacket")
    pajamas = overnight["items"][2]["id"]
    laptop = work_kit["items"][0]["id"]
    _ok(client.put(f"{base}/template-items/{pajamas}", json={"name": "Shorts"}))
    drifted = _ok(client.delete(f"{base}/template-items/{laptop}"))
    assert drifted["changed_count"] == 2
    assert [(i["name"], i["changed"]) for i in drifted["items"] if i["changed"]] == [
        ("Shorts", True)
    ]
    assert "Laptop" not in _names(drifted)
    # Neither template changed.
    assert [
        i["name"]
        for i in _ok(client.get(f"/api/packing-list-templates/{overnight['id']}"))["items"]
    ] == ["Toothbrush", "Phone charger", "Pajamas"]

    resynced = _ok(client.post(f"{base}/resync", json={"scope": "items"}))
    assert resynced["changed_count"] == 0
    assert _names(resynced) == [
        "Toothbrush",
        "Phone charger",
        "Pajamas",
        "Laptop",
        "Phone charger",
        "Rain jacket",
    ]


def test_bags_read_through_every_included_template(client, overnight, work_kit):
    suitcase = _ok(client.post("/api/packing-list-bags", json={"name": "Suitcase"}), 201)
    item = work_kit["items"][0]
    _ok(
        client.put(
            f"/api/packing-list-templates/{work_kit['id']}/items/{item['id']}",
            json={"bag_id": suitcase["id"]},
        )
    )
    day = _list(client, TRIP, [overnight["id"], work_kit["id"]])
    assert [b["name"] for b in day["bags"]] == ["Suitcase"]


def test_the_summary_reads_a_named_list_like_any_other(client, db_session, overnight, work_kit):
    day = _list(client, TRIP, [overnight["id"], work_kit["id"]], day=SATURDAY)
    _own(client, day["id"], "Rain jacket")
    text = logic.summary_text(db_session, datetime(2026, 10, 8).date())
    assert f'- "{TRIP}" for Saturday, October 10, packed 2 days before' in text
    assert "0 of 6 packed." in text
    assert text.count("- Phone charger") == 2
    assert "- Rain jacket" in text


def test_a_schedule_still_puts_a_single_template_on_a_day(client, db_session, overnight):
    _ok(
        client.post(
            "/api/packing-list-template-schedules",
            json={"packing_list_template_id": overnight["id"], "recurrence_type": "daily"},
        ),
        201,
    )
    days = _days(client)
    assert days
    assert {(d["name"], tuple(t["id"] for t in d["templates"])) for d in days} == {
        ("Dad's Overnight Bag", (overnight["id"],))
    }


def test_a_schedule_skips_a_date_a_list_already_includes_the_template_on(
    client, db_session, overnight
):
    trip = _list(client, TRIP, [overnight["id"]], day=TODAY)
    _ok(
        client.post(
            "/api/packing-list-template-schedules",
            json={"packing_list_template_id": overnight["id"], "recurrence_type": "daily"},
        ),
        201,
    )
    on_today = [d for d in _days(client) if d["date"] == TODAY]
    assert [d["id"] for d in on_today] == [trip["id"]]


def test_an_event_list_with_no_name_takes_its_first_templates(client, overnight, work_kit):
    event = _ok(
        client.post(
            "/api/events",
            json={
                "title": "Trip",
                "all_day": True,
                "start": SATURDAY,
                "packing_lists": [{"packing_list_template_ids": [work_kit["id"], overnight["id"]]}],
            },
        ),
        201,
    )
    assert [p["name"] for p in event["packing_lists"]] == ["Dad's Work Kit"]
    response = client.post(
        "/api/events",
        json={
            "title": "Trip",
            "all_day": True,
            "start": SATURDAY,
            "packing_lists": [{"name": "  "}],
        },
    )
    assert response.status_code == 422


def test_an_event_keeps_its_list_when_the_list_is_renamed_on_its_day(client, overnight):
    event = _ok(
        client.post(
            "/api/events",
            json={
                "title": "Trip",
                "all_day": True,
                "start": SATURDAY,
                "packing_lists": [{"name": TRIP, "packing_list_template_ids": [overnight["id"]]}],
            },
        ),
        201,
    )
    (day,) = _days(client)
    _ok(client.put(f"/api/packing-list-days/{day['id']}", json={"name": "SC trip"}))
    occurrence = _ok(client.get("/api/events", params={"start": SATURDAY, "end": SATURDAY}))[
        "occurrences"
    ][0]
    (listed,) = occurrence["packing_lists"]
    assert listed["name"] == "SC trip"

    # Sending it back by key (as the calendar does) keeps it the same list.
    _ok(
        client.put(
            f"/api/events/{event['id']}",
            json={
                "title": "Trip!",
                "packing_lists": [
                    {
                        "key": listed["key"],
                        "name": TRIP,
                        "packing_list_template_ids": [overnight["id"]],
                    }
                ],
            },
        )
    )
    assert [(d["id"], d["name"]) for d in _days(client)] == [(day["id"], "SC trip")]


def test_an_event_adopts_a_list_of_that_name_and_includes_its_templates(
    client, overnight, work_kit
):
    existing = _list(client, TRIP, [overnight["id"]], day=SATURDAY)
    response = client.post(
        "/api/events",
        json={
            "title": "Trip",
            "all_day": True,
            "start": SATURDAY,
            "packing_lists": [{"name": TRIP, "packing_list_template_ids": [work_kit["id"]]}],
        },
    )
    assert response.status_code == 409
    assert response.json()["detail"]["adopts"][0]["name"] == TRIP
    _ok(
        client.post(
            "/api/events?adopt=true",
            json={
                "title": "Trip",
                "all_day": True,
                "start": SATURDAY,
                "packing_lists": [{"name": TRIP, "packing_list_template_ids": [work_kit["id"]]}],
            },
        ),
        201,
    )
    (day,) = _days(client)
    assert day["id"] == existing["id"]
    assert [t["name"] for t in day["templates"]] == ["Dad's Overnight Bag", "Dad's Work Kit"]


def test_deleting_a_template_drops_it_from_an_event_list_that_has_others(
    client, db_session, overnight, work_kit
):
    event = _ok(
        client.post(
            "/api/events",
            json={
                "title": "Trip",
                "all_day": True,
                "start": SATURDAY,
                "packing_lists": [
                    {"name": TRIP, "packing_list_template_ids": [overnight["id"], work_kit["id"]]}
                ],
            },
        ),
        201,
    )
    client.delete(f"/api/packing-list-templates/{overnight['id']}")
    refreshed = _ok(client.get(f"/api/events/{event['id']}"))
    assert refreshed["packing_lists"][0]["packing_list_template_ids"] == [work_kit["id"]]
    (day,) = _days(client)
    # Still the event's list, and it still moves with it.
    assert [e["event_id"] for e in day["events"]] == [event["id"]]
    _ok(client.put(f"/api/events/{event['id']}", json={"start": SUNDAY, "end": SUNDAY}))
    assert [d["date"] for d in _days(client)] == [SUNDAY]


def test_the_archive_finds_a_list_by_a_template_it_includes(
    client, db_session, make_packing_day, overnight
):
    make_packing_day("2026-09-30", [overnight["id"]], name=TRIP, commit=True)
    page = _ok(client.get("/api/packing-list-days/previous?search=overnight"))
    assert [d["name"] for d in page["items"]] == [TRIP]


def test_deleting_a_list_takes_its_includes_with_it(client, db_session, overnight):
    day = _list(client, TRIP, [overnight["id"]])
    assert client.delete(f"/api/packing-list-days/{day['id']}").status_code == 204
    assert db_session.query(PackingListDayTemplate).count() == 0
    assert db_session.query(PackingListDay).count() == 0
    assert len(_ok(client.get(f"/api/packing-list-templates/{overnight['id']}"))["items"]) == 3
