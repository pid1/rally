"""Tests for Pushover notifications to event attendees.

The transport is always stubbed — no test may reach the real API. What is
actually under test is the policy around it, which is where the interesting
failure modes live: who gets a message, how many times, how late is too late,
and what happens when the provider is down.
"""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from rally.models import EventNotification, Setting
from rally.notifications import (
    KIND_CREATED,
    KIND_DELETED,
    KIND_REMINDER,
    KIND_UPDATED,
    REMINDER_GRACE_MINUTES,
    PushoverError,
    check_due_reminders,
    purge_old_notifications,
    run_due_reminders_once_per_minute,
    send_pushover,
)

CHICAGO = ZoneInfo("America/Chicago")


@pytest.fixture(autouse=True)
def _tz(local_timezone):
    local_timezone("America/Chicago")


@pytest.fixture(autouse=True)
def _clock(frozen_now):
    """Pin "now" just before the fixture events, so they are still upcoming."""
    frozen_now(datetime(2026, 8, 11, 12, tzinfo=UTC))


@pytest.fixture
def token(make_setting):
    make_setting("pushover_app_token", "app-token")
    return "app-token"


@pytest.fixture
def reachable(make_member):
    return make_member("Emma", pushover_user_key="emma-key")


@pytest.fixture
def unreachable(make_member):
    return make_member("Jon")


# --- The transport itself ------------------------------------------------------


class _Response:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {"status": 1}

    def json(self):
        return self._payload


def test_send_pushover_posts_token_user_and_message(monkeypatch):
    captured = {}

    def fake_post(url, data=None, timeout=None):
        captured.update({"url": url, "data": data})
        return _Response()

    monkeypatch.setattr("requests.post", fake_post)
    send_pushover("tok", "user", "Dentist at 9", title="Dentist", device="phone")

    assert captured["data"] == {
        "token": "tok",
        "user": "user",
        "message": "Dentist at 9",
        "title": "Dentist",
        "device": "phone",
    }


def test_send_pushover_treats_a_200_without_status_1_as_a_failure(monkeypatch):
    """Pushover reports rejection in the body, not only in the status code."""
    monkeypatch.setattr(
        "requests.post",
        lambda *a, **k: _Response(200, {"status": 0, "errors": ["user identifier is invalid"]}),
    )
    with pytest.raises(PushoverError, match="user identifier is invalid"):
        send_pushover("tok", "bad-user", "hi")


def test_send_pushover_wraps_a_transport_error(monkeypatch):
    def boom(*args, **kwargs):
        raise OSError("connection reset")

    monkeypatch.setattr("requests.post", boom)
    with pytest.raises(PushoverError, match="connection reset"):
        send_pushover("tok", "user", "hi")


# --- Who gets notified ---------------------------------------------------------


def test_notify_reports_each_recipient_separately(
    client, token, reachable, unreachable, make_event, mock_pushover
):
    """ "It worked" and "both phones buzzed" are different claims."""
    event = make_event("Dentist", attendees=[reachable, unreachable])

    response = client.post(f"/api/events/{event.id}/notify", json={})

    assert response.status_code == 200
    assert response.json()["sent"] == ["Emma"]
    assert response.json()["skipped"] == ["Jon"]
    assert response.json()["failed"] == []
    assert len(mock_pushover.sent) == 1


def test_non_attendees_are_never_notified(
    client, token, reachable, make_member, make_event, mock_pushover
):
    make_member("Maya", pushover_user_key="maya-key")  # Not on the event
    event = make_event("Dentist", attendees=[reachable])

    client.post(f"/api/events/{event.id}/notify", json={})

    assert [message["user"] for message in mock_pushover.sent] == ["emma-key"]


def test_an_event_with_no_attendees_notifies_nobody(
    client, token, reachable, make_event, mock_pushover
):
    event = make_event("Dentist")
    response = client.post(f"/api/events/{event.id}/notify", json={})
    assert response.json() == {
        "sent": [],
        "skipped": [],
        "muted": [],
        "failed": [],
        "error": None,
    }
    assert mock_pushover.sent == []


