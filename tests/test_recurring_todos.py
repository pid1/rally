"""Tests for the recurring-todos router CRUD and its last-completion aggregation.

The recurrence date math lives in rally.recurrence (covered separately); this
covers the HTTP endpoints that manage recurring templates plus the list
endpoint's per-template "last completed" rollup and timezone handling.
"""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from rally.models import RecurringTodo
from rally.routers.recurring_todos import format_local_completion


def _create(client, **fields):
    payload = {"title": "Water plants", "recurrence_type": "daily"}
    payload.update(fields)
    resp = client.post("/api/recurring-todos", json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


# --- create --------------------------------------------------------------------


def test_create_defaults(client):
    body = _create(client)
    assert body["title"] == "Water plants"
    assert body["recurrence_type"] == "daily"
    assert body["has_due_date"] is False
    assert body["active"] is True
    assert body["id"] > 0


def test_create_with_all_fields(client, make_member):
    dad = make_member("Dad")
    body = _create(
        client,
        title="Trash",
        description="bins to curb",
        recurrence_type="weekly",
        recurrence_day=2,
        assigned_to=dad.id,
        has_due_date=True,
        remind_days_before=1,
        custom_rule={"freq": "weekly", "weekdays": [2]},
    )
    assert body["recurrence_type"] == "weekly"
    assert body["recurrence_day"] == 2
    assert body["assigned_to"] == dad.id
    assert body["has_due_date"] is True
    assert body["remind_days_before"] == 1
    assert body["custom_rule"] == {"freq": "weekly", "weekdays": [2]}


# --- list ----------------------------------------------------------------------


def test_list_orders_by_created_desc(client, make_recurring_todo):
    make_recurring_todo("Older", created_at=datetime(2026, 1, 1))
    make_recurring_todo("Newer", created_at=datetime(2026, 1, 2))

    titles = [r["title"] for r in client.get("/api/recurring-todos").json()]

    assert titles == ["Newer", "Older"]


def test_list_without_completions_has_null_last_completed(client, make_recurring_todo):
    make_recurring_todo("Vitamins")

    row = client.get("/api/recurring-todos").json()[0]

    assert row["last_completed_at"] is None
    assert row["last_completed_date"] is None


def test_list_aggregates_most_recent_completion(client, make_recurring_todo, make_todo):
    rt = make_recurring_todo("Vitamins")
    make_todo(
        "Vitamins",
        completed=True,
        completed_at=datetime(2026, 3, 10, 8, 0),
        recurring_todo_id=rt.id,
    )
    make_todo(
        "Vitamins",
        completed=True,
        completed_at=datetime(2026, 3, 12, 9, 0),
        recurring_todo_id=rt.id,
    )
    # An open instance must not count toward "last completed".
    make_todo("Vitamins", completed=False, completed_at=None, recurring_todo_id=rt.id)

    row = client.get("/api/recurring-todos").json()[0]

    assert row["last_completed_date"] == "2026-03-12"
    assert row["last_completed_at"].startswith("2026-03-12T09:00")


def test_list_last_completed_date_uses_local_timezone(
    client, make_recurring_todo, make_todo, make_setting
):
    make_setting("local_timezone", "Asia/Kolkata")  # UTC+05:30
    rt = make_recurring_todo("Vitamins")
    # 20:00 UTC is 01:30 the next local day in Kolkata.
    make_todo(
        "Vitamins",
        completed=True,
        completed_at=datetime(2026, 3, 12, 20, 0),
        recurring_todo_id=rt.id,
    )

    row = client.get("/api/recurring-todos").json()[0]

    assert row["last_completed_date"] == "2026-03-13"


# --- get -----------------------------------------------------------------------


def test_get_found_and_404(client, make_recurring_todo):
    rt = make_recurring_todo("Vitamins")
    assert client.get(f"/api/recurring-todos/{rt.id}").json()["id"] == rt.id
    assert client.get("/api/recurring-todos/9999").status_code == 404


# --- update --------------------------------------------------------------------


def test_update_none_checked_fields(client, make_recurring_todo):
    rt = make_recurring_todo("Vitamins", recurrence_type="daily", has_due_date=False, active=True)

    body = client.put(
        f"/api/recurring-todos/{rt.id}",
        json={
            "title": "Multivitamins",
            "description": "with breakfast",
            "recurrence_type": "weekly",
            "recurrence_day": 3,
            "has_due_date": True,
            "active": False,
        },
    ).json()

    assert body["title"] == "Multivitamins"
    assert body["description"] == "with breakfast"
    assert body["recurrence_type"] == "weekly"
    assert body["recurrence_day"] == 3
    assert body["has_due_date"] is True
    assert body["active"] is False


def test_update_unset_semantics(client, make_recurring_todo, make_member):
    dad = make_member("Dad")
    rt = make_recurring_todo("Vitamins", custom_rule={"freq": "daily"})

    # Set the UNSET-guarded fields.
    body = client.put(
        f"/api/recurring-todos/{rt.id}",
        json={
            "assigned_to": dad.id,
            "remind_days_before": 2,
            "custom_rule": {"freq": "weekly", "weekdays": [1]},
        },
    ).json()
    assert body["assigned_to"] == dad.id
    assert body["remind_days_before"] == 2
    assert body["custom_rule"] == {"freq": "weekly", "weekdays": [1]}

    # Omit them -> untouched.
    body = client.put(f"/api/recurring-todos/{rt.id}", json={"title": "Renamed"}).json()
    assert body["assigned_to"] == dad.id
    assert body["custom_rule"] == {"freq": "weekly", "weekdays": [1]}

    # Explicit null -> cleared.
    body = client.put(
        f"/api/recurring-todos/{rt.id}",
        json={"assigned_to": None, "remind_days_before": None, "custom_rule": None},
    ).json()
    assert body["assigned_to"] is None
    assert body["remind_days_before"] is None
    assert body["custom_rule"] is None


def test_update_404(client):
    assert client.put("/api/recurring-todos/9999", json={"title": "x"}).status_code == 404


# --- delete --------------------------------------------------------------------


def test_delete_and_404(client, db_session, make_recurring_todo):
    rt = make_recurring_todo("Vitamins")

    assert client.delete(f"/api/recurring-todos/{rt.id}").status_code == 204
    assert db_session.get(RecurringTodo, rt.id) is None

    assert client.delete("/api/recurring-todos/9999").status_code == 404


# --- format_local_completion (module helper) -----------------------------------


def test_format_local_completion_today(frozen_now):
    frozen_now(datetime(2026, 3, 15, 15, 0, tzinfo=UTC))
    out = format_local_completion(datetime(2026, 3, 15, 14, 30, tzinfo=UTC), ZoneInfo("UTC"))
    assert out == "Today at 2:30 PM"


def test_format_local_completion_yesterday(frozen_now):
    frozen_now(datetime(2026, 3, 15, 15, 0, tzinfo=UTC))
    out = format_local_completion(datetime(2026, 3, 14, 9, 5, tzinfo=UTC), ZoneInfo("UTC"))
    assert out == "Yesterday at 9:05 AM"


def test_format_local_completion_older_date_ordinal(frozen_now):
    frozen_now(datetime(2026, 3, 15, 15, 0, tzinfo=UTC))
    out = format_local_completion(datetime(2026, 3, 3, 10, 0, tzinfo=UTC), ZoneInfo("UTC"))
    assert out == "Mar 3rd, 2026 at 10:00 AM"


def test_format_local_completion_teens_ordinal(frozen_now):
    frozen_now(datetime(2026, 3, 20, 15, 0, tzinfo=UTC))
    out = format_local_completion(datetime(2026, 3, 12, 13, 0, tzinfo=UTC), ZoneInfo("UTC"))
    assert out == "Mar 12th, 2026 at 1:00 PM"
