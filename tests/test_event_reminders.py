"""Several reminders per event, of any length (#336)."""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from rally.calendars.reminders import MAX_LEAD_MINUTES, Reminder
from rally.models import EventNotification, EventOverride, EventReminder
from rally.notifications import KIND_REMINDER, check_due_reminders

CHICAGO = ZoneInfo("America/Chicago")


@pytest.fixture(autouse=True)
def _tz(local_timezone):
    local_timezone("America/Chicago")


@pytest.fixture(autouse=True)
def _clock(frozen_now):
    frozen_now(datetime(2026, 8, 1, 12, tzinfo=UTC))


@pytest.fixture
def token(make_setting):
    make_setting("pushover_app_token", "app-token")
    return "app-token"


@pytest.fixture
def emma(make_member):
    return make_member("Emma", pushover_user_key="emma-key")


def _at(local_iso: str) -> datetime:
    return datetime.fromisoformat(local_iso).replace(tzinfo=CHICAGO).astimezone(UTC)


def _create(client, **overrides) -> dict:
    payload = {
        "title": "Dentist",
        "start": "2026-08-11T15:30",
        "end": "2026-08-11T16:30",
        "tzid": "America/Chicago",
    }
    payload.update(overrides)
    response = client.post("/api/events", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def _occurrences(client, start="2026-08-01", end="2026-09-30") -> list[dict]:
    response = client.get("/api/events", params={"start": start, "end": end})
    assert response.status_code == 200, response.text
    return response.json()["occurrences"]


# --- What the API takes and gives back -----------------------------------------


def test_an_event_keeps_several_reminders_shortest_first(client):
    created = _create(
        client,
        reminders=[{"minutes_before": 1440}, {"minutes_before": 60}, {"minutes_before": 10080}],
    )
    assert [r["label"] for r in created["reminders"]] == [
        "1 hour before",
        "1 day before",
        "1 week before",
    ]
    assert [r["key"] for r in created["reminders"]] == ["m60", "m1440", "m10080"]
    (occurrence,) = _occurrences(client)
    assert occurrence["reminders_display"] == ["1 hour before", "1 day before", "1 week before"]
    assert occurrence["reminders_own"] is False


def test_an_all_day_event_takes_days_before_at_a_time(client):
    created = _create(
        client,
        all_day=True,
        start="2026-08-14",
        end="2026-08-14",
        reminders=[{"days_before": 1, "at_time": "09:00"}, {"days_before": 14, "at_time": "18:00"}],
    )
    assert [r["label"] for r in created["reminders"]] == [
        "1 day before at 9:00 AM",
        "2 weeks before at 6:00 PM",
    ]
    assert created["reminders"][0]["at_time"] == "09:00"


@pytest.mark.parametrize(
    ("reminders", "all_day", "words"),
    [
        ([{"minutes_before": m} for m in (0, 10, 30, 60, 120, 1440)], False, "up to 5"),
        ([{"minutes_before": MAX_LEAD_MINUTES + 1}], False, "at most 35 days"),
        ([{"minutes_before": -60}], False, "at most 35 days"),
        ([{"minutes_before": 60}, {"minutes_before": 60}], False, "already on the list"),
        ([{"days_before": 1, "at_time": "09:00"}], False, "timed event"),
        ([{"minutes_before": 60}], True, "all-day event"),
        ([{"days_before": 36, "at_time": "09:00"}], True, "at most 35 days"),
        ([{"days_before": 1, "at_time": "nine"}], True, "HH:MM"),
    ],
)
def test_a_reminder_list_the_api_refuses(client, reminders, all_day, words):
    payload = {"title": "Dentist", "tzid": "America/Chicago", "reminders": reminders}
    if all_day:
        payload.update(all_day=True, start="2026-08-14", end="2026-08-14")
    else:
        payload.update(start="2026-08-11T15:30", end="2026-08-11T16:30")
    response = client.post("/api/events", json=payload)
    assert response.status_code == 422
    assert words in response.json()["detail"]


def test_the_old_single_field_is_gone(client):
    created = _create(client)
    assert "notify_minutes_before" not in created
    assert "notify_minutes_before" not in _occurrences(client)[0]


def test_saving_without_reminders_leaves_them_alone(client):
    created = _create(client, reminders=[{"minutes_before": 90}])
    response = client.put(f"/api/events/{created['id']}", json={"title": "Dentist — Emma"})
    assert [r["minutes_before"] for r in response.json()["reminders"]] == [90]


def test_changing_between_timed_and_all_day_clears_them(client):
    created = _create(client, reminders=[{"minutes_before": 60}])
    response = client.put(
        f"/api/events/{created['id']}",
        json={"all_day": True, "start": "2026-08-11", "end": "2026-08-11"},
    )
    assert response.json()["reminders"] == []


def test_a_refused_list_writes_nothing(client, db_session):
    created = _create(client, reminders=[{"minutes_before": 60}])
    response = client.put(
        f"/api/events/{created['id']}",
        json={"title": "Renamed", "reminders": [{"minutes_before": 999999}]},
    )
    assert response.status_code == 422
    after = client.get(f"/api/events/{created['id']}").json()
    assert after["title"] == "Dentist"
    assert [r["minutes_before"] for r in after["reminders"]] == [60]


# --- Sending -------------------------------------------------------------------


def test_each_reminder_sends_once(client, db_session, token, emma, mock_pushover):
    _create(
        client,
        location="Smile Dental",
        attendee_ids=[emma.id],
        reminders=[{"minutes_before": 60}, {"minutes_before": 1440}],
    )
    mock_pushover.sent.clear()

    assert check_due_reminders(db_session, now=_at("2026-08-10T15:30")) == 1
    assert check_due_reminders(db_session, now=_at("2026-08-10T15:31")) == 0
    assert check_due_reminders(db_session, now=_at("2026-08-11T14:30")) == 1
    assert check_due_reminders(db_session, now=_at("2026-08-11T14:31")) == 0

    assert [push["message"] for push in mock_pushover.sent] == [
        "Tomorrow 3:30 PM · at Smile Dental",
        "Today 3:30 PM · at Smile Dental",
    ]
    keys = {
        row.reminder_key
        for row in db_session.query(EventNotification).filter_by(kind=KIND_REMINDER)
    }
    assert keys == {"m60", "m1440"}


def test_a_reminder_up_to_35_days_ahead_is_sent(client, db_session, token, emma, mock_pushover):
    _create(
        client,
        start="2026-09-05T14:00",
        end="2026-09-05T15:00",
        attendee_ids=[emma.id],
        reminders=[{"minutes_before": MAX_LEAD_MINUTES}],
    )
    mock_pushover.sent.clear()
    assert check_due_reminders(db_session, now=_at("2026-08-01T14:00")) == 1
    assert mock_pushover.sent[0]["message"] == "Sep 5 2:00 PM"


def test_a_week_ahead_push_names_the_date_not_the_weekday(
    client, db_session, token, emma, mock_pushover
):
    """A bare ``Tuesday`` sent on a Tuesday reads as today."""
    _create(client, attendee_ids=[emma.id], reminders=[{"minutes_before": 10080}])
    mock_pushover.sent.clear()
    check_due_reminders(db_session, now=_at("2026-08-04T15:30"))
    assert mock_pushover.sent[0]["message"] == "Aug 11 3:30 PM"


def test_inside_a_week_the_push_names_the_weekday(client, db_session, token, emma, mock_pushover):
    _create(client, attendee_ids=[emma.id], reminders=[{"minutes_before": 3 * 1440}])
    mock_pushover.sent.clear()
    check_due_reminders(db_session, now=_at("2026-08-08T15:30"))
    assert mock_pushover.sent[0]["message"] == "Tuesday 3:30 PM"


def test_an_all_day_reminder_is_a_wall_clock_time_across_dst(
    client, db_session, token, emma, mock_pushover
):
    """Sunday Nov 1, 2026 is the fall-back day: 9:00 AM is still 9:00 AM."""
    _create(
        client,
        all_day=True,
        start="2026-11-02",
        end="2026-11-02",
        attendee_ids=[emma.id],
        reminders=[{"days_before": 1, "at_time": "09:00"}],
    )
    mock_pushover.sent.clear()
    assert check_due_reminders(db_session, now=_at("2026-11-01T08:59")) == 0
    assert check_due_reminders(db_session, now=_at("2026-11-01T09:00")) == 1
    assert mock_pushover.sent[0]["message"] == "All day tomorrow"


# --- One occurrence's own list -------------------------------------------------


def _weekly(client, emma) -> dict:
    return _create(
        client,
        title="Piano lesson",
        start="2026-08-04T16:00",
        end="2026-08-04T17:00",
        rrule="FREQ=WEEKLY;BYDAY=TU",
        attendee_ids=[emma.id],
        reminders=[{"minutes_before": 60}],
    )


def test_this_occurrence_keeps_a_whole_list_of_its_own(client, db_session, emma):
    event = _weekly(client, emma)
    response = client.put(
        f"/api/events/{event['id']}",
        params={"scope": "this", "occurrence_date": "2026-08-11"},
        json={"reminders": [{"minutes_before": 60}, {"minutes_before": 1440}]},
    )
    assert response.status_code == 200, response.text
    assert [r["minutes_before"] for r in response.json()["reminders"]] == [60]

    # A later change to the series does not reach it.
    client.put(f"/api/events/{event['id']}", json={"reminders": [{"minutes_before": 30}]})

    by_date = {o["occurrence_date"]: o for o in _occurrences(client)}
    assert by_date["2026-08-11"]["reminders_display"] == ["1 hour before", "1 day before"]
    assert by_date["2026-08-11"]["reminders_own"] is True
    assert by_date["2026-08-18"]["reminders_display"] == ["30 minutes before"]
    assert by_date["2026-08-18"]["reminders_own"] is False
    override = db_session.query(EventOverride).filter_by(occurrence_date="2026-08-11").one()
    assert override.own_reminders is True


def test_an_empty_own_list_sends_nothing_that_day(client, db_session, token, emma, mock_pushover):
    event = _weekly(client, emma)
    client.put(
        f"/api/events/{event['id']}",
        params={"scope": "this", "occurrence_date": "2026-08-11"},
        json={"reminders": []},
    )
    mock_pushover.sent.clear()
    assert check_due_reminders(db_session, now=_at("2026-08-11T15:00")) == 0
    assert check_due_reminders(db_session, now=_at("2026-08-18T15:00")) == 1


def test_a_following_split_carries_the_reminders_and_the_own_lists(client, db_session, emma):
    event = _weekly(client, emma)
    client.put(
        f"/api/events/{event['id']}",
        params={"scope": "this", "occurrence_date": "2026-08-25"},
        json={"reminders": [{"minutes_before": 10}]},
    )
    tail = client.put(
        f"/api/events/{event['id']}",
        params={"scope": "following", "occurrence_date": "2026-08-18"},
        json={"title": "Piano lesson (new studio)"},
    ).json()
    assert [r["minutes_before"] for r in tail["reminders"]] == [60]
    own = db_session.query(EventReminder).filter_by(occurrence_date="2026-08-25").one()
    assert own.event_id == tail["id"]


def test_deleting_an_event_deletes_its_reminders(client, db_session, emma):
    event = _weekly(client, emma)
    client.put(
        f"/api/events/{event['id']}",
        params={"scope": "this", "occurrence_date": "2026-08-25"},
        json={"reminders": [{"minutes_before": 10}]},
    )
    assert client.delete(f"/api/events/{event['id']}").status_code == 204
    assert db_session.query(EventReminder).count() == 0


def test_deleting_the_following_drops_their_own_lists(client, db_session, emma):
    event = _weekly(client, emma)
    client.put(
        f"/api/events/{event['id']}",
        params={"scope": "this", "occurrence_date": "2026-08-25"},
        json={"reminders": [{"minutes_before": 10}]},
    )
    client.delete(
        f"/api/events/{event['id']}",
        params={"scope": "following", "occurrence_date": "2026-08-18"},
    )
    remaining = db_session.query(EventReminder).all()
    assert [(r.occurrence_date, r.minutes_before) for r in remaining] == [(None, 60)]


# --- The dataclass ---------------------------------------------------------------


def test_a_reminder_that_does_not_fit_the_occurrence_is_never_due():
    class _Occurrence:
        all_day = True
        start_local_date = "2026-08-14"

    assert Reminder(minutes_before=60).due_at(_Occurrence(), CHICAGO) is None


# --- Wording an unsaved reminder -------------------------------------------------


@pytest.mark.parametrize(
    ("sent", "label", "key"),
    [
        ({"minutes_before": 14400}, "1 week 3 days before", "m14400"),
        ({"minutes_before": 0}, "When it starts", "m0"),
        ({"days_before": 2, "at_time": "18:00"}, "2 days before at 6:00 PM", "d2@18:00"),
    ],
)
def test_an_unsaved_reminder_is_worded_by_the_server(client, sent, label, key):
    response = client.post("/api/events/describe-reminder", json=sent)
    assert response.status_code == 200, response.text
    assert response.json()["label"] == label
    assert response.json()["key"] == key


def test_describing_refuses_what_a_save_would(client):
    response = client.post("/api/events/describe-reminder", json={"minutes_before": 60 * 24 * 36})
    assert response.status_code == 422
    assert "at most 35 days" in response.json()["detail"]
