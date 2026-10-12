"""Packing lists on calendar events (#270).

An event brings packing list templates, and the days it puts them on move and
go with it. What is pinned here is ``rally.packing_lists.sync_event_packing_lists``
through the API: creating, adopting a day already there, moving (with checks),
the adopt confirmation, a day shared by two events, deleting at every scope,
taking a list off on the Packing Lists page, and never touching a past day.
"""

from datetime import UTC, datetime

import pytest

from rally import packing_lists as logic
from rally.models import (
    EventPackingList,
    PackingListDay,
    PackingListDayCheck,
    PackingListDayEvent,
)

# Thursday, 8 October 2026, mid-morning in Chicago. The lookahead runs through
# Thursday the 15th.
NOW = datetime(2026, 10, 8, 15, 0, tzinfo=UTC)
SATURDAY = "2026-10-10"
NEXT_SATURDAY = "2026-10-17"


@pytest.fixture(autouse=True)
def _frozen(frozen_now, local_timezone):
    local_timezone("America/Chicago")
    frozen_now(NOW)


def _template(client, name, items=()):
    response = client.post("/api/packing-list-templates", json={"name": name})
    assert response.status_code == 201, response.text
    template = response.json()
    for item in items:
        client.post(f"/api/packing-list-templates/{template['id']}/items", json={"name": item})
    return template


@pytest.fixture
def beach(client):
    return _template(client, "Beach day", ["Sunscreen", "Towels"])


@pytest.fixture
def nana(client):
    return _template(client, "Nana overnight", ["Pajamas"])


