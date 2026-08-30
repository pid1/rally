"""Tests for the events API: CRUD, the three-scope edit matrix, and filtering.

The scope matrix is the part that matters most. Editing one occurrence of a
series is the interaction calendars get wrong, and getting it wrong loses data
— so every cell of edit/delete × this/following/all has a test, plus the two
rules that are easy to assume and easy to break: "all" keeps overrides, and the
split point is a date rather than an index.
"""

import pytest

pytestmark = pytest.mark.usefixtures("local_timezone")


@pytest.fixture(autouse=True)
def _tz(local_timezone):
    local_timezone("America/Chicago")


def _create(client, **overrides):
    payload = {"title": "Dentist", "start": "2026-08-11T09:00"}
    # Only supply the default end when the caller left the default start alone;
    # otherwise the two disagree and the API rightly rejects the pair.
    if "start" not in overrides and "end" not in overrides:
        payload["end"] = "2026-08-11T10:00"
    payload.update(overrides)
    response = client.post("/api/events", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def _occurrences(client, start="2026-08-01", end="2026-09-01", **params):
    response = client.get("/api/events", params={"start": start, "end": end, **params})
    assert response.status_code == 200, response.text
    return response.json()["occurrences"]


# --- Create --------------------------------------------------------------------


def test_create_event_returns_local_times_and_creates_a_calendar(client):
    event = _create(client)
    assert event["start"] == "2026-08-11T09:00"
    assert event["end"] == "2026-08-11T10:00"
    assert event["start_date"] == "2026-08-11"
    assert event["tzid"] == "America/Chicago"
    assert event["uid"].startswith("rally-")
    # A fresh install has no native calendar; one is created on demand rather
    # than failing the first event the family tries to add.
    assert event["calendar_id"]


def test_create_all_day_event_keeps_the_inclusive_last_day(client):
    event = _create(
        client, all_day=True, start="2026-08-14", end="2026-08-16", title="Camping trip"
    )
    assert event["all_day"] is True
    assert event["start_date"] == "2026-08-14"
    assert event["end_date"] == "2026-08-16"

    occurrence = [o for o in _occurrences(client) if o["title"] == "Camping trip"][0]
    assert occurrence["dates"] == ["2026-08-14", "2026-08-15", "2026-08-16"]


def test_create_with_attendees(client, make_member):
    emma = make_member("Emma")
    jon = make_member("Jon")
    event = _create(client, attendee_ids=[emma.id, jon.id])
    assert sorted(event["attendee_ids"]) == sorted([emma.id, jon.id])


def test_create_ignores_attendee_ids_that_are_not_family_members(client, make_member):
    """SQLite does not enforce the reference, so a bad id would be invisible."""
    emma = make_member("Emma")
    event = _create(client, attendee_ids=[emma.id, 9999])
    assert event["attendee_ids"] == [emma.id]


def test_create_rejects_an_invalid_recurrence_rule(client):
    response = client.post(
        "/api/events",
        json={"title": "Bad", "start": "2026-08-11T09:00", "rrule": "FREQ=SOMETIMES"},
    )
    assert response.status_code == 422


def test_create_rejects_an_end_before_the_start(client):
    response = client.post(
        "/api/events",
        json={
            "title": "Backwards",
            "start": "2026-08-11T10:00",
            "end": "2026-08-11T09:00",
        },
    )
    assert response.status_code == 422


def test_create_rejects_an_unknown_calendar(client):
    response = client.post(
        "/api/events",
        json={"title": "X", "start": "2026-08-11T09:00", "calendar_id": 999},
    )
    assert response.status_code == 422


# --- Read ----------------------------------------------------------------------


def test_list_occurrences_expands_a_series(client):
    _create(
        client,
        title="Scouts",
        start="2026-08-04T19:00",
        rrule="FREQ=WEEKLY;BYDAY=TU;COUNT=3",
    )
    dates = [o["start_date"] for o in _occurrences(client)]
    assert dates == ["2026-08-04", "2026-08-11", "2026-08-18"]


def test_list_occurrences_filters_by_member(client, make_member):
    emma = make_member("Emma")
    jon = make_member("Jon")
    _create(client, title="Piano", attendee_ids=[emma.id])
    _create(client, title="Standup", start="2026-08-12T09:00", attendee_ids=[jon.id])

    assert [o["title"] for o in _occurrences(client, member="Emma")] == ["Piano"]
    # No selection is the unfiltered state, matching the /todo assignee chips.
    assert len(_occurrences(client)) == 2


def test_list_occurrences_rejects_an_oversized_window(client):
    response = client.get("/api/events", params={"start": "2026-01-01", "end": "2030-01-01"})
    assert response.status_code == 422


def test_list_occurrences_rejects_malformed_dates(client):
    assert client.get("/api/events", params={"start": "last tuesday"}).status_code == 422


def test_get_event_returns_the_series_not_an_occurrence(client):
    created = _create(client, rrule="FREQ=WEEKLY;BYDAY=TU")
    event = client.get(f"/api/events/{created['id']}").json()
    assert event["rrule"] == "FREQ=WEEKLY;BYDAY=TU"
    assert event["overrides"] == []


def test_get_missing_event_is_404(client):
    assert client.get("/api/events/404").status_code == 404


def test_event_occurrences_endpoint_lists_one_series(client):
    created = _create(
        client,
        title="Scouts",
        start="2026-08-04T19:00",
        rrule="FREQ=WEEKLY;BYDAY=TU;COUNT=2",
    )
    response = client.get(
        f"/api/events/{created['id']}/occurrences",
        params={"start": "2026-08-01", "end": "2026-09-01"},
    )
    assert [o["start_date"] for o in response.json()] == ["2026-08-04", "2026-08-11"]


# --- Update: scope = all -------------------------------------------------------


def test_update_all_changes_the_series(client):
    created = _create(client, rrule="FREQ=WEEKLY;BYDAY=TU;COUNT=3")
    response = client.put(f"/api/events/{created['id']}", json={"title": "Orthodontist"})
    assert response.status_code == 200
    assert {o["title"] for o in _occurrences(client)} == {"Orthodontist"}


def test_update_all_keeps_an_existing_override(client):
    """Correcting a title must not snap a moved occurrence back into line."""
    created = _create(
        client,
        title="Soccer",
        start="2026-08-04T09:00",
        rrule="FREQ=WEEKLY;BYDAY=TU;COUNT=3",
    )
    client.put(
        f"/api/events/{created['id']}",
        params={"scope": "this", "occurrence_date": "2026-08-11"},
        json={"start": "2026-08-13T09:00", "end": "2026-08-13T10:00"},
    )
    client.put(f"/api/events/{created['id']}", json={"title": "Soccer practice"})

    moved = [o for o in _occurrences(client) if o["occurrence_date"] == "2026-08-11"][0]
    assert moved["start_date"] == "2026-08-13"  # still moved
    assert moved["title"] == "Soccer practice"  # and renamed


def test_update_clears_a_field_with_null_but_leaves_omitted_fields_alone(client):
    created = _create(client, description="bring paperwork", location="Dr. Kim")
    client.put(f"/api/events/{created['id']}", json={"description": None})
    event = client.get(f"/api/events/{created['id']}").json()
    assert event["description"] is None
    assert event["location"] == "Dr. Kim"


def test_update_can_remove_a_recurrence(client):
    created = _create(client, rrule="FREQ=WEEKLY;BYDAY=TU;COUNT=3")
    client.put(f"/api/events/{created['id']}", json={"rrule": None})
    assert len(_occurrences(client)) == 1


# --- Update: scope = this ------------------------------------------------------


def test_update_this_writes_an_override_and_leaves_the_series(client):
    created = _create(
        client,
        title="Scouts",
        start="2026-08-04T19:00",
        rrule="FREQ=WEEKLY;BYDAY=TU;COUNT=3",
    )
    client.put(
        f"/api/events/{created['id']}",
        params={"scope": "this", "occurrence_date": "2026-08-11"},
        json={"title": "Scouts (camp planning)"},
    )
    titles = {o["occurrence_date"]: o["title"] for o in _occurrences(client)}
    assert titles == {
        "2026-08-04": "Scouts",
        "2026-08-11": "Scouts (camp planning)",
        "2026-08-18": "Scouts",
    }


def test_update_this_requires_an_occurrence_date(client):
    created = _create(client, rrule="FREQ=WEEKLY;BYDAY=TU")
    response = client.put(f"/api/events/{created['id']}", params={"scope": "this"}, json={})
    assert response.status_code == 422


def test_scoped_update_rejects_a_non_recurring_event(client):
    created = _create(client)
    response = client.put(
        f"/api/events/{created['id']}",
        params={"scope": "this", "occurrence_date": "2026-08-11"},
        json={"title": "X"},
    )
    assert response.status_code == 422


# --- Update: scope = following -------------------------------------------------


def test_update_following_splits_the_series(client):
    created = _create(
        client,
        title="Scouts",
        start="2026-08-04T19:00",
        rrule="FREQ=WEEKLY;BYDAY=TU;COUNT=5",
    )
    response = client.put(
        f"/api/events/{created['id']}",
        params={"scope": "following", "occurrence_date": "2026-08-18"},
        json={"title": "Scouts (new hall)"},
    )
    assert response.status_code == 200
    assert response.json()["id"] != created["id"]  # A new series carries the tail

    titles = {o["occurrence_date"]: o["title"] for o in _occurrences(client)}
    assert titles["2026-08-04"] == "Scouts"
    assert titles["2026-08-11"] == "Scouts"
    assert titles["2026-08-18"] == "Scouts (new hall)"
    assert titles["2026-08-25"] == "Scouts (new hall)"


def test_split_point_is_a_date_not_an_occurrence_index(client):
    """Cancelling an earlier occurrence must not shift where the split lands."""
    created = _create(
        client,
        title="Scouts",
        start="2026-08-04T19:00",
        rrule="FREQ=WEEKLY;BYDAY=TU;COUNT=5",
    )
    client.delete(
        f"/api/events/{created['id']}",
        params={"scope": "this", "occurrence_date": "2026-08-11"},
    )
    client.put(
        f"/api/events/{created['id']}",
        params={"scope": "following", "occurrence_date": "2026-08-18"},
        json={"title": "Scouts (new hall)"},
    )
    titles = {o["occurrence_date"]: o["title"] for o in _occurrences(client)}
    assert "2026-08-11" not in titles  # still cancelled
    assert titles["2026-08-04"] == "Scouts"
    assert titles["2026-08-18"] == "Scouts (new hall)"


def test_split_carries_attendees_forward(client, make_member):
    emma = make_member("Emma")
    created = _create(
        client,
        title="Scouts",
        start="2026-08-04T19:00",
        rrule="FREQ=WEEKLY;BYDAY=TU;COUNT=4",
        attendee_ids=[emma.id],
    )
    tail = client.put(
        f"/api/events/{created['id']}",
        params={"scope": "following", "occurrence_date": "2026-08-18"},
        json={"title": "Scouts (new hall)"},
    ).json()
    assert tail["attendee_ids"] == [emma.id]


def test_split_moves_only_the_overrides_at_or_after_the_split(client):
    created = _create(
        client,
        title="Scouts",
        start="2026-08-04T19:00",
        rrule="FREQ=WEEKLY;BYDAY=TU;COUNT=5",
    )
    for occurrence_date in ("2026-08-11", "2026-08-25"):
        client.put(
            f"/api/events/{created['id']}",
            params={"scope": "this", "occurrence_date": occurrence_date},
            json={"title": f"Special {occurrence_date}"},
        )

    tail = client.put(
        f"/api/events/{created['id']}",
        params={"scope": "following", "occurrence_date": "2026-08-18"},
        json={"title": "Scouts (new hall)"},
    ).json()

    head = client.get(f"/api/events/{created['id']}").json()
    assert [o["occurrence_date"] for o in head["overrides"]] == ["2026-08-11"]
    assert [o["occurrence_date"] for o in tail["overrides"]] == ["2026-08-25"]


# --- Delete --------------------------------------------------------------------


def test_delete_this_cancels_one_occurrence(client):
    created = _create(
        client,
        title="Scouts",
        start="2026-08-04T19:00",
        rrule="FREQ=WEEKLY;BYDAY=TU;COUNT=3",
    )
    response = client.delete(
        f"/api/events/{created['id']}",
        params={"scope": "this", "occurrence_date": "2026-08-11"},
    )
    assert response.status_code == 204
    assert [o["occurrence_date"] for o in _occurrences(client)] == [
        "2026-08-04",
        "2026-08-18",
    ]


def test_delete_following_truncates_the_series(client):
    created = _create(
        client,
        title="Scouts",
        start="2026-08-04T19:00",
        rrule="FREQ=WEEKLY;BYDAY=TU;COUNT=5",
    )
    client.delete(
        f"/api/events/{created['id']}",
        params={"scope": "following", "occurrence_date": "2026-08-18"},
    )
    assert [o["occurrence_date"] for o in _occurrences(client)] == [
        "2026-08-04",
        "2026-08-11",
    ]


def test_delete_all_removes_the_event_and_its_rows(client, db_session, make_member):
    from rally.models import EventAttendee, EventOverride

    emma = make_member("Emma")
    created = _create(client, rrule="FREQ=WEEKLY;BYDAY=TU;COUNT=3", attendee_ids=[emma.id])
    client.put(
        f"/api/events/{created['id']}",
        params={"scope": "this", "occurrence_date": "2026-08-18"},
        json={"title": "One-off"},
    )

    assert client.delete(f"/api/events/{created['id']}").status_code == 204
    assert _occurrences(client) == []
    # The cascade is explicit because SQLite does not enforce the references.
    assert db_session.query(EventAttendee).filter_by(event_id=created["id"]).count() == 0
    assert db_session.query(EventOverride).filter_by(event_id=created["id"]).count() == 0


def test_delete_missing_event_is_404(client):
    assert client.delete("/api/events/404").status_code == 404


# --- Native calendars in the Settings CRUD -------------------------------------


def test_deleting_a_native_calendar_takes_its_events_with_it(client, db_session):
    from rally.models import Event

    created = _create(client)
    calendar_id = created["calendar_id"]

    assert client.delete(f"/api/calendars/{calendar_id}").status_code == 204
    assert db_session.query(Event).count() == 0


def test_native_calendar_connection_test_reports_an_event_count(client):
    created = _create(client)
    response = client.post(f"/api/calendars/{created['calendar_id']}/test")
    assert response.json() == {
        "success": True,
        "message": "Rally calendar with 1 event(s)",
    }


def test_adding_a_family_member_gives_them_a_calendar(client, db_session):
    from rally.models import Calendar

    client.post("/api/family", json={"name": "Theo"})
    calendars = db_session.query(Calendar).filter(Calendar.cal_type == "native").all()
    assert [c.label for c in calendars] == ["Theo's Calendar"]
