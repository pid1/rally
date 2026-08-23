"""Tests for the recurring-todos router CRUD and its last-completion aggregation.

The recurrence date math lives in rally.recurrence (covered separately); this
covers the HTTP endpoints that manage recurring templates plus the list
endpoint's per-template "last completed" rollup and timezone handling.
"""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from rally.models import RecurringTodo, Todo
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


# --- start date ----------------------------------------------------------------


def test_create_with_start_date_reads_back(client):
    body = _create(client, title="Replace smoke detector battery", start_date="2027-01-01")

    assert body["start_date"] == "2027-01-01"
    assert client.get(f"/api/recurring-todos/{body['id']}").json()["start_date"] == "2027-01-01"


def test_create_without_start_date_is_null(client):
    assert _create(client)["start_date"] is None


def test_create_rejects_a_malformed_start_date(client):
    resp = client.post(
        "/api/recurring-todos",
        json={"title": "Batteries", "recurrence_type": "daily", "start_date": "next January"},
    )

    assert resp.status_code == 422
    assert resp.json()["detail"] == "start_date must be YYYY-MM-DD"


def test_create_rejects_a_start_date_that_is_not_yyyy_mm_dd(client):
    # date.fromisoformat() would take the compact form; the column is not it.
    resp = client.post(
        "/api/recurring-todos",
        json={"title": "Batteries", "recurrence_type": "daily", "start_date": "20270101"},
    )

    assert resp.status_code == 422


def test_update_start_date_while_nothing_is_generated(client, make_recurring_todo):
    rt = make_recurring_todo("Batteries", start_date="2026-01-01")

    body = client.put(f"/api/recurring-todos/{rt.id}", json={"start_date": "2027-01-01"}).json()

    assert body["start_date"] == "2027-01-01"
    assert body["last_generated_date"] is None


def test_update_start_date_unset_semantics(client, make_recurring_todo):
    rt = make_recurring_todo("Batteries", start_date="2027-01-01")

    # Omitted -> untouched.
    assert (
        client.put(f"/api/recurring-todos/{rt.id}", json={"title": "Renamed"}).json()["start_date"]
        == "2027-01-01"
    )

    # Explicit null -> cleared.
    assert (
        client.put(f"/api/recurring-todos/{rt.id}", json={"start_date": None}).json()["start_date"]
        is None
    )


def test_update_rejects_a_malformed_start_date(client, make_recurring_todo):
    rt = make_recurring_todo("Batteries")

    resp = client.put(f"/api/recurring-todos/{rt.id}", json={"start_date": "01/01/2027"})

    assert resp.status_code == 422


def test_update_after_generation_reschedules_the_open_instance(
    client, db_session, make_recurring_todo, make_todo, frozen_now
):
    # The "I picked the wrong year" correction: the template owns the anchor, so
    # moving the start date moves the open instance and last_generated_date with it.
    frozen_now(datetime(2026, 8, 22, 12, tzinfo=UTC))
    rt = make_recurring_todo(
        "Replace smoke detector battery",
        recurrence_type="custom",
        custom_rule={"freq": "monthly", "interval": 12, "mode": "day", "day": 1},
        has_due_date=True,
        start_date="2026-09-01",
        last_generated_date="2026-09-01",
    )
    instance = make_todo(
        "Replace smoke detector battery",
        completed=False,
        completed_at=None,
        due_date="2026-09-01",
        recurring_todo_id=rt.id,
    )

    body = client.put(f"/api/recurring-todos/{rt.id}", json={"start_date": "2027-01-01"}).json()

    assert body["start_date"] == "2027-01-01"
    assert body["last_generated_date"] == "2027-01-01"
    db_session.refresh(instance)
    assert instance.due_date == "2027-01-01"


def test_update_after_generation_leaves_a_dateless_instance_alone(
    client, db_session, make_recurring_todo, make_todo, frozen_now
):
    # has_due_date is off, so the instance never carried a date to rewrite; only
    # the anchor moves.
    frozen_now(datetime(2026, 8, 22, 12, tzinfo=UTC))
    rt = make_recurring_todo(
        "Stretch",
        recurrence_type="daily",
        has_due_date=False,
        last_generated_date="2026-08-22",
    )
    instance = make_todo(
        "Stretch", completed=False, completed_at=None, due_date=None, recurring_todo_id=rt.id
    )

    body = client.put(f"/api/recurring-todos/{rt.id}", json={"start_date": "2026-10-01"}).json()

    assert body["last_generated_date"] == "2026-10-01"
    db_session.refresh(instance)
    assert instance.due_date is None


def test_update_start_date_refused_after_a_completion(client, make_recurring_todo, make_todo):
    rt = make_recurring_todo("Batteries", start_date="2026-01-01", last_generated_date="2026-01-01")
    make_todo(
        "Batteries",
        completed=True,
        completed_at=datetime(2026, 1, 1, 9, 0),
        due_date="2026-01-01",
        recurring_todo_id=rt.id,
    )

    resp = client.put(f"/api/recurring-todos/{rt.id}", json={"start_date": "2027-01-01"})

    assert resp.status_code == 409
    assert "completed" in resp.json()["detail"]
    assert client.get(f"/api/recurring-todos/{rt.id}").json()["start_date"] == "2026-01-01"