def _event(client, **payload):
    body = {"title": "Trip", "all_day": True, "start": SATURDAY, "end": SATURDAY}
    body.update(payload)
    response = client.post("/api/events", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def _days(client):
    return client.get("/api/packing-list-days").json()


def _on(client, name):
    return [(d["date"], d["name"]) for d in _days(client) if d["name"] == name]


def _put(client, event_id, query="", **payload):
    return client.put(f"/api/events/{event_id}{query}", json=payload)


# --- Creating ------------------------------------------------------------------


def test_saving_an_event_puts_its_lists_on_its_day(client, beach, nana):
    event = _event(
        client,
        title="Nana's house overnight and beach day",
        packing_list_template_ids=[beach["id"], nana["id"]],
    )
    assert event["packing_list_template_ids"] == [beach["id"], nana["id"]]

    days = _days(client)
    assert sorted((d["date"], d["name"]) for d in days) == [
        (SATURDAY, "Beach day"),
        (SATURDAY, "Nana overnight"),
    ]
    for day in days:
        assert day["events"] == [
            {
                "event_id": event["id"],
                "title": "Nana's house overnight and beach day",
                "occurrence_date": SATURDAY,
            }
        ]
        # Kept in sync with the template, following its lead time.
        assert day["packing_list_template_id"] is not None
        assert day["label"] is None


def test_a_far_off_one_time_event_gets_its_lists_at_once(client, beach):
    _event(client, start="2026-12-24", end="2026-12-24", packing_list_template_ids=[beach["id"]])
    assert _on(client, "Beach day") == [("2026-12-24", "Beach day")]


def test_the_occurrence_names_its_lists(client, beach, nana):
    event = _event(client, packing_list_template_ids=[nana["id"], beach["id"]])
    occurrence = client.get("/api/events", params={"start": SATURDAY, "end": "2026-10-11"}).json()[
        "occurrences"
    ][0]
    assert [p["name"] for p in occurrence["packing_lists"]] == ["Beach day", "Nana overnight"]
    assert all(p["day_ids"] for p in occurrence["packing_lists"])
    assert occurrence["event_id"] == event["id"]


def test_an_unknown_template_is_refused(client):
    response = client.post(
        "/api/events",
        json={
            "title": "Trip",
            "start": SATURDAY,
            "all_day": True,
            "packing_list_template_ids": [99],
        },
    )
    assert response.status_code == 422


def test_saving_adopts_a_day_already_there_with_its_checks(client, beach, db_session):
    day = client.post(
        "/api/packing-list-days", json={"packing_list_template_id": beach["id"], "date": SATURDAY}
    ).json()
    item_id = day["items"][0]["id"]
    client.put(
        f"/api/packing-list-days/{day['id']}/template-items/{item_id}", json={"checked": True}
    )

    _event(client, packing_list_template_ids=[beach["id"]])

    days = _days(client)
    assert [d["id"] for d in days] == [day["id"]]
    assert days[0]["checked"] == 1
    assert len(days[0]["events"]) == 1


def test_every_day_puts_the_list_on_each_date(client, beach):
    _event(
        client,
        start=SATURDAY,
        end="2026-10-12",
        packing_list_span="every",
        packing_list_template_ids=[beach["id"]],
    )
    assert [date for date, _ in _on(client, "Beach day")] == [SATURDAY, "2026-10-11", "2026-10-12"]


def test_first_day_puts_a_multi_day_event_on_its_start(client, beach):
    _event(
        client,
        start=SATURDAY,
        end="2026-10-11",
        packing_list_span="first",
        packing_list_template_ids=[beach["id"]],
    )
    assert _on(client, "Beach day") == [(SATURDAY, "Beach day")]


# --- Moving --------------------------------------------------------------------


def test_moving_the_event_moves_the_day_and_its_checks(client, beach):
    event = _event(client, packing_list_template_ids=[beach["id"]])
    day = _days(client)[0]
    item_id = day["items"][0]["id"]
    client.put(
        f"/api/packing-list-days/{day['id']}/template-items/{item_id}", json={"checked": True}
    )

    response = _put(client, event["id"], start=NEXT_SATURDAY, end=NEXT_SATURDAY, all_day=True)
    assert response.status_code == 200, response.text

    days = _days(client)
    assert [(d["id"], d["date"], d["checked"]) for d in days] == [(day["id"], NEXT_SATURDAY, 1)]


def test_moving_onto_a_day_already_there_asks_first(client, beach, db_session):
    event = _event(client, packing_list_template_ids=[beach["id"]])
    moving = _days(client)[0]
    client.post(f"/api/packing-list-days/{moving['id']}/check-all")
    there = client.post(
        "/api/packing-list-days",
        json={"packing_list_template_id": beach["id"], "date": NEXT_SATURDAY},
    ).json()

    response = _put(client, event["id"], start=NEXT_SATURDAY, end=NEXT_SATURDAY, all_day=True)
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["message"] == (
        '"Beach day" is already on Saturday, October 17. This event will use that list '
        "instead, and what was checked on Saturday, October 10 will be lost. Continue?"
    )
    assert detail["adopts"][0]["checked"] is True
    # Nothing was written: the event and its day are where they were.
    assert client.get(f"/api/events/{event['id']}").json()["start_date"] == SATURDAY
    assert {d["id"] for d in _days(client)} == {moving["id"], there["id"]}

    response = _put(
        client, event["id"], "?adopt=true", start=NEXT_SATURDAY, end=NEXT_SATURDAY, all_day=True
    )
    assert response.status_code == 200, response.text
    days = _days(client)
    assert [(d["id"], d["date"], d["checked"]) for d in days] == [(there["id"], NEXT_SATURDAY, 0)]
    assert days[0]["events"][0]["event_id"] == event["id"]


def test_a_shared_day_stays_with_the_other_event(client, beach):
    beach_trip = _event(client, title="Beach trip", packing_list_template_ids=[beach["id"]])
    nana_trip = _event(client, title="Nana overnight", packing_list_template_ids=[beach["id"]])
    shared = _days(client)[0]
    assert [e["title"] for e in shared["events"]] == ["Beach trip", "Nana overnight"]
    client.post(f"/api/packing-list-days/{shared['id']}/check-all")

    response = _put(client, nana_trip["id"], start=NEXT_SATURDAY, end=NEXT_SATURDAY, all_day=True)
    assert response.status_code == 200, response.text

    days = {d["date"]: d for d in _days(client)}
    assert days[SATURDAY]["id"] == shared["id"]
    assert days[SATURDAY]["checked"] == days[SATURDAY]["total"]
    assert [e["event_id"] for e in days[SATURDAY]["events"]] == [beach_trip["id"]]
    assert days[NEXT_SATURDAY]["checked"] == 0
    assert [e["event_id"] for e in days[NEXT_SATURDAY]["events"]] == [nana_trip["id"]]


def test_moving_into_the_past_is_refused_while_it_has_lists(client, beach):
    event = _event(client, packing_list_template_ids=[beach["id"]])
    response = _put(client, event["id"], start="2026-10-01", end="2026-10-01", all_day=True)
    assert response.status_code == 422
    assert response.json()["detail"] == (
        "This event has packing lists. Remove them before moving it to a day that has passed."
    )
    assert client.get(f"/api/events/{event['id']}").json()["start_date"] == SATURDAY
    assert _on(client, "Beach day") == [(SATURDAY, "Beach day")]

    # Taking the lists off first lets it move.
    response = _put(
        client,
        event["id"],
        start="2026-10-01",
        end="2026-10-01",
        all_day=True,
        packing_list_template_ids=[],
    )
    assert response.status_code == 200, response.text
    assert _days(client) == []


def test_removing_a_list_in_the_modal_takes_it_off(client, beach, nana):
    event = _event(client, packing_list_template_ids=[beach["id"], nana["id"]])
    _put(client, event["id"], packing_list_template_ids=[nana["id"]])
    assert [d["name"] for d in _days(client)] == ["Nana overnight"]


def test_switching_to_first_day_lets_the_extra_dates_go(client, beach):
    event = _event(
        client,
        start=SATURDAY,
        end="2026-10-12",
        packing_list_span="every",
        packing_list_template_ids=[beach["id"]],
    )
    _put(client, event["id"], packing_list_span="first")
    assert _on(client, "Beach day") == [(SATURDAY, "Beach day")]


# --- Deleting ------------------------------------------------------------------


def test_deleting_the_event_takes_its_lists_off(client, beach, nana):
    event = _event(client, packing_list_template_ids=[beach["id"], nana["id"]])
    assert client.delete(f"/api/events/{event['id']}").status_code == 204
    assert _days(client) == []


def test_deleting_takes_an_adopted_day_too(client, beach):
    client.post(
        "/api/packing-list-days", json={"packing_list_template_id": beach["id"], "date": SATURDAY}
    )
    event = _event(client, packing_list_template_ids=[beach["id"]])
    client.delete(f"/api/events/{event['id']}")
    assert _days(client) == []


def test_a_day_another_event_still_links_survives_a_delete(client, beach):
    first = _event(client, title="Beach trip", packing_list_template_ids=[beach["id"]])
    _event(client, title="Nana overnight", packing_list_template_ids=[beach["id"]])
    client.delete(f"/api/events/{first['id']}")
    days = _days(client)
    assert [e["title"] for e in days[0]["events"]] == ["Nana overnight"]


def test_past_days_stay_when_the_event_goes(client, beach, frozen_now, db_session):
    event = _event(client, packing_list_template_ids=[beach["id"]])
    day_id = _days(client)[0]["id"]
    frozen_now(datetime(2026, 10, 12, 15, 0, tzinfo=UTC))

    client.delete(f"/api/events/{event['id']}")
    day = db_session.get(PackingListDay, day_id)
    assert day is not None and day.date == SATURDAY
    assert db_session.query(PackingListDayEvent).count() == 0
    assert db_session.query(EventPackingList).count() == 0


# --- Repeating events -----------------------------------------------------------

SCHOOL_DAYS = ["2026-10-08", "2026-10-09", "2026-10-12", "2026-10-13", "2026-10-14", "2026-10-15"]


@pytest.fixture
def backpack(client):
    return _template(client, "School backpack", ["Lunch"])


@pytest.fixture
def gym(client):
    return _template(client, "Gym bag", ["Sneakers"])


@pytest.fixture
def drop_off(client, backpack):
    return _event(
        client,
        title="School drop-off",
        all_day=False,
        start="2026-10-08T07:30",
        end="2026-10-08T08:00",
        rrule="FREQ=DAILY;BYDAY=MO,TU,WE,TH,FR",
        packing_list_template_ids=[backpack["id"]],
    )


def test_a_repeating_event_fills_the_lookahead(client, drop_off):
    assert [date for date, _ in _on(client, "School backpack")] == SCHOOL_DAYS


def test_the_lookahead_moves_forward_each_day(client, drop_off, frozen_now):
    frozen_now(datetime(2026, 10, 9, 15, 0, tzinfo=UTC))
    assert _on(client, "School backpack")[-1] == ("2026-10-16", "School backpack")


def test_a_repeating_event_far_ahead_has_its_next_occurrence(client, backpack):
    _event(
        client,
        title="Monthly swim",
        start="2026-11-05",
        end="2026-11-05",
        rrule="FREQ=MONTHLY",
        packing_list_template_ids=[backpack["id"]],
    )
    assert _on(client, "School backpack") == [("2026-11-05", "School backpack")]


def test_deleting_one_occurrence_takes_its_list_off(client, drop_off):
    response = client.delete(
        f"/api/events/{drop_off['id']}", params={"scope": "this", "occurrence_date": "2026-10-09"}
    )
    assert response.status_code == 204
    dates = [date for date, _ in _on(client, "School backpack")]
    assert "2026-10-09" not in dates
    assert "2026-10-12" in dates


def test_one_occurrence_adds_and_drops_a_list(client, drop_off, backpack, gym):
    tuesday = "2026-10-13"
    response = _put(
        client,
        drop_off["id"],
        f"?scope=this&occurrence_date={tuesday}",
        packing_list_template_ids=[backpack["id"], gym["id"]],
    )
    assert response.status_code == 200, response.text
    assert _on(client, "Gym bag") == [(tuesday, "Gym bag")]

    # Field Day moves: the gym bag comes off Tuesday, the backpack stays.
    _put(
        client,
        drop_off["id"],
        f"?scope=this&occurrence_date={tuesday}",
        packing_list_template_ids=[backpack["id"]],
    )
    assert _on(client, "Gym bag") == []
    assert (tuesday, "School backpack") in _on(client, "School backpack")


def test_a_series_edit_keeps_an_occurrences_own_list(client, drop_off, backpack, gym):
    rain = _template(client, "Rain jacket")
    tuesday = "2026-10-13"
    _put(
        client,
        drop_off["id"],
        f"?scope=this&occurrence_date={tuesday}",
        packing_list_template_ids=[backpack["id"], gym["id"]],
    )
    _put(client, drop_off["id"], packing_list_template_ids=[backpack["id"], rain["id"]])
    assert _on(client, "Gym bag") == [(tuesday, "Gym bag")]
    assert len(_on(client, "Rain jacket")) == len(SCHOOL_DAYS)


def test_this_and_following_carries_the_lists_to_the_tail(client, drop_off, gym, backpack):
    response = _put(
        client,
        drop_off["id"],
        "?scope=following&occurrence_date=2026-10-12",
        packing_list_template_ids=[backpack["id"], gym["id"]],
    )
    assert response.status_code == 200, response.text
    tail = response.json()
    assert tail["packing_list_template_ids"] == [backpack["id"], gym["id"]]
    assert [date for date, _ in _on(client, "Gym bag")] == SCHOOL_DAYS[2:]
    by_date = {d["date"]: d for d in _days(client) if d["name"] == "School backpack"}
    assert by_date["2026-10-09"]["events"][0]["event_id"] == drop_off["id"]
    assert by_date["2026-10-12"]["events"][0]["event_id"] == tail["id"]


def test_deleting_this_and_following_takes_the_rest_off(client, drop_off):
    client.delete(
        f"/api/events/{drop_off['id']}",
        params={"scope": "following", "occurrence_date": "2026-10-12"},
    )
    assert [date for date, _ in _on(client, "School backpack")] == SCHOOL_DAYS[:2]


# --- From the Packing Lists page -------------------------------------------------


def test_an_event_linked_day_cannot_change_date(client, beach):
    _event(
        client,
        title="Nana's house overnight and beach day",
        packing_list_template_ids=[beach["id"]],
    )
    day = _days(client)[0]
    response = client.put(f"/api/packing-list-days/{day['id']}", json={"date": NEXT_SATURDAY})
    assert response.status_code == 422
    assert response.json()["detail"] == (
        'This packing list moves with "Nana\'s house overnight and beach day". '
        "Change the date on the calendar."
    )
    # Its label and lead time are still its own.
    response = client.put(f"/api/packing-list-days/{day['id']}", json={"label": "Emma"})
    assert response.status_code == 200


def test_removing_the_day_takes_the_list_off_the_occurrence(client, drop_off):
    friday = next(d for d in _days(client) if d["date"] == "2026-10-09")
    assert client.delete(f"/api/packing-list-days/{friday['id']}").status_code == 204

    # The repeating event does not put it back, and Friday no longer lists it.
    dates = [date for date, _ in _on(client, "School backpack")]
    assert "2026-10-09" not in dates
    occurrence = next(
        o
        for o in client.get(
            "/api/events", params={"start": "2026-10-09", "end": "2026-10-10"}
        ).json()["occurrences"]
    )
    assert occurrence["packing_lists"] == []


def test_removing_a_one_time_events_day_drops_the_list(client, beach):
    event = _event(client, packing_list_template_ids=[beach["id"]])
    client.delete(f"/api/packing-list-days/{_days(client)[0]['id']}")
    assert client.get(f"/api/events/{event['id']}").json()["packing_list_template_ids"] == []
    assert _days(client) == []


def test_removing_one_date_of_every_day_keeps_the_others(client, beach):
    event = _event(
        client,
        start=SATURDAY,
        end="2026-10-12",
        packing_list_span="every",
        packing_list_template_ids=[beach["id"]],
    )
    sunday = next(d for d in _days(client) if d["date"] == "2026-10-11")
    client.delete(f"/api/packing-list-days/{sunday['id']}")

    assert [date for date, _ in _on(client, "Beach day")] == [SATURDAY, "2026-10-12"]
    # Syncing again leaves Sunday empty, and the event still lists Beach day.
    _put(client, event["id"], title="Trip again")
    assert [date for date, _ in _on(client, "Beach day")] == [SATURDAY, "2026-10-12"]
    assert client.get(f"/api/events/{event['id']}").json()["packing_list_template_ids"] == [
        beach["id"]
    ]


def test_deleting_the_template_unlinks_its_days(client, beach, db_session):
    event = _event(client, packing_list_template_ids=[beach["id"]])
    client.delete(f"/api/packing-list-templates/{beach['id']}")

    days = _days(client)
    assert len(days) == 1
    assert days[0]["packing_list_template_id"] is None
    assert days[0]["events"] == []
    assert client.get(f"/api/events/{event['id']}").json()["packing_list_template_ids"] == []

    # The day no longer moves with the event.
    _put(client, event["id"], start=NEXT_SATURDAY, end=NEXT_SATURDAY, all_day=True)
    assert [d["date"] for d in _days(client)] == [SATURDAY]


def test_a_day_with_checks_kept_by_its_template_check(client, beach, db_session):
    """A sanity check that the checks the move test relies on are real rows."""
    _event(client, packing_list_template_ids=[beach["id"]])
    day = _days(client)[0]
    client.post(f"/api/packing-list-days/{day['id']}/check-all")
    assert db_session.query(PackingListDayCheck).filter_by(day_id=day["id"]).count() == 2


# --- Wording -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("names", "expected"),
    [
        (["A"], '"A"'),
        (["A", "B"], '"A" and "B"'),
        (["A", "B", "C"], '"A", "B", and "C"'),
    ],
)
def test_names_are_quoted_with_the_oxford_comma(names, expected):
    assert logic.join_names(names) == expected
