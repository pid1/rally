"""Tests for the push that goes out when a task is assigned to somebody.

The transport is always stubbed — no test may reach the real API. What is under
test is the policy: who gets a push, when a write counts as a hand-over, and
that a provider outage cannot fail the task write it rides on.
"""

from datetime import UTC, datetime

import pytest

from rally import notification_prefs
from rally.models import MemberNotificationPref, Todo
from rally.todo_notifications import due_label, notify_assignment

TODAY = datetime(2026, 8, 19, 12, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _tz(local_timezone):
    local_timezone("America/Chicago")


@pytest.fixture(autouse=True)
def _clock(frozen_now):
    frozen_now(TODAY)


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


def _create(client, **overrides) -> dict:
    payload = {"title": "Take out the trash"}
    payload.update(overrides)
    response = client.post("/api/todos", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


# --- Who hears about it --------------------------------------------------------


def test_assigning_a_new_task_pushes_to_the_assignee(client, token, reachable, mock_pushover):
    _create(client, assigned_to=reachable.id)

    assert len(mock_pushover.sent) == 1
    push = mock_pushover.sent[0]
    assert push["user"] == "emma-key"
    assert push["title"] == "New Task: Take out the trash"


def test_an_unassigned_task_pushes_to_nobody(client, token, reachable, mock_pushover):
    _create(client)

    assert mock_pushover.sent == []


def test_only_the_assignee_hears_about_it(client, token, reachable, make_member, mock_pushover):
    make_member("Dad", pushover_user_key="dad-key")
    _create(client, assigned_to=reachable.id)

    assert [p["user"] for p in mock_pushover.sent] == ["emma-key"]


def test_an_assignee_without_a_key_is_skipped_not_failed(
    client, token, unreachable, mock_pushover, db_session
):
    _create(client, assigned_to=unreachable.id)

    assert mock_pushover.sent == []
    assert db_session.query(Todo).count() == 1


def test_the_members_device_is_used_when_set(client, token, make_member, mock_pushover):
    member = make_member("Emma", pushover_user_key="emma-key", pushover_device="phone")
    _create(client, assigned_to=member.id)

    assert mock_pushover.sent[0]["device"] == "phone"


# --- What counts as a hand-over ------------------------------------------------


def test_reassigning_pushes_to_the_new_assignee(
    client, token, reachable, make_member, mock_pushover
):
    dad = make_member("Dad", pushover_user_key="dad-key")
    todo = _create(client, assigned_to=dad.id)
    mock_pushover.sent.clear()

    response = client.put(f"/api/todos/{todo['id']}", json={"assigned_to": reachable.id})
    assert response.status_code == 200

    assert [p["user"] for p in mock_pushover.sent] == ["emma-key"]


def test_taking_an_unassigned_task_and_giving_it_to_somebody_pushes(
    client, token, reachable, mock_pushover
):
    todo = _create(client)

    client.put(f"/api/todos/{todo['id']}", json={"assigned_to": reachable.id})

    assert len(mock_pushover.sent) == 1


def test_editing_a_task_without_changing_the_assignee_stays_silent(
    client, token, reachable, mock_pushover
):
    todo = _create(client, assigned_to=reachable.id)
    mock_pushover.sent.clear()

    response = client.put(
        f"/api/todos/{todo['id']}",
        json={"title": "Take out the recycling", "assigned_to": reachable.id},
    )
    assert response.status_code == 200

    assert mock_pushover.sent == []


def test_clearing_the_assignee_pushes_to_nobody(client, token, reachable, mock_pushover):
    todo = _create(client, assigned_to=reachable.id)
    mock_pushover.sent.clear()

    client.put(f"/api/todos/{todo['id']}", json={"assigned_to": None})

    assert mock_pushover.sent == []


def test_completing_a_task_does_not_re_announce_it(client, token, reachable, mock_pushover):
    todo = _create(client, assigned_to=reachable.id)
    mock_pushover.sent.clear()

    client.put(f"/api/todos/{todo['id']}", json={"completed": True})

    assert mock_pushover.sent == []


def test_a_completed_task_handed_over_is_not_announced(
    client, token, reachable, make_member, mock_pushover
):
    """Reassigning something already done is bookkeeping, not work."""
    dad = make_member("Dad", pushover_user_key="dad-key")
    todo = _create(client, assigned_to=dad.id)
    client.put(f"/api/todos/{todo['id']}", json={"completed": True})
    mock_pushover.sent.clear()

    client.put(f"/api/todos/{todo['id']}", json={"assigned_to": reachable.id})

    assert mock_pushover.sent == []


def test_a_generated_recurring_instance_is_not_announced(
    client, token, reachable, make_recurring_todo, mock_pushover, db_session
):
    """The hand-over happened when the template was written, not every morning."""
    make_recurring_todo("Feed the dog", recurrence_type="daily", assigned_to=reachable.id)

    response = client.get("/api/todos")
    assert response.status_code == 200
    assert any(t["title"] == "Feed the dog" for t in response.json())

    assert mock_pushover.sent == []


# --- The message ---------------------------------------------------------------


def test_a_task_due_today_says_so(client, token, reachable, mock_pushover):
    _create(client, assigned_to=reachable.id, due_date="2026-08-19")

    assert mock_pushover.sent[0]["message"] == "Due today"


def test_a_task_due_tomorrow_says_so(client, token, reachable, mock_pushover):
    _create(client, assigned_to=reachable.id, due_date="2026-08-20")

    assert mock_pushover.sent[0]["message"] == "Due tomorrow"


def test_a_task_due_this_week_is_named_by_weekday(client, token, reachable, mock_pushover):
    _create(client, assigned_to=reachable.id, due_date="2026-08-22")

    assert mock_pushover.sent[0]["message"] == "Due Saturday"


def test_a_distant_due_date_is_named_by_date(client, token, reachable, mock_pushover):
    _create(client, assigned_to=reachable.id, due_date="2026-09-30")

    assert mock_pushover.sent[0]["message"] == "Due Sep 30"


def test_inheriting_something_late_says_it_is_overdue(client, token, reachable, mock_pushover):
    _create(client, assigned_to=reachable.id, due_date="2026-08-14")

    assert mock_pushover.sent[0]["message"] == "Overdue since Aug 14"


def test_a_due_date_in_another_year_carries_the_year(client, token, reachable, mock_pushover):
    _create(client, assigned_to=reachable.id, due_date="2027-01-04")

    assert mock_pushover.sent[0]["message"] == "Due Jan 4, 2027"


def test_the_description_rides_along(client, token, reachable, mock_pushover):
    _create(
        client,
        assigned_to=reachable.id,
        due_date="2026-08-19",
        description="Bins go out by the curb",
    )

    assert mock_pushover.sent[0]["message"] == "Due today\nBins go out by the curb"


def test_a_task_with_nothing_to_add_still_has_a_body(client, token, reachable, mock_pushover):
    """Pushover rejects an empty message, so there is always something to say."""
    _create(client, assigned_to=reachable.id)

    assert mock_pushover.sent[0]["message"] == "It's on your list."


def test_a_very_long_description_is_trimmed_rather_than_rejected(
    client, token, reachable, mock_pushover
):
    _create(client, assigned_to=reachable.id, description="x" * 2000)

    message = mock_pushover.sent[0]["message"]
    assert len(message) <= 1024
    assert message.endswith("…")


def test_due_label_ignores_a_malformed_date():
    from datetime import date

    assert due_label("not-a-date", date(2026, 8, 19)) is None


# --- Failure is data -----------------------------------------------------------


def test_the_toggle_turns_the_pushes_off(client, token, reachable, make_setting, mock_pushover):
    make_setting("todo_notify_enabled", "false")

    _create(client, assigned_to=reachable.id)

    assert mock_pushover.sent == []


def test_a_missing_app_token_skips_cleanly(client, reachable, mock_pushover, db_session):
    _create(client, assigned_to=reachable.id)

    assert mock_pushover.sent == []
    assert db_session.query(Todo).count() == 1


def test_a_pushover_failure_never_fails_the_write(
    client, token, reachable, mock_pushover, db_session
):
    mock_pushover.fail_with("service unavailable")

    _create(client, assigned_to=reachable.id)

    assert db_session.query(Todo).count() == 1


def test_a_failure_is_reported_by_name(db_session, token, reachable, mock_pushover):
    todo = Todo(title="Take out the trash", assigned_to=reachable.id, completed=False)
    db_session.add(todo)
    db_session.commit()
    mock_pushover.fail_with("service unavailable")

    result = notify_assignment(db_session, todo)

    assert result["failed"] == ["Emma"]
    assert result["sent"] == []


def test_an_assignee_who_no_longer_exists_is_not_a_crash(db_session, token, mock_pushover):
    todo = Todo(title="Take out the trash", assigned_to=999, completed=False)
    db_session.add(todo)
    db_session.commit()

    result = notify_assignment(db_session, todo)

    assert result["sent"] == []
    assert result["skipped_reason"] == "the assignee no longer exists"


# --- Per-member preferences ----------------------------------------------------


def test_an_assignee_who_muted_hand_offs_is_not_pushed(
    client, db_session, token, reachable, mock_pushover
):
    db_session.add(
        MemberNotificationPref(
            family_member_id=reachable.id,
            kind=notification_prefs.TASK_ASSIGNMENT,
            enabled=False,
        )
    )
    db_session.commit()

    _create(client, assigned_to=reachable.id)

    assert mock_pushover.sent == []


def test_a_muted_assignee_is_named_rather_than_dropped(db_session, token, reachable):
    """*Muted* and *skipped* are different answers to a silent phone."""
    db_session.add(
        MemberNotificationPref(
            family_member_id=reachable.id,
            kind=notification_prefs.TASK_ASSIGNMENT,
            enabled=False,
        )
    )
    db_session.commit()
    todo = Todo(title="Take out the trash", assigned_to=reachable.id)
    db_session.add(todo)
    db_session.commit()

    result = notify_assignment(db_session, todo)

    assert result["muted"] == ["Emma"]
    assert result["skipped"] == []
    assert result["skipped_reason"] == "the assignee has task hand-offs turned off"


def test_muting_hand_offs_does_not_mute_that_persons_reminders(
    client, db_session, token, reachable, mock_pushover
):
    db_session.add(
        MemberNotificationPref(
            family_member_id=reachable.id,
            kind=notification_prefs.TASK_ASSIGNMENT,
            enabled=False,
        )
    )
    db_session.commit()

    assert (
        notification_prefs.wants(db_session, reachable, notification_prefs.EVENT_REMINDER) is True
    )
