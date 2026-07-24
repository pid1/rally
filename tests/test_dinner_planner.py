"""Tests for the dinner/meal planner router: CRUD, reviews, meal history, and
the meal-type sort order, plus the DB-level rating check constraint.
"""

from datetime import UTC, datetime

import pytest
from sqlalchemy.exc import IntegrityError

from rally.models import DinnerPlan


def _create(client, date="2026-05-01", **fields):
    payload = {"date": date, "plan": "A meal"}
    payload.update(fields)
    resp = client.post("/api/dinner-plans", json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


# --- CRUD ----------------------------------------------------------------------


def test_create_defaults_meal_type_dinner(client):
    body = _create(client, plan="Tacos")
    assert body["meal_type"] == "Dinner"
    assert body["plan"] == "Tacos"
    assert body["date"] == "2026-05-01"


def test_create_with_attendees_and_cook(client, make_member):
    dad = make_member("Dad")
    mom = make_member("Mom")

    body = _create(client, meal_type="Lunch", attendee_ids=[dad.id, mom.id], cook_id=dad.id)

    assert body["meal_type"] == "Lunch"
    assert body["attendee_ids"] == [dad.id, mom.id]
    assert body["cook_id"] == dad.id


def test_list_ordered_by_date_then_meal_type(client):
    _create(client, date="2026-05-01", meal_type="Dinner")
    _create(client, date="2026-05-01", meal_type="Breakfast")
    _create(client, date="2026-04-30", meal_type="Lunch")

    got = [(p["date"], p["meal_type"]) for p in client.get("/api/dinner-plans").json()]

    assert got == [
        ("2026-04-30", "Lunch"),
        ("2026-05-01", "Breakfast"),
        ("2026-05-01", "Dinner"),
    ]


def test_get_by_date_ordered_by_meal_type(client):
    _create(client, date="2026-05-01", meal_type="Dinner")
    _create(client, date="2026-05-01", meal_type="Breakfast")
    _create(client, date="2026-05-02", meal_type="Dinner")

    types = [p["meal_type"] for p in client.get("/api/dinner-plans/date/2026-05-01").json()]

    assert types == ["Breakfast", "Dinner"]


def test_get_found_and_404(client):
    plan = _create(client)
    assert client.get(f"/api/dinner-plans/{plan['id']}").json()["id"] == plan["id"]
    assert client.get("/api/dinner-plans/9999").status_code == 404


def test_update_unset_semantics(client, make_member):
    dad = make_member("Dad")
    plan = _create(client, cook_id=dad.id, attendee_ids=[dad.id])

    # Omitted -> untouched.
    body = client.put(f"/api/dinner-plans/{plan['id']}", json={"plan": "New"}).json()
    assert body["plan"] == "New"
    assert body["cook_id"] == dad.id
    assert body["attendee_ids"] == [dad.id]

    # Explicit null -> cleared.
    body = client.put(
        f"/api/dinner-plans/{plan['id']}", json={"cook_id": None, "attendee_ids": None}
    ).json()
    assert body["cook_id"] is None
    assert body["attendee_ids"] is None


def test_update_date_and_meal_type(client):
    plan = _create(client, date="2026-05-01", meal_type="Dinner")

    body = client.put(
        f"/api/dinner-plans/{plan['id']}", json={"date": "2026-06-01", "meal_type": "Lunch"}
    ).json()

    assert body["date"] == "2026-06-01"
    assert body["meal_type"] == "Lunch"


def test_update_404(client):
    assert client.put("/api/dinner-plans/9999", json={"plan": "x"}).status_code == 404


def test_delete_and_404(client, db_session):
    plan = _create(client)

    assert client.delete(f"/api/dinner-plans/{plan['id']}").status_code == 204
    assert db_session.get(DinnerPlan, plan["id"]) is None

    assert client.delete("/api/dinner-plans/9999").status_code == 404


# --- Reviews -------------------------------------------------------------------


def test_review_sets_rating_and_text(client):
    plan = _create(client)

    body = client.put(
        f"/api/dinner-plans/{plan['id']}/review", json={"rating": 5, "review": "Great"}
    ).json()

    assert body["rating"] == 5
    assert body["review"] == "Great"


@pytest.mark.parametrize("bad", [0, 6, -1, 99])
def test_review_rating_out_of_range_422(client, bad):
    plan = _create(client)
    resp = client.put(f"/api/dinner-plans/{plan['id']}/review", json={"rating": bad})
    assert resp.status_code == 422


def test_review_clear_rating_via_null(client):
    plan = _create(client)
    client.put(f"/api/dinner-plans/{plan['id']}/review", json={"rating": 4})

    body = client.put(f"/api/dinner-plans/{plan['id']}/review", json={"rating": None}).json()

    assert body["rating"] is None


def test_review_omitting_rating_leaves_it(client):
    plan = _create(client)
    client.put(f"/api/dinner-plans/{plan['id']}/review", json={"rating": 4})

    # Omit rating, set review only -> rating stays.
    body = client.put(f"/api/dinner-plans/{plan['id']}/review", json={"review": "ok"}).json()

    assert body["rating"] == 4
    assert body["review"] == "ok"


def test_review_clear_review_via_null(client):
    plan = _create(client)
    client.put(f"/api/dinner-plans/{plan['id']}/review", json={"review": "tasty"})

    body = client.put(f"/api/dinner-plans/{plan['id']}/review", json={"review": None}).json()

    assert body["review"] is None


def test_review_404(client):
    assert client.put("/api/dinner-plans/9999/review", json={"rating": 5}).status_code == 404


# --- Meal history --------------------------------------------------------------

TODAY = datetime(2026, 5, 10, 12, tzinfo=UTC)


def test_history_filters_past_only_and_rating_desc(client, make_dinner_plan, frozen_now):
    frozen_now(TODAY)
    make_dinner_plan("2026-05-01", plan="A", rating=5)
    make_dinner_plan("2026-05-02", plan="B", rating=3)
    make_dinner_plan("2026-05-03", plan="C", rating=None)
    make_dinner_plan("2026-05-10", plan="Today", rating=5)  # not before today -> excluded
    make_dinner_plan("2026-05-15", plan="Future", rating=5)  # excluded

    hist = client.get("/api/dinner-plans/history").json()  # default rating_desc

    assert [(p["date"], p["rating"]) for p in hist] == [
        ("2026-05-01", 5),
        ("2026-05-02", 3),
        ("2026-05-03", None),  # nulls last
    ]


def test_history_min_rating_filter(client, make_dinner_plan, frozen_now):
    frozen_now(TODAY)
    make_dinner_plan("2026-05-01", plan="A", rating=5)
    make_dinner_plan("2026-05-02", plan="B", rating=2)

    hist = client.get("/api/dinner-plans/history", params={"min_rating": 3}).json()

    assert [p["date"] for p in hist] == ["2026-05-01"]


def test_history_sort_date_asc_and_desc(client, make_dinner_plan, frozen_now):
    frozen_now(TODAY)
    make_dinner_plan("2026-05-01", plan="A", rating=1)
    make_dinner_plan("2026-05-05", plan="B", rating=1)

    asc = client.get("/api/dinner-plans/history", params={"sort": "date_asc"}).json()
    desc = client.get("/api/dinner-plans/history", params={"sort": "date_desc"}).json()

    assert [p["date"] for p in asc] == ["2026-05-01", "2026-05-05"]
    assert [p["date"] for p in desc] == ["2026-05-05", "2026-05-01"]


def test_history_meal_type_filter(client, make_dinner_plan, frozen_now):
    frozen_now(TODAY)
    make_dinner_plan("2026-05-01", plan="Eggs", meal_type="Breakfast", rating=4)
    make_dinner_plan("2026-05-02", plan="Steak", meal_type="Dinner", rating=4)

    hist = client.get("/api/dinner-plans/history", params={"meal_type": "Breakfast"}).json()

    assert [p["plan"] for p in hist] == ["Eggs"]


def test_history_meal_type_filter_multiple(client, make_dinner_plan, frozen_now):
    frozen_now(TODAY)
    make_dinner_plan("2026-05-01", plan="Eggs", meal_type="Breakfast", rating=4)
    make_dinner_plan("2026-05-02", plan="Sandwich", meal_type="Lunch", rating=4)
    make_dinner_plan("2026-05-03", plan="Steak", meal_type="Dinner", rating=4)

    hist = client.get(
        "/api/dinner-plans/history", params={"meal_type": ["Breakfast", "Lunch"]}
    ).json()

    assert sorted(p["plan"] for p in hist) == ["Eggs", "Sandwich"]


def test_history_meal_type_composes_with_min_rating(client, make_dinner_plan, frozen_now):
    frozen_now(TODAY)
    make_dinner_plan("2026-05-01", plan="GoodDinner", meal_type="Dinner", rating=5)
    make_dinner_plan("2026-05-02", plan="BadDinner", meal_type="Dinner", rating=2)
    make_dinner_plan("2026-05-03", plan="GoodLunch", meal_type="Lunch", rating=5)

    hist = client.get(
        "/api/dinner-plans/history",
        params={"meal_type": "Dinner", "min_rating": 3},
    ).json()

    assert [p["plan"] for p in hist] == ["GoodDinner"]


def test_history_invalid_meal_type_returns_422(client, make_dinner_plan, frozen_now):
    frozen_now(TODAY)
    make_dinner_plan("2026-05-01", plan="Eggs", meal_type="Breakfast", rating=4)

    resp = client.get("/api/dinner-plans/history", params={"meal_type": "Brunch"})

    assert resp.status_code == 422


def test_history_respects_local_timezone(client, make_dinner_plan, frozen_now, local_timezone):
    # At 02:00Z the local date in Kolkata (+05:30) is already the next day, so a
    # plan dated that local day counts as "today" and is excluded from history.
    frozen_now(datetime(2026, 5, 10, 2, 0, tzinfo=UTC))
    local_timezone("Asia/Kolkata")  # local date is 2026-05-10
    make_dinner_plan("2026-05-09", plan="Yesterday", rating=4)
    make_dinner_plan("2026-05-10", plan="LocalToday", rating=4)

    dates = [p["date"] for p in client.get("/api/dinner-plans/history").json()]

    assert dates == ["2026-05-09"]


# --- DB-level rating constraint ------------------------------------------------


def test_rating_check_constraint_rejects_out_of_range(db_session):
    db_session.add(DinnerPlan(date="2026-05-01", plan="X", rating=6))
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()