def test_missing_app_token_skips_cleanly_rather_than_crashing(
    client, reachable, make_event, mock_pushover
):
    event = make_event("Dentist", attendees=[reachable])
    response = client.post(f"/api/events/{event.id}/notify", json={})

    assert response.status_code == 200
    assert response.json()["sent"] == []
    assert response.json()["skipped"] == ["Emma"]
    assert "token" in response.json()["error"]


def test_notify_uses_the_members_device_when_set(
    client, token, make_member, make_event, mock_pushover
):
    member = make_member("Emma", pushover_user_key="emma-key", pushover_device="kitchen")
    event = make_event("Dentist", attendees=[member])
    client.post(f"/api/events/{event.id}/notify", json={})
    assert mock_pushover.sent[0]["device"] == "kitchen"


def test_notify_defaults_to_the_next_upcoming_occurrence(
    client, token, reachable, make_event, mock_pushover, frozen_now
):
    frozen_now(datetime(2026, 8, 12, 12, tzinfo=UTC))
    event = make_event(
        "Scouts",
        start="2026-08-04T19:00",
        rrule="FREQ=WEEKLY;BYDAY=TU;COUNT=5",
        attendees=[reachable],
    )
    client.post(f"/api/events/{event.id}/notify", json={})

    assert "Tuesday" in mock_pushover.sent[0]["message"]
    assert "7:00 PM" in mock_pushover.sent[0]["message"]


def test_notify_accepts_a_message_override(client, token, reachable, make_event, mock_pushover):
    event = make_event("Dentist", attendees=[reachable])
    client.post(f"/api/events/{event.id}/notify", json={"message": "Leaving now, meet me there"})
    assert mock_pushover.sent[0]["message"] == "Leaving now, meet me there"


def test_notify_rejects_a_malformed_occurrence_date(client, token, reachable, make_event):
    event = make_event("Dentist", attendees=[reachable])
    response = client.post(
        f"/api/events/{event.id}/notify", json={"occurrence_date": "next tuesday"}
    )
    assert response.status_code == 422


def test_notify_missing_event_is_404(client):
    assert client.post("/api/events/404/notify", json={}).status_code == 404


# --- Reminders -----------------------------------------------------------------


def _at(local_str):
    """A UTC instant from a Chicago wall time."""
    return datetime.fromisoformat(local_str).replace(tzinfo=CHICAGO).astimezone(UTC)


def test_reminder_fires_once_its_moment_arrives(
    db_session, token, reachable, make_event, mock_pushover
):
    make_event("Dentist", start="2026-08-11T09:00", notify_minutes_before=30, attendees=[reachable])
    sent = check_due_reminders(db_session, now=_at("2026-08-11T08:30"))

    assert sent == 1
    assert mock_pushover.sent[0]["title"] == "Dentist"


def test_reminder_does_not_fire_early(db_session, token, reachable, make_event, mock_pushover):
    make_event("Dentist", start="2026-08-11T09:00", notify_minutes_before=30, attendees=[reachable])
    assert check_due_reminders(db_session, now=_at("2026-08-11T08:29")) == 0
    assert mock_pushover.sent == []


def test_a_reminder_is_sent_exactly_once(db_session, token, reachable, make_event, mock_pushover):
    make_event("Dentist", start="2026-08-11T09:00", notify_minutes_before=30, attendees=[reachable])
    for minute in (30, 31, 32):
        check_due_reminders(db_session, now=_at(f"2026-08-11T08:{minute}"))

    assert len(mock_pushover.sent) == 1


def test_each_occurrence_of_a_series_reminds_independently(
    db_session, token, reachable, make_event, mock_pushover
):
    make_event(
        "Scouts",
        start="2026-08-04T19:00",
        rrule="FREQ=WEEKLY;BYDAY=TU;COUNT=3",
        notify_minutes_before=60,
        attendees=[reachable],
    )
    check_due_reminders(db_session, now=_at("2026-08-04T18:00"))
    check_due_reminders(db_session, now=_at("2026-08-11T18:00"))

    assert len(mock_pushover.sent) == 2
    dates = {
        row.occurrence_date
        for row in db_session.query(EventNotification).filter_by(kind=KIND_REMINDER).all()
    }
    assert dates == {"2026-08-04", "2026-08-11"}


