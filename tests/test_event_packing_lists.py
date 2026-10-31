"""Packing lists on calendar events (#270).

An event brings packing list templates, and the days it puts them on move and
go with it. What is pinned here is ``rally.packing_lists.sync_event_packing_lists``
through the API: creating, adopting a day already there, moving (with checks),
the adopt confirmation, a day shared by two events, deleting at every scope,
taking a list off on the Packing Lists page, and never touching a past day.
"""

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import pytest

from rally import packing_lists as logic
from rally.models import (
    Event,
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


def _event(client, adopt=False, **payload):
    """Create an event. ``adopt`` answers yes to joining a day already there
    (#291), the way the calendar does after asking."""
    body = {"title": "Trip", "all_day": True, "start": SATURDAY, "end": SATURDAY}
    body.update(payload)
    response = client.post("/api/events?adopt=true" if adopt else "/api/events", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def _days(client):
    return client.get("/api/packing-list-days").json()


def _brings(event):
    """The templates an event's own lists include, in order (#281: each list
    names its templates; a bare template is a list of its own)."""
    return [tid for item in event["packing_lists"] for tid in item["packing_list_template_ids"]]


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
    assert _brings(event) == [beach["id"], nana["id"]]
    assert [item["name"] for item in event["packing_lists"]] == ["Beach day", "Nana overnight"]

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
        assert len(day["templates"]) == 1
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

    # Joining a day already there is asked first (#291), and nothing is
    # written until it is answered.
    body = {"title": "Trip", "all_day": True, "start": SATURDAY, "end": SATURDAY}
    response = client.post("/api/events", json={**body, "packing_list_template_ids": [beach["id"]]})
    assert response.status_code == 409
    assert response.json()["detail"]["message"] == (
        '"Beach day" is already on Saturday, Oct 10, 2026. This event will use that list. Continue?'
    )
    assert (
        client.get("/api/events", params={"start": SATURDAY, "end": SATURDAY}).json()["occurrences"]
        == []
    )

    response = client.post(
        "/api/events?adopt=true", json={**body, "packing_list_template_ids": [beach["id"]]}
    )
    assert response.status_code == 201

    days = _days(client)
    assert [d["id"] for d in days] == [day["id"]]
    assert days[0]["checked"] == 1
    assert len(days[0]["events"]) == 1


def test_a_three_day_event_puts_its_list_on_its_first_day_only(client, beach):
    """#281: a multi-day event is one ongoing event, not a trip each day.
    `Put on` is gone, and an old client's `packing_list_span` is ignored."""
    _event(
        client,
        start=SATURDAY,
        end="2026-10-12",
        packing_list_span="every",
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
        '"Beach day" is already on Saturday, Oct 17, 2026. This event will use that '
        "list instead, and what was checked on Saturday, Oct 10, 2026 will be lost. Continue?"
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
    nana_trip = _event(
        client, adopt=True, title="Nana overnight", packing_list_template_ids=[beach["id"]]
    )
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
        "?release=remove",
        start="2026-10-01",
        end="2026-10-01",
        all_day=True,
        packing_list_template_ids=[],
    )
    assert response.status_code == 200, response.text
    assert _days(client) == []


def test_removing_a_list_in_the_modal_takes_it_off(client, beach, nana):
    event = _event(client, packing_list_template_ids=[beach["id"], nana["id"]])
    _put(client, event["id"], "?release=remove", packing_list_template_ids=[nana["id"]])
    assert [d["name"] for d in _days(client)] == ["Nana overnight"]


# --- Deleting ------------------------------------------------------------------


def test_deleting_the_event_takes_its_lists_off(client, beach, nana):
    event = _event(client, packing_list_template_ids=[beach["id"], nana["id"]])
    response = client.delete(f"/api/events/{event['id']}", params={"release": "remove"})
    assert response.status_code == 204
    assert _days(client) == []


def test_deleting_takes_an_adopted_day_too(client, beach):
    client.post(
        "/api/packing-list-days", json={"packing_list_template_id": beach["id"], "date": SATURDAY}
    )
    event = _event(client, adopt=True, packing_list_template_ids=[beach["id"]])
    client.delete(f"/api/events/{event['id']}", params={"release": "remove"})
    assert _days(client) == []


def test_a_day_another_event_still_links_survives_a_delete(client, beach):
    first = _event(client, title="Beach trip", packing_list_template_ids=[beach["id"]])
    _event(client, adopt=True, title="Nana overnight", packing_list_template_ids=[beach["id"]])
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
        f"/api/events/{drop_off['id']}",
        params={"scope": "this", "occurrence_date": "2026-10-09", "release": "remove"},
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
        f"?scope=this&occurrence_date={tuesday}&release=remove",
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
    assert _brings(tail) == [backpack["id"], gym["id"]]
    assert [date for date, _ in _on(client, "Gym bag")] == SCHOOL_DAYS[2:]
    by_date = {d["date"]: d for d in _days(client) if d["name"] == "School backpack"}
    assert by_date["2026-10-09"]["events"][0]["event_id"] == drop_off["id"]
    assert by_date["2026-10-12"]["events"][0]["event_id"] == tail["id"]


def test_deleting_this_and_following_takes_the_rest_off(client, drop_off):
    client.delete(
        f"/api/events/{drop_off['id']}",
        params={"scope": "following", "occurrence_date": "2026-10-12", "release": "remove"},
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
    assert _brings(client.get(f"/api/events/{event['id']}").json()) == []
    assert _days(client) == []


def test_deleting_the_template_unlinks_its_days(client, beach, db_session):
    event = _event(client, packing_list_template_ids=[beach["id"]])
    client.delete(f"/api/packing-list-templates/{beach['id']}")

    days = _days(client)
    assert len(days) == 1
    # The list stays, with the template's items as its own (#281).
    assert (days[0]["name"], days[0]["templates"]) == ("Beach day", [])
    assert [i["name"] for i in days[0]["items"]] == ["Sunscreen", "Towels"]
    assert days[0]["events"] == []
    assert _brings(client.get(f"/api/events/{event['id']}").json()) == []

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


# --- Asking before a newly added list joins a day (#291) ------------------------


def test_adding_a_list_on_a_day_it_is_already_on_asks_first(client, beach):
    event = _event(client)
    day = client.post(
        "/api/packing-list-days", json={"packing_list_template_id": beach["id"], "date": SATURDAY}
    ).json()

    response = _put(client, event["id"], packing_list_template_ids=[beach["id"]])
    assert response.status_code == 409
    detail = response.json()["detail"]
    # No "instead" and nothing lost: the event had no day of its own.
    assert detail["message"] == (
        '"Beach day" is already on Saturday, Oct 10, 2026. This event will use that list. Continue?'
    )
    assert detail["adopts"] == [
        {"name": "Beach day", "from_date": None, "to_date": SATURDAY, "checked": False}
    ]
    assert _brings(client.get(f"/api/events/{event['id']}").json()) == []
    assert _days(client)[0]["events"] == []

    response = _put(client, event["id"], "?adopt=true", packing_list_template_ids=[beach["id"]])
    assert response.status_code == 200
    days = _days(client)
    assert [d["id"] for d in days] == [day["id"]]
    assert [e["event_id"] for e in days[0]["events"]] == [event["id"]]


def test_adding_a_list_and_moving_in_one_save_asks_first(client, beach):
    event = _event(client)
    client.post(
        "/api/packing-list-days",
        json={"packing_list_template_id": beach["id"], "date": NEXT_SATURDAY},
    )

    response = _put(
        client,
        event["id"],
        start=NEXT_SATURDAY,
        end=NEXT_SATURDAY,
        all_day=True,
        packing_list_template_ids=[beach["id"]],
    )
    assert response.status_code == 409
    assert response.json()["detail"]["adopts"][0]["to_date"] == NEXT_SATURDAY
    # Nothing was written: the event is still on its old date.
    assert client.get(f"/api/events/{event['id']}").json()["start_date"] == SATURDAY


def test_an_occurrence_coming_into_view_joins_a_day_without_asking(
    client, beach, frozen_now, db_session
):
    """Only a list newly added to the event asks. A series that already brings
    the list, whose next occurrence lands on a day already there because time
    has passed, joins it silently: on an unrelated edit and in the daily sync."""
    event = _event(client, rrule="FREQ=WEEKLY;BYDAY=SA", packing_list_template_ids=[beach["id"]])
    later = "2026-10-24"
    there = client.post(
        "/api/packing-list-days", json={"packing_list_template_id": beach["id"], "date": later}
    ).json()

    # By the 18th, the 24th is inside the seven-day lookahead.
    frozen_now(datetime(2026, 10, 18, 15, 0, tzinfo=UTC))
    response = _put(client, event["id"], title="Saturday swim")
    assert response.status_code == 200, response.text
    joined = next(d for d in _days(client) if d["date"] == later)
    assert joined["id"] == there["id"]
    assert [e["event_id"] for e in joined["events"]] == [event["id"]]

    # The daily sync never asks either.
    assert (
        logic.process_event_packing_lists(
            db_session, date(2026, 10, 18), ZoneInfo("America/Chicago")
        )
        >= 1
    )


# --- Refusing a list added to the past (#290) -----------------------------------

PAST_SATURDAY = "2026-10-03"
ADDED_TO_PAST = "This event has already passed, so a packing list can't be added to it."


def test_adding_a_list_to_a_past_event_is_refused(client, beach):
    event = _event(client, start=PAST_SATURDAY, end=PAST_SATURDAY)
    response = _put(client, event["id"], packing_list_template_ids=[beach["id"]])
    assert response.status_code == 422
    assert response.json()["detail"] == ADDED_TO_PAST
    # Nothing was stored, and no day was made.
    assert _brings(client.get(f"/api/events/{event['id']}").json()) == []
    assert _days(client) == []


def test_creating_a_past_event_with_a_list_is_refused(client, beach):
    body = {"title": "Trip", "all_day": True, "start": PAST_SATURDAY, "end": PAST_SATURDAY}
    response = client.post("/api/events", json={**body, "packing_list_template_ids": [beach["id"]]})
    assert response.status_code == 422
    assert response.json()["detail"] == ADDED_TO_PAST
    assert (
        client.get("/api/events", params={"start": PAST_SATURDAY, "end": PAST_SATURDAY}).json()[
            "occurrences"
        ]
        == []
    )


def test_adding_a_list_to_one_past_occurrence_is_refused(client, beach):
    series = _event(client, start="2026-09-26", end="2026-09-26", rrule="FREQ=WEEKLY;BYDAY=SA")
    response = _put(
        client,
        series["id"],
        f"?scope=this&occurrence_date={PAST_SATURDAY}",
        packing_list_template_ids=[beach["id"]],
    )
    assert response.status_code == 422
    assert response.json()["detail"] == ADDED_TO_PAST


def test_a_series_that_started_in_the_past_still_takes_a_list(client, beach):
    """Its occurrences from today on are where the list goes."""
    series = _event(client, start="2026-09-26", end="2026-09-26", rrule="FREQ=WEEKLY;BYDAY=SA")
    response = _put(client, series["id"], packing_list_template_ids=[beach["id"]])
    assert response.status_code == 200, response.text
    assert [date for date, _ in _on(client, "Beach day")][:1] == [SATURDAY]


def test_an_event_under_way_takes_no_new_list(client, beach):
    """The sync's own rule decides (#281): a list goes on the first day, and
    that has passed."""
    under_way = _event(client, start="2026-10-06", end="2026-10-10")
    response = _put(client, under_way["id"], packing_list_template_ids=[beach["id"]])
    assert response.status_code == 422
    assert response.json()["detail"] == ADDED_TO_PAST


def test_keeping_a_past_events_lists_is_not_adding_them(client, beach, frozen_now):
    """An event that brought a list while it was upcoming can still be edited
    after it has passed."""
    event = _event(client, packing_list_template_ids=[beach["id"]])
    frozen_now(datetime(2026, 10, 12, 15, 0, tzinfo=UTC))
    response = _put(
        client, event["id"], title="Beach trip", packing_list_template_ids=[beach["id"]]
    )
    assert response.status_code == 200, response.text


def test_a_far_off_event_or_series_still_takes_a_list(client, beach):
    """However far ahead it is, an event that has not happened has a day for
    the list."""
    far = "2029-06-02"
    one_time = _event(client, start=far, end=far)
    response = _put(client, one_time["id"], packing_list_template_ids=[beach["id"]])
    assert response.status_code == 200, response.text

    series = _event(client, start=far, end=far, rrule="FREQ=WEEKLY;BYDAY=SA")
    response = _put(client, series["id"], packing_list_template_ids=[beach["id"]])
    assert response.status_code == 200, response.text


# --- Keeping a list an event lets go (#313) --------------------------------------


def _check_first_item(client, day):
    item_id = day["items"][0]["id"]
    client.put(
        f"/api/packing-list-days/{day['id']}/template-items/{item_id}", json={"checked": True}
    )


def test_removing_a_list_on_a_day_asks_first(client, beach):
    event = _event(client, packing_list_template_ids=[beach["id"]])
    response = _put(client, event["id"], packing_list_template_ids=[])
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["message"] == '"Beach day" is on Saturday, Oct 10, 2026.'
    assert detail["keep_label"] == "Keep it on Saturday"
    assert detail["remove_label"] == "Remove it, with what was checked on it"
    assert [(r["name"], r["date"]) for r in detail["releases"]] == [("Beach day", SATURDAY)]
    # Nothing was written: the event still brings the list.
    lists = client.get(f"/api/events/{event['id']}").json()["packing_lists"]
    assert [p["packing_list_template_ids"] for p in lists] == [[beach["id"]]]
    assert [e["title"] for e in _days(client)[0]["events"]] == ["Trip"]


def test_keeping_leaves_an_ordinary_list_with_its_checks(client, beach):
    event = _event(client, packing_list_template_ids=[beach["id"]])
    day = _days(client)[0]
    _check_first_item(client, day)

    response = _put(client, event["id"], "?release=keep", packing_list_template_ids=[])
    assert response.status_code == 200, response.text
    days = _days(client)
    assert [(d["id"], d["date"], d["checked"], d["events"]) for d in days] == [
        (day["id"], SATURDAY, 1, [])
    ]
    # No longer the event's: its date is free to change.
    response = client.put(f"/api/packing-list-days/{day['id']}", json={"date": NEXT_SATURDAY})
    assert response.status_code == 200, response.text


def test_keeping_a_day_that_was_there_first(client, beach):
    """The demo's case: a list somebody (or a schedule) put on the day, joined
    by an event and then removed from it, can stay where it was."""
    day = client.post(
        "/api/packing-list-days", json={"packing_list_template_id": beach["id"], "date": SATURDAY}
    ).json()
    event = _event(client, adopt=True, packing_list_template_ids=[beach["id"]])
    response = _put(client, event["id"], "?release=keep", packing_list_template_ids=[])
    assert response.status_code == 200, response.text
    assert [(d["id"], d["events"]) for d in _days(client)] == [(day["id"], [])]


def test_a_shared_day_is_not_asked_about(client, beach):
    first = _event(client, title="Beach trip", packing_list_template_ids=[beach["id"]])
    _event(client, adopt=True, title="Nana overnight", packing_list_template_ids=[beach["id"]])
    response = _put(client, first["id"], packing_list_template_ids=[])
    assert response.status_code == 200, response.text
    assert [e["title"] for e in _days(client)[0]["events"]] == ["Nana overnight"]


def test_moving_an_event_does_not_ask(client, beach):
    event = _event(client, packing_list_template_ids=[beach["id"]])
    response = _put(client, event["id"], start=NEXT_SATURDAY, end=NEXT_SATURDAY, all_day=True)
    assert response.status_code == 200, response.text
    assert _on(client, "Beach day") == [(NEXT_SATURDAY, "Beach day")]


def test_joining_and_letting_go_in_one_save_is_one_question(client, beach, nana):
    client.post(
        "/api/packing-list-days", json={"packing_list_template_id": nana["id"], "date": SATURDAY}
    )
    event = _event(client, packing_list_template_ids=[beach["id"]])

    response = _put(client, event["id"], packing_list_template_ids=[nana["id"]])
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["message"] == (
        '"Nana overnight" is already on Saturday, Oct 10, 2026. This event will use that list. '
        '"Beach day" is on Saturday, Oct 10, 2026.'
    )
    assert detail["keep_label"] == 'Keep "Beach day" on Saturday'
    assert detail["remove_label"] == 'Remove "Beach day", with what was checked on it'
    assert [a["name"] for a in detail["adopts"]] == ["Nana overnight"]

    response = _put(
        client, event["id"], "?adopt=true&release=keep", packing_list_template_ids=[nana["id"]]
    )
    assert response.status_code == 200, response.text
    by_name = {d["name"]: [e["title"] for e in d["events"]] for d in _days(client)}
    assert by_name == {"Beach day": [], "Nana overnight": ["Trip"]}


def test_removing_from_one_occurrence_asks_about_that_day_only(client, drop_off, backpack):
    response = _put(
        client,
        drop_off["id"],
        "?scope=this&occurrence_date=2026-10-13",
        packing_list_template_ids=[],
    )
    assert response.status_code == 409
    assert [r["date"] for r in response.json()["detail"]["releases"]] == ["2026-10-13"]
    assert response.json()["detail"]["keep_label"] == "Keep it on Tuesday"


def test_removing_from_a_series_names_each_day(client, drop_off):
    response = _put(client, drop_off["id"], packing_list_template_ids=[])
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert [r["date"] for r in detail["releases"]] == SCHOOL_DAYS
    assert detail["keep_label"] == "Keep it on its days"

    response = _put(client, drop_off["id"], "?release=keep", packing_list_template_ids=[])
    assert response.status_code == 200, response.text
    assert [d for d, _ in _on(client, "School backpack")] == SCHOOL_DAYS
    assert all(d["events"] == [] for d in _days(client))


def test_deleting_an_event_asks_and_can_keep(client, beach, nana):
    event = _event(client, packing_list_template_ids=[beach["id"], nana["id"]])
    question = client.get(f"/api/events/{event['id']}/delete-question").json()
    assert question["message"] == '"Beach day" and "Nana overnight" are on Saturday, Oct 10, 2026.'
    assert question["keep_label"] == "Keep them on Saturday"
    assert question["remove_label"] == "Remove them, with what was checked on them"
    # Asking wrote nothing.
    assert client.get(f"/api/events/{event['id']}").status_code == 200

    assert client.delete(f"/api/events/{event['id']}").status_code == 409
    response = client.delete(f"/api/events/{event['id']}", params={"release": "keep"})
    assert response.status_code == 204
    assert sorted((d["name"], d["events"]) for d in _days(client)) == [
        ("Beach day", []),
        ("Nana overnight", []),
    ]


def test_deleting_with_nothing_on_a_day_asks_nothing(client):
    event = _event(client)
    question = client.get(f"/api/events/{event['id']}/delete-question").json()
    assert question["releases"] == []
    assert client.delete(f"/api/events/{event['id']}").status_code == 204


def test_deleting_a_series_names_its_lists_without_dates(client, drop_off, backpack, gym):
    tuesday = "2026-10-13"
    _put(
        client,
        drop_off["id"],
        f"?scope=this&occurrence_date={tuesday}",
        packing_list_template_ids=[backpack["id"], gym["id"]],
    )
    for scope, occurrence in (("all", None), ("following", "2026-10-12")):
        params = {"scope": scope}
        if occurrence:
            params["occurrence_date"] = occurrence
        question = client.get(f"/api/events/{drop_off['id']}/delete-question", params=params)
        detail = question.json()
        assert detail["message"] == '"School backpack" and "Gym bag" are on its upcoming days.', (
            scope
        )
        assert detail["keep_label"] == "Keep them on their days"

    response = client.delete(
        f"/api/events/{drop_off['id']}",
        params={"scope": "following", "occurrence_date": "2026-10-12", "release": "keep"},
    )
    assert response.status_code == 204
    assert [d for d, _ in _on(client, "School backpack")] == SCHOOL_DAYS
    assert _on(client, "Gym bag") == [(tuesday, "Gym bag")]


def test_deleting_one_occurrence_names_its_day(client, drop_off):
    question = client.get(
        f"/api/events/{drop_off['id']}/delete-question",
        params={"scope": "this", "occurrence_date": "2026-10-09"},
    ).json()
    assert question["message"] == '"School backpack" is on Friday, Oct 9, 2026.'
    assert question["keep_label"] == "Keep it on Friday"


def test_a_sync_nobody_asked_for_never_asks(client, beach, db_session):
    event = _event(client, packing_list_template_ids=[beach["id"]])
    db_session.query(EventPackingList).filter(EventPackingList.event_id == event["id"]).delete()
    db_session.commit()
    event_row = db_session.get(Event, event["id"])
    logic.sync_event_packing_lists(
        db_session, event_row, date(2026, 10, 8), tz=ZoneInfo("America/Chicago")
    )
    db_session.commit()
    assert _days(client) == []


def test_the_question_says_when_a_schedule_made_the_day(client, beach, nana, db_session):
    for template in (beach, nana):
        client.post(
            "/api/packing-list-days",
            json={"packing_list_template_id": template["id"], "date": SATURDAY},
        )
    db_session.query(PackingListDay).filter(PackingListDay.name == "Nana overnight").update(
        {PackingListDay.schedule_id: 1}
    )
    db_session.commit()

    event = _event(client, adopt=True, packing_list_template_ids=[nana["id"]])
    detail = _put(client, event["id"], packing_list_template_ids=[]).json()["detail"]
    assert detail["message"] == (
        '"Nana overnight" is on Saturday, Oct 10, 2026. '
        "Its schedule put it there, and won't put it back if it's removed."
    )
    assert [r["scheduled"] for r in detail["releases"]] == [True]

    # With several lists, the ones a schedule made are named.
    _put(client, event["id"], "?adopt=true", packing_list_template_ids=[beach["id"], nana["id"]])
    question = client.get(f"/api/events/{event['id']}/delete-question").json()
    assert question["message"] == (
        '"Beach day" and "Nana overnight" are on Saturday, Oct 10, 2026. '
        '"Nana overnight" was put there by its schedule.'
    )
