"""Tests for the todos router CRUD endpoints and the today-cutoff helper.

Complements test_completed_todos.py (which covers GET /api/todos/completed) by
covering the rest of the router: today_start_utc, POST, GET (list + by id),
PUT (partial updates via the UNSET sentinel, and the completed_at transition),
and DELETE.
"""

from datetime import UTC, datetime

from rally.models import Todo
from rally.routers.todos import today_start_utc

NOON = datetime(2026, 3, 15, 12, 0, tzinfo=UTC)


# --- today_start_utc -----------------------------------------------------------


def test_today_start_utc_defaults_to_utc(db_session, frozen_now):
    frozen_now(NOON)
    # No local_timezone setting -> UTC; local midnight today is 2026-03-15 00:00Z.
    assert today_start_utc(db_session) == datetime(2026, 3, 15, 0, 0, tzinfo=UTC)


def test_today_start_utc_respects_local_timezone(db_session, make_setting, frozen_now):
    frozen_now(NOON)
    make_setting("local_timezone", "Asia/Kolkata")  # UTC+05:30, no DST
    # Local midnight Mar 15 in +05:30 is 18:30Z the previous day.
    assert today_start_utc(db_session) == datetime(2026, 3, 14, 18, 30, tzinfo=UTC)


# --- POST /api/todos -----------------------------------------------------------


def test_create_todo_defaults_incomplete(client):
    resp = client.post("/api/todos", json={"title": "Buy milk"})

    assert resp.status_code == 201
    body = resp.json()
    assert body["title"] == "Buy milk"
    assert body["completed"] is False
    assert body["completed_at"] is None
    assert body["id"] > 0


def test_create_todo_persists_all_fields(client, make_member):
    dad = make_member("Dad")

    resp = client.post(
        "/api/todos",
        json={
            "title": "Dentist",
            "description": "cleaning",
            "due_date": "2026-05-01",
            "assigned_to": dad.id,
            "remind_days_before": 3,
        },
    )

    assert resp.status_code == 201
    body = resp.json()
    assert body["description"] == "cleaning"
    assert body["due_date"] == "2026-05-01"
    assert body["assigned_to"] == dad.id
    assert body["remind_days_before"] == 3


# --- GET /api/todos (list) -----------------------------------------------------


def test_list_orders_by_created_desc(client, make_todo):
    make_todo("Older", completed=False, completed_at=None, created_at=datetime(2026, 1, 1))
    make_todo("Newer", completed=False, completed_at=None, created_at=datetime(2026, 1, 2))

    titles = [t["title"] for t in client.get("/api/todos").json()]

    assert titles == ["Newer", "Older"]


def test_list_hides_completed_before_today(client, make_todo, frozen_now):
    frozen_now(NOON)
    make_todo("Open", completed=False, completed_at=None)
    make_todo("Done old", completed=True, completed_at=datetime(2020, 1, 1))
    make_todo("Done today", completed=True, completed_at=datetime(2026, 3, 15, 9, 0))

    titles = {t["title"] for t in client.get("/api/todos").json()}

    # Completed-before-today is hidden; open and completed-today remain.
    assert titles == {"Open", "Done today"}


def test_list_include_hidden_shows_completed_before_today(client, make_todo, frozen_now):
    frozen_now(NOON)
    make_todo("Done old", completed=True, completed_at=datetime(2020, 1, 1))

    titles = [
        t["title"] for t in client.get("/api/todos", params={"include_hidden": "true"}).json()
    ]

    assert "Done old" in titles


def test_list_runs_recurring_generation(client, db_session, make_recurring_todo, frozen_now):
    # GET /api/todos calls process_recurring_todos as a side effect.
    frozen_now(NOON)
    make_recurring_todo("Vitamins", recurrence_type="daily", has_due_date=True)

    titles = [t["title"] for t in client.get("/api/todos").json()]

    assert "Vitamins" in titles
    assert db_session.query(Todo).filter(Todo.title == "Vitamins").count() == 1


# --- GET /api/todos/{id} -------------------------------------------------------


def test_get_todo_found(client, make_todo):
    todo = make_todo("Task", completed=False, completed_at=None)

    resp = client.get(f"/api/todos/{todo.id}")

    assert resp.status_code == 200
    assert resp.json()["id"] == todo.id


def test_get_todo_missing_returns_404(client):
    assert client.get("/api/todos/99999").status_code == 404


# --- PUT /api/todos/{id} -------------------------------------------------------


def test_update_complete_sets_completed_at(client, make_todo, frozen_now):
    frozen_now(datetime(2026, 3, 15, 9, 30, tzinfo=UTC))
    todo = make_todo("Task", completed=False, completed_at=None)

    body = client.put(f"/api/todos/{todo.id}", json={"completed": True}).json()

    assert body["completed"] is True
    assert body["completed_at"].startswith("2026-03-15T09:30")


def test_update_reopen_clears_completed_at(client, make_todo):
    todo = make_todo("Task", completed=True, completed_at=datetime(2026, 3, 15, 9, 30))

    body = client.put(f"/api/todos/{todo.id}", json={"completed": False}).json()

    assert body["completed"] is False
    assert body["completed_at"] is None


def test_update_omitted_fields_are_untouched(client, make_todo):
    todo = make_todo(
        "Task", completed=False, completed_at=None, due_date="2026-05-01", description="orig"
    )

    body = client.put(f"/api/todos/{todo.id}", json={"title": "Renamed"}).json()

    assert body["title"] == "Renamed"
    # due_date defaults to UNSET when omitted -> left as-is.
    assert body["due_date"] == "2026-05-01"
    assert body["description"] == "orig"


def test_update_explicit_null_clears_unset_field(client, make_todo, make_member):
    dad = make_member("Dad")
    todo = make_todo(
        "Task", completed=False, completed_at=None, due_date="2026-05-01", assigned_to=dad.id
    )

    body = client.put(f"/api/todos/{todo.id}", json={"due_date": None, "assigned_to": None}).json()

    # Explicit null (not UNSET) clears these fields.
    assert body["due_date"] is None
    assert body["assigned_to"] is None


def test_update_description_and_remind_days(client, make_todo):
    todo = make_todo("Task", completed=False, completed_at=None, description="orig")

    body = client.put(
        f"/api/todos/{todo.id}", json={"description": "new", "remind_days_before": 2}
    ).json()

    assert body["description"] == "new"
    assert body["remind_days_before"] == 2


def test_update_missing_returns_404(client):
    assert client.put("/api/todos/99999", json={"title": "x"}).status_code == 404


# --- DELETE /api/todos/{id} ----------------------------------------------------


def test_delete_todo(client, db_session, make_todo):
    todo = make_todo("Task", completed=False, completed_at=None)

    resp = client.delete(f"/api/todos/{todo.id}")

    assert resp.status_code == 204
    assert db_session.query(Todo).filter(Todo.id == todo.id).count() == 0


def test_delete_missing_returns_404(client):
    assert client.delete("/api/todos/99999").status_code == 404