def test_lead_time_is_measured_from_the_occurrence_across_a_dst_change(
    db_session, token, reachable, make_event, mock_pushover
):
    """A fixed offset from DTSTART is an hour wrong for half the year."""
    make_event(
        "Scouts",
        start="2026-10-27T19:00",
        rrule="FREQ=WEEKLY;BYDAY=TU;COUNT=3",
        notify_minutes_before=30,
        attendees=[reachable],
    )
    # Before the transition: 6:30 PM CDT.
    assert check_due_reminders(db_session, now=_at("2026-10-27T18:30")) == 1
    # After it: still 6:30 PM, now CST — a different UTC instant.
    assert check_due_reminders(db_session, now=_at("2026-11-03T18:30")) == 1
    assert len(mock_pushover.sent) == 2


def test_a_long_missed_window_is_dropped_not_replayed(
    db_session, token, reachable, make_event, mock_pushover
):
    """A push at 11:00 for a 08:30 reminder misinforms rather than reminds."""
    make_event("Dentist", start="2026-08-11T09:00", notify_minutes_before=30, attendees=[reachable])
    assert check_due_reminders(db_session, now=_at("2026-08-11T11:00")) == 0
    assert mock_pushover.sent == []


def test_a_briefly_missed_window_still_sends(
    db_session, token, reachable, make_event, mock_pushover
):
    make_event("Dentist", start="2026-08-11T09:00", notify_minutes_before=30, attendees=[reachable])
    late = _at("2026-08-11T08:30") + timedelta(minutes=REMINDER_GRACE_MINUTES - 1)
    assert check_due_reminders(db_session, now=late) == 1


def test_events_without_a_reminder_are_ignored(
    db_session, token, reachable, make_event, mock_pushover
):
    make_event("Dentist", start="2026-08-11T09:00", attendees=[reachable])
    assert check_due_reminders(db_session, now=_at("2026-08-11T08:30")) == 0


def test_a_failed_send_is_recorded_and_retried_within_the_window(
    db_session, token, reachable, make_event, mock_pushover
):
    """A five-minute outage must not silently eat the day's reminder."""
    make_event("Dentist", start="2026-08-11T09:00", notify_minutes_before=30, attendees=[reachable])

    mock_pushover.fail_with("service unavailable")
    assert check_due_reminders(db_session, now=_at("2026-08-11T08:30")) == 0

    row = db_session.query(EventNotification).one()
    assert row.status == "failed"
    assert row.detail == "service unavailable"

    mock_pushover.succeed()
    assert check_due_reminders(db_session, now=_at("2026-08-11T08:33")) == 1

    db_session.expire_all()
    assert db_session.query(EventNotification).one().status == "sent"
    assert len(mock_pushover.sent) == 1


def test_a_failure_never_escapes_as_an_exception(
    db_session, token, reachable, make_event, mock_pushover
):
    make_event("Dentist", start="2026-08-11T09:00", notify_minutes_before=30, attendees=[reachable])
    mock_pushover.fail_with("boom")
    assert check_due_reminders(db_session, now=_at("2026-08-11T08:30")) == 0  # no raise


def test_all_day_reminder_message_says_all_day(
    db_session, token, reachable, make_event, mock_pushover
):
    make_event(
        "Emma's birthday",
        start="2026-08-14",
        end="2026-08-14",
        all_day=True,
        notify_minutes_before=60,
        attendees=[reachable],
    )
    check_due_reminders(db_session, now=_at("2026-08-13T23:00"))
    assert "All day" in mock_pushover.sent[0]["message"]