def test_update_a_completed_series_with_its_own_start_date_is_not_a_change(
    client, make_recurring_todo, make_todo
):
    # The modal shows the locked field's value; re-sending it must not refuse an
    # edit to the rest of the form.
    rt = make_recurring_todo("Batteries", start_date="2026-01-01", last_generated_date="2026-01-01")
    make_todo(
        "Batteries",
        completed=True,
        completed_at=datetime(2026, 1, 1, 9, 0),
        due_date="2026-01-01",
        recurring_todo_id=rt.id,
    )

    body = client.put(
        f"/api/recurring-todos/{rt.id}",
        json={"title": "Replace batteries", "start_date": "2026-01-01"},
    )

    assert body.status_code == 200
    assert body.json()["title"] == "Replace batteries"


def test_update_start_date_does_not_touch_a_completed_instance(
    client, db_session, make_recurring_todo, make_todo, frozen_now
):
    # Only the *open* instance is re-dated; history is history.
    frozen_now(datetime(2026, 8, 22, 12, tzinfo=UTC))
    rt = make_recurring_todo(
        "Vitamins",
        recurrence_type="daily",
        has_due_date=True,
        last_generated_date="2026-08-22",
    )
    open_instance = make_todo(
        "Vitamins",
        completed=False,
        completed_at=None,
        due_date="2026-08-22",
        recurring_todo_id=rt.id,
    )

    client.put(f"/api/recurring-todos/{rt.id}", json={"start_date": "2026-09-01"})

    db_session.refresh(open_instance)
    assert open_instance.due_date == "2026-09-01"
    assert db_session.query(Todo).filter(Todo.completed == True).count() == 0  # noqa: E712


# --- preview -------------------------------------------------------------------


def _preview(client, **payload):
    resp = client.post("/api/recurring-todos/preview", json=payload)
    assert resp.status_code == 200, resp.text
    return resp.json()["occurrences"]


def test_preview_daily_starts_today_without_a_start_date(client, frozen_now):
    frozen_now(datetime(2026, 8, 22, 12, tzinfo=UTC))

    assert _preview(client, recurrence_type="daily") == [
        "2026-08-22",
        "2026-08-23",
        "2026-08-24",
    ]


def test_preview_weekly_floors_to_the_chosen_weekday(client, frozen_now):
    frozen_now(datetime(2026, 8, 22, 12, tzinfo=UTC))  # a Saturday

    assert _preview(client, recurrence_type="weekly", recurrence_day=0) == [
        "2026-08-24",
        "2026-08-31",
        "2026-09-07",
    ]


def test_preview_monthly_rolls_forward_past_this_months_day(client, frozen_now):
    # The rollforward, read back: created on the 22nd, "Monthly on the 1st"
    # previews September rather than a task due three weeks ago.
    frozen_now(datetime(2026, 8, 22, 12, tzinfo=UTC))

    assert _preview(client, recurrence_type="monthly", recurrence_day=1) == [
        "2026-09-01",
        "2026-10-01",
        "2026-11-01",
    ]


def test_preview_custom_rule_is_never_saved(client, db_session, frozen_now):
    frozen_now(datetime(2026, 8, 22, 12, tzinfo=UTC))

    occurrences = _preview(
        client,
        recurrence_type="custom",
        custom_rule={"freq": "monthly", "interval": 12, "mode": "day", "day": 1},
        start_date="2027-01-01",
    )

    assert occurrences == ["2027-01-01", "2028-01-01", "2029-01-01"]
    assert db_session.query(RecurringTodo).count() == 0


def test_preview_custom_monthly_weekday(client, frozen_now):
    frozen_now(datetime(2026, 8, 22, 12, tzinfo=UTC))

    assert _preview(
        client,
        recurrence_type="custom",
        custom_rule={
            "freq": "monthly",
            "interval": 6,
            "mode": "weekday",
            "ordinal": "first",
            "weekday": 6,
        },
        start_date="2027-01-01",
    ) == ["2027-01-03", "2027-07-04", "2028-01-02"]


def test_preview_custom_every_n_days_anchors_on_the_start_date(client, frozen_now):
    frozen_now(datetime(2026, 8, 22, 12, tzinfo=UTC))

    assert _preview(
        client,
        recurrence_type="custom",
        custom_rule={"freq": "daily", "interval": 90},
        start_date="2026-10-15",
    ) == ["2026-10-15", "2027-01-13", "2027-04-13"]


def test_preview_rejects_a_malformed_start_date(client):
    resp = client.post(
        "/api/recurring-todos/preview",
        json={"recurrence_type": "daily", "start_date": "soon"},
    )

    assert resp.status_code == 422