def test_location_is_included_when_known(db_session, token, reachable, make_event, mock_pushover):
    make_event(
        "Dentist",
        start="2026-08-11T09:00",
        notify_minutes_before=30,
        location="Dr. Kim",
        attendees=[reachable],
    )
    check_due_reminders(db_session, now=_at("2026-08-11T08:30"))
    assert "at Dr. Kim" in mock_pushover.sent[0]["message"]


# --- The opportunistic hook ----------------------------------------------------


def test_the_hook_runs_at_most_once_a_minute(
    db_session, token, reachable, make_event, mock_pushover
):
    make_event("Dentist", start="2026-08-11T09:00", notify_minutes_before=30, attendees=[reachable])
    now = _at("2026-08-11T08:30")

    assert run_due_reminders_once_per_minute(db_session, now=now) == 1
    # A second call in the same minute is a no-op even though nothing has been
    # sent since — the gate is the minute, not the outcome.
    assert run_due_reminders_once_per_minute(db_session, now=now + timedelta(seconds=20)) == 0
    assert db_session.get(Setting, "reminder_last_check_at").value == "2026-08-11T13:30"


def test_listing_events_gives_reminders_a_chance_to_fire(
    client, db_session, token, reachable, make_event, mock_pushover, frozen_now
):
    """A dev-served instance has no scheduler, so the API is the only clock."""
    make_event("Dentist", start="2026-08-11T09:00", notify_minutes_before=30, attendees=[reachable])
    frozen_now(_at("2026-08-11T08:30"))

    client.get("/api/events", params={"start": "2026-08-01", "end": "2026-09-01"})

    assert len(mock_pushover.sent) == 1


# --- Retention -----------------------------------------------------------------


def test_old_notification_records_are_purged(db_session):
    db_session.add(
        EventNotification(
            event_id=1,
            occurrence_date="2026-06-01",
            family_member_id=1,
            kind=KIND_REMINDER,
            status="sent",
        )
    )
    db_session.add(
        EventNotification(
            event_id=1,
            occurrence_date="2026-08-01",
            family_member_id=1,
            kind=KIND_REMINDER,
            status="sent",
        )
    )
    db_session.commit()

    assert purge_old_notifications(db_session, "2026-08-14") == 1
    assert db_session.query(EventNotification).count() == 1


# --- Add, change and remove notices --------------------------------------------
#
# Changing the calendar is the thing the user asked for. These check both halves
# of that: the attendees learn what changed, and nothing about the push can stop
# the change from happening.
#
# The message is one body sent to everybody — a title naming what happened, then
# When / Where / Attendees. It is deliberately not personalized: the recipients
# are the attendees, so a body addressed to nobody in particular still reaches
# exactly the right people, and the family compares one text rather than four.


def _create(client, **overrides) -> dict:
    payload = {
        "title": "Dentist",
        "start": "2026-08-14T09:00",
        "end": "2026-08-14T10:00",
        "tzid": "America/Chicago",
    }
    payload.update(overrides)
    response = client.post("/api/events", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def test_an_addition_reads_as_the_whole_notice(
    client, token, reachable, make_member, mock_pushover
):
    """The shape of the thing, asserted once, in full."""
    rodryk = make_member("Rodryk", pushover_user_key="rodryk-key")

    _create(
        client,
        title="Soccer practice",
        start="2026-08-14T17:30",
        end="2026-08-14T18:30",
        location="Field 3",
        attendee_ids=[reachable.id, rodryk.id],
    )

    assert mock_pushover.sent[0]["title"] == "Calendar Addition: Soccer practice"
    assert mock_pushover.sent[0]["message"] == (
        "When: 2026-08-14 · 5:30 to 6:30 PM CDT\nWhere: Field 3\nAttendees: Emma, Rodryk"
    )


def test_a_repeating_addition_reads_as_the_whole_notice(
    client, token, reachable, make_member, mock_pushover
):
    """The same, plus the sentence a recurring series earns."""
    rodryk = make_member("Rodryk", pushover_user_key="rodryk-key")

    _create(
        client,
        title="Soccer practice",
        start="2026-08-14T17:30",
        end="2026-08-14T18:30",
        location="Field 3",
        rrule="FREQ=WEEKLY;BYDAY=FR",
        attendee_ids=[reachable.id, rodryk.id],
    )

    assert mock_pushover.sent[0]["message"] == (
        "When: 2026-08-14 · 5:30 to 6:30 PM CDT\n"
        "Where: Field 3\n"
        "Attendees: Emma, Rodryk\n"
        "This event repeats weekly on Friday"
    )


# --- How long it runs ----------------------------------------------------------


def test_a_time_crossing_noon_states_both_meridiems(client, token, reachable, mock_pushover):
    """ "11:30 to 1:00 PM" would be an hour and a half of guesswork."""
    _create(
        client,
        start="2026-08-14T11:30",
        end="2026-08-14T13:00",
        attendee_ids=[reachable.id],
    )

    assert "When: 2026-08-14 · 11:30 AM to 1:00 PM CDT" in mock_pushover.sent[0]["message"]


def test_an_event_with_no_duration_states_one_time(client, token, reachable, mock_pushover):
    _create(
        client,
        start="2026-08-14T09:00",
        end="2026-08-14T09:00",
        attendee_ids=[reachable.id],
    )

    assert "When: 2026-08-14 · 9:00 AM CDT" in mock_pushover.sent[0]["message"]


def test_a_timed_event_spanning_days_dates_both_ends(client, token, reachable, mock_pushover):
    """Which end is which has to be readable without doing arithmetic."""
    _create(
        client,
        title="Red-eye",
        start="2026-08-14T22:30",
        end="2026-08-15T06:15",
        attendee_ids=[reachable.id],
    )

    assert "When: 2026-08-14 10:30 PM – 2026-08-15 6:15 AM CDT" in mock_pushover.sent[0]["message"]


# --- How often it repeats ------------------------------------------------------
#
# The add-event form offers five choices and compiles them to RRULE. These are
# those five, read back in the words somebody chose them by.


@pytest.mark.parametrize(
    ("rrule", "expected"),
    [
        ("FREQ=DAILY", "This event repeats daily"),
        ("FREQ=WEEKLY;BYDAY=FR", "This event repeats weekly on Friday"),
        ("FREQ=WEEKLY;INTERVAL=2;BYDAY=FR", "This event repeats every 2 weeks on Friday"),
        ("FREQ=MONTHLY;BYMONTHDAY=14", "This event repeats monthly on the 14th"),
        ("FREQ=YEARLY", "This event repeats yearly"),
    ],
)
def test_each_repeat_choice_is_read_back_in_its_own_words(
    client, token, reachable, mock_pushover, rrule, expected
):
    _create(client, rrule=rrule, attendee_ids=[reachable.id])
    assert mock_pushover.sent[0]["message"].endswith(expected)


def test_a_rule_richer_than_the_form_stays_vague_rather_than_wrong(
    client, token, reachable, mock_pushover
):
    """An imported rule may say things the five choices cannot."""
    _create(client, rrule="FREQ=HOURLY;INTERVAL=6", attendee_ids=[reachable.id])
    assert mock_pushover.sent[0]["message"].endswith("This event repeats on a custom schedule")


def test_a_multi_day_weekly_rule_names_every_day(client, token, reachable, mock_pushover):
    _create(client, rrule="FREQ=WEEKLY;BYDAY=MO,WE,FR", attendee_ids=[reachable.id])
    assert mock_pushover.sent[0]["message"].endswith(
        "This event repeats weekly on Monday, Wednesday and Friday"
    )


def test_a_one_off_event_says_nothing_about_repeating(client, token, reachable, mock_pushover):
    _create(client, attendee_ids=[reachable.id])
    assert "repeats" not in mock_pushover.sent[0]["message"]


def test_a_deletion_still_says_what_it_was_repeating(
    client, token, reachable, make_event, mock_pushover
):
    """Knowing a whole weekly series just went away is the point."""
    event = make_event(
        "Scouts",
        start="2026-08-04T19:00",
        rrule="FREQ=WEEKLY;BYDAY=TU",
        attendees=[reachable],
    )

    client.delete(f"/api/events/{event.id}")

    assert mock_pushover.sent[0]["title"] == "Calendar Deletion: Scouts"
    assert mock_pushover.sent[0]["message"].endswith("This event repeats weekly on Tuesday")


def test_every_recipient_gets_the_same_body(client, token, reachable, make_member, mock_pushover):
    maya = make_member("Maya", pushover_user_key="maya-key")
    alex = make_member("Alex", pushover_user_key="alex-key")

    _create(client, attendee_ids=[reachable.id, maya.id, alex.id])

    bodies = {message["user"]: message["message"] for message in mock_pushover.sent}
    assert set(bodies) == {"emma-key", "maya-key", "alex-key"}
    assert len(set(bodies.values())) == 1


def test_the_notice_dates_the_event_in_the_servers_local_time(
    client, token, reachable, mock_pushover
):
    """9:00 AM Chicago is 14:00 UTC — the notice must say the local reading."""
    _create(client, attendee_ids=[reachable.id])

    assert "When: 2026-08-14 · 9:00 to 10:00 AM CDT" in mock_pushover.sent[0]["message"]


def test_an_all_day_notice_carries_the_date_without_inventing_a_time(
    client, token, reachable, mock_pushover
):
    _create(
        client,
        title="Emma's birthday",
        all_day=True,
        start="2026-08-14",
        end="2026-08-14",
        attendee_ids=[reachable.id],
    )

    assert "When: 2026-08-14 · All day" in mock_pushover.sent[0]["message"]


def test_a_multi_day_notice_names_both_ends(client, token, reachable, mock_pushover):
    _create(
        client,
        title="Camping",
        all_day=True,
        start="2026-08-14",
        end="2026-08-16",
        attendee_ids=[reachable.id],
    )

    assert "When: 2026-08-14 – 2026-08-16 · All day" in mock_pushover.sent[0]["message"]


def test_a_notice_without_a_location_simply_omits_the_line(client, token, reachable, mock_pushover):
    _create(client, attendee_ids=[reachable.id])
    assert "Where:" not in mock_pushover.sent[0]["message"]


def test_the_attendee_line_names_everybody_including_the_unreachable(
    client, token, reachable, unreachable, mock_pushover
):
    """A member with no Pushover key is still going to the dentist."""
    _create(client, attendee_ids=[reachable.id, unreachable.id])

    assert [message["user"] for message in mock_pushover.sent] == ["emma-key"]
    assert "Attendees: Emma, Jon" in mock_pushover.sent[0]["message"]


def test_a_recurring_notice_describes_the_next_occurrence(client, token, reachable, mock_pushover):
    """ "Scouts moved" means the next Scouts, not the one three months out."""
    _create(
        client,
        title="Scouts",
        start="2026-08-04T19:00",
        end="2026-08-04T20:00",
        rrule="FREQ=WEEKLY;BYDAY=TU;COUNT=10",
        attendee_ids=[reachable.id],
    )

    assert "When: 2026-08-11 · 7:00 to 8:00 PM CDT" in mock_pushover.sent[0]["message"]


def test_an_event_with_no_attendees_announces_nothing(client, token, reachable, mock_pushover):
    _create(client)
    assert mock_pushover.sent == []


def test_a_modification_says_so(client, token, reachable, make_event, mock_pushover):
    event = make_event("Dentist", start="2026-08-14T09:00", attendees=[reachable])

    response = client.put(f"/api/events/{event.id}", json={"start": "2026-08-14T10:30"})

    assert response.status_code == 200
    assert mock_pushover.sent[0]["title"] == "Calendar Modification: Dentist"
    assert "When: 2026-08-14 · 10:30 to 11:30 AM CDT" in mock_pushover.sent[0]["message"]


def test_modifying_one_occurrence_names_that_occurrence_not_the_next_one(
    client, token, reachable, make_event, mock_pushover
):
    """A change to next Tuesday must not be announced as this Tuesday."""
    event = make_event(
        "Scouts",
        start="2026-08-04T19:00",
        rrule="FREQ=WEEKLY;BYDAY=TU;COUNT=10",
        attendees=[reachable],
    )

    response = client.put(
        f"/api/events/{event.id}",
        params={"scope": "this", "occurrence_date": "2026-08-18"},
        json={"start": "2026-08-18T17:30", "end": "2026-08-18T18:30"},
    )

    assert response.status_code == 200
    assert "When: 2026-08-18 · 5:30 to 6:30 PM CDT" in mock_pushover.sent[0]["message"]


def test_modifying_the_rest_of_a_series_announces_the_edited_tail(
    client, token, reachable, make_event, mock_pushover
):
    event = make_event(
        "Scouts",
        start="2026-08-04T19:00",
        rrule="FREQ=WEEKLY;BYDAY=TU;COUNT=10",
        attendees=[reachable],
    )

    response = client.put(
        f"/api/events/{event.id}",
        params={"scope": "following", "occurrence_date": "2026-08-18"},
        json={"title": "Scouts (new time)", "start": "2026-08-18T17:30"},
    )

    assert response.status_code == 200
    assert mock_pushover.sent[0]["title"] == "Calendar Modification: Scouts (new time)"
    assert "When: 2026-08-18 · 5:30 to 6:30 PM CDT" in mock_pushover.sent[0]["message"]


def test_deleting_an_event_announces_the_deletion(
    client, token, reachable, make_event, mock_pushover
):
    """The notice is built before the delete and sent after it."""
    event = make_event(
        "Dentist", start="2026-08-14T09:00", location="Dr. Kim", attendees=[reachable]
    )

    assert client.delete(f"/api/events/{event.id}").status_code == 204

    assert mock_pushover.sent[0]["title"] == "Calendar Deletion: Dentist"
    assert mock_pushover.sent[0]["message"] == (
        "When: 2026-08-14 · 9:00 to 10:00 AM CDT\nWhere: Dr. Kim\nAttendees: Emma"
    )


def test_deleting_an_event_leaves_no_orphaned_notification_row(
    client, db_session, token, reachable, make_event, mock_pushover
):
    """The cascade removes this event's rows; the notice must not re-add one."""
    event = make_event("Dentist", start="2026-08-14T09:00", attendees=[reachable])

    client.delete(f"/api/events/{event.id}")

    assert len(mock_pushover.sent) == 1
    assert db_session.query(EventNotification).count() == 0


def test_deleting_one_occurrence_names_that_occurrence(
    client, token, reachable, make_event, mock_pushover
):
    """Cancelling next Tuesday must not be announced as this Tuesday."""
    event = make_event(
        "Scouts",
        start="2026-08-04T19:00",
        rrule="FREQ=WEEKLY;BYDAY=TU;COUNT=10",
        attendees=[reachable],
    )

    response = client.delete(
        f"/api/events/{event.id}", params={"scope": "this", "occurrence_date": "2026-08-18"}
    )

    assert response.status_code == 204
    assert mock_pushover.sent[0]["title"] == "Calendar Deletion: Scouts"
    assert "When: 2026-08-18 · 7:00 to 8:00 PM CDT" in mock_pushover.sent[0]["message"]


def test_deleting_the_rest_of_a_series_names_the_first_occurrence_removed(
    client, token, reachable, make_event, mock_pushover
):
    event = make_event(
        "Scouts",
        start="2026-08-04T19:00",
        rrule="FREQ=WEEKLY;BYDAY=TU;COUNT=10",
        attendees=[reachable],
    )

    response = client.delete(
        f"/api/events/{event.id}", params={"scope": "following", "occurrence_date": "2026-08-18"}
    )

    assert response.status_code == 204
    assert "When: 2026-08-18 · 7:00 to 8:00 PM CDT" in mock_pushover.sent[0]["message"]


def test_a_deletion_notice_records_against_a_surviving_series(
    client, db_session, token, reachable, make_event, mock_pushover
):
    """Cancelling one occurrence leaves the event, so the row has a home."""
    event = make_event(
        "Scouts",
        start="2026-08-04T19:00",
        rrule="FREQ=WEEKLY;BYDAY=TU;COUNT=10",
        attendees=[reachable],
    )

    client.delete(
        f"/api/events/{event.id}", params={"scope": "this", "occurrence_date": "2026-08-18"}
    )

    row = db_session.query(EventNotification).one()
    assert (row.kind, row.status, row.occurrence_date) == (KIND_DELETED, "sent", "2026-08-18")


def test_the_change_still_happens_when_pushover_is_down(
    client, db_session, token, reachable, mock_pushover
):
    """The calendar is the product; the push is a courtesy."""
    mock_pushover.fail_with("service unavailable")

    created = _create(client, attendee_ids=[reachable.id])

    assert client.get(f"/api/events/{created['id']}").status_code == 200
    row = db_session.query(EventNotification).filter(EventNotification.kind == KIND_CREATED).one()
    assert row.status == "failed"


def test_a_deletion_still_happens_when_pushover_is_down(
    client, token, reachable, make_event, mock_pushover
):
    event = make_event("Dentist", start="2026-08-14T09:00", attendees=[reachable])
    mock_pushover.fail_with("service unavailable")

    assert client.delete(f"/api/events/{event.id}").status_code == 204
    assert client.get(f"/api/events/{event.id}").status_code == 404


def test_a_notice_records_who_it_reached(client, db_session, token, reachable, mock_pushover):
    created = _create(client, attendee_ids=[reachable.id])

    row = db_session.query(EventNotification).one()
    assert (row.event_id, row.kind, row.status) == (created["id"], KIND_CREATED, "sent")
    assert row.occurrence_date == "2026-08-14"


def test_change_notices_do_not_consume_the_reminder_slot(
    client, db_session, token, reachable, mock_pushover
):
    """Every row keys on the same occurrence; only ``kind`` separates them."""
    created = _create(
        client, start="2026-08-11T13:00", end="2026-08-11T14:00", attendee_ids=[reachable.id]
    )
    client.put(f"/api/events/{created['id']}", json={"notify_minutes_before": 30})

    assert check_due_reminders(db_session, now=_at("2026-08-11T12:30")) == 1
    kinds = {row.kind for row in db_session.query(EventNotification).all()}
    assert kinds == {KIND_CREATED, KIND_UPDATED, KIND_REMINDER}


# --- Connectivity tests --------------------------------------------------------


def test_member_pushover_test_sends_a_real_message(client, token, reachable, mock_pushover):
    response = client.post(f"/api/family/{reachable.id}/test-pushover")
    assert response.json()["success"] is True
    assert len(mock_pushover.sent) == 1


def test_member_pushover_test_without_a_key_explains_itself(client, token, unreachable):
    response = client.post(f"/api/family/{unreachable.id}/test-pushover")
    assert response.json() == {"success": False, "error": "Jon has no Pushover user key"}


def test_member_pushover_test_reports_a_provider_error(client, token, reachable, mock_pushover):
    mock_pushover.fail_with("application token is invalid")
    response = client.post(f"/api/family/{reachable.id}/test-pushover")
    assert response.json() == {"success": False, "error": "application token is invalid"}


def test_settings_pushover_test_needs_somebody_to_send_to(client, token, unreachable):
    response = client.post("/api/settings/test-pushover")
    assert response.json()["success"] is False
    assert "no family member" in response.json()["error"].lower()


def test_settings_pushover_test_succeeds(client, token, reachable, mock_pushover):
    response = client.post("/api/settings/test-pushover")
    assert response.json()["success"] is True
    assert "Emma" in response.json()["message"]


def test_settings_pushover_test_without_a_token(client, reachable):
    response = client.post("/api/settings/test-pushover")
    assert response.json() == {"success": False, "error": "Missing Pushover application token"}
