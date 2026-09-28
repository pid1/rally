"""Tests for the meal planner router: CRUD, reviews, previous meals, and
the meal-type sort order, plus the DB-level rating check constraint.
"""

from datetime import UTC, datetime

import pytest
from sqlalchemy.exc import IntegrityError

from rally.models import MealPlan


def _create(client, date="2026-05-01", **fields):
    payload = {"date": date, "plan": "A meal"}
    payload.update(fields)
    resp = client.post("/api/meal-planner", json=payload)
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

    got = [(p["date"], p["meal_type"]) for p in client.get("/api/meal-planner").json()]

    assert got == [
        ("2026-04-30", "Lunch"),
        ("2026-05-01", "Breakfast"),
        ("2026-05-01", "Dinner"),
    ]


def test_get_by_date_ordered_by_meal_type(client):
    _create(client, date="2026-05-01", meal_type="Dinner")
    _create(client, date="2026-05-01", meal_type="Breakfast")
    _create(client, date="2026-05-02", meal_type="Dinner")

    types = [p["meal_type"] for p in client.get("/api/meal-planner/date/2026-05-01").json()]

    assert types == ["Breakfast", "Dinner"]


def test_get_found_and_404(client):
    plan = _create(client)
    assert client.get(f"/api/meal-planner/{plan['id']}").json()["id"] == plan["id"]
    assert client.get("/api/meal-planner/9999").status_code == 404


def test_update_unset_semantics(client, make_member):
    dad = make_member("Dad")
    plan = _create(client, cook_id=dad.id, attendee_ids=[dad.id])

    # Omitted -> untouched.
    body = client.put(f"/api/meal-planner/{plan['id']}", json={"plan": "New"}).json()
    assert body["plan"] == "New"
    assert body["cook_id"] == dad.id
    assert body["attendee_ids"] == [dad.id]

    # Explicit null -> cleared.
    body = client.put(
        f"/api/meal-planner/{plan['id']}", json={"cook_id": None, "attendee_ids": None}
    ).json()
    assert body["cook_id"] is None
    assert body["attendee_ids"] is None


def test_update_date_and_meal_type(client):
    plan = _create(client, date="2026-05-01", meal_type="Dinner")

    body = client.put(
        f"/api/meal-planner/{plan['id']}", json={"date": "2026-06-01", "meal_type": "Lunch"}
    ).json()

    assert body["date"] == "2026-06-01"
    assert body["meal_type"] == "Lunch"


def test_update_404(client):
    assert client.put("/api/meal-planner/9999", json={"plan": "x"}).status_code == 404


def test_delete_and_404(client, db_session):
    plan = _create(client)

    assert client.delete(f"/api/meal-planner/{plan['id']}").status_code == 204
    assert db_session.get(MealPlan, plan["id"]) is None

    assert client.delete("/api/meal-planner/9999").status_code == 404


# --- Reviews -------------------------------------------------------------------


def test_review_sets_rating_and_text(client):
    plan = _create(client)

    body = client.put(
        f"/api/meal-planner/{plan['id']}/review", json={"rating": 5, "review": "Great"}
    ).json()

    assert body["rating"] == 5
    assert body["review"] == "Great"


@pytest.mark.parametrize("bad", [0, 6, -1, 99])
def test_review_rating_out_of_range_422(client, bad):
    plan = _create(client)
    resp = client.put(f"/api/meal-planner/{plan['id']}/review", json={"rating": bad})
    assert resp.status_code == 422


def test_review_clear_rating_via_null(client):
    plan = _create(client)
    client.put(f"/api/meal-planner/{plan['id']}/review", json={"rating": 4})

    body = client.put(f"/api/meal-planner/{plan['id']}/review", json={"rating": None}).json()

    assert body["rating"] is None


def test_review_omitting_rating_leaves_it(client):
    plan = _create(client)
    client.put(f"/api/meal-planner/{plan['id']}/review", json={"rating": 4})

    # Omit rating, set review only -> rating stays.
    body = client.put(f"/api/meal-planner/{plan['id']}/review", json={"review": "ok"}).json()

    assert body["rating"] == 4
    assert body["review"] == "ok"


def test_review_clear_review_via_null(client):
    plan = _create(client)
    client.put(f"/api/meal-planner/{plan['id']}/review", json={"review": "tasty"})

    body = client.put(f"/api/meal-planner/{plan['id']}/review", json={"review": None}).json()

    assert body["review"] is None


def test_review_404(client):
    assert client.put("/api/meal-planner/9999/review", json={"rating": 5}).status_code == 404


# --- Previous meals --------------------------------------------------------------

TODAY = datetime(2026, 5, 10, 12, tzinfo=UTC)


def test_previous_filters_past_only_and_rating_desc(client, make_meal_plan, frozen_now):
    frozen_now(TODAY)
    make_meal_plan("2026-05-01", plan="A", rating=5)
    make_meal_plan("2026-05-02", plan="B", rating=3)
    make_meal_plan("2026-05-03", plan="C", rating=None)
    make_meal_plan("2026-05-10", plan="Today", rating=5)  # not before today -> excluded
    make_meal_plan("2026-05-15", plan="Future", rating=5)  # excluded

    hist = client.get("/api/meal-planner/previous").json()["items"]  # default rating_desc

    assert [(p["date"], p["rating"]) for p in hist] == [
        ("2026-05-01", 5),
        ("2026-05-02", 3),
        ("2026-05-03", None),  # nulls last
    ]


def test_previous_min_rating_filter(client, make_meal_plan, frozen_now):
    frozen_now(TODAY)
    make_meal_plan("2026-05-01", plan="A", rating=5)
    make_meal_plan("2026-05-02", plan="B", rating=2)

    hist = client.get("/api/meal-planner/previous", params={"min_rating": 3}).json()["items"]

    assert [p["date"] for p in hist] == ["2026-05-01"]


def test_previous_sort_date_asc_and_desc(client, make_meal_plan, frozen_now):
    frozen_now(TODAY)
    make_meal_plan("2026-05-01", plan="A", rating=1)
    make_meal_plan("2026-05-05", plan="B", rating=1)

    asc = client.get("/api/meal-planner/previous", params={"sort": "date_asc"}).json()["items"]
    desc = client.get("/api/meal-planner/previous", params={"sort": "date_desc"}).json()["items"]

    assert [p["date"] for p in asc] == ["2026-05-01", "2026-05-05"]
    assert [p["date"] for p in desc] == ["2026-05-05", "2026-05-01"]


def test_previous_meal_type_filter(client, make_meal_plan, frozen_now):
    frozen_now(TODAY)
    make_meal_plan("2026-05-01", plan="Eggs", meal_type="Breakfast", rating=4)
    make_meal_plan("2026-05-02", plan="Steak", meal_type="Dinner", rating=4)

    hist = client.get("/api/meal-planner/previous", params={"meal_type": "Breakfast"}).json()[
        "items"
    ]

    assert [p["plan"] for p in hist] == ["Eggs"]


def test_previous_meal_type_filter_multiple(client, make_meal_plan, frozen_now):
    frozen_now(TODAY)
    make_meal_plan("2026-05-01", plan="Eggs", meal_type="Breakfast", rating=4)
    make_meal_plan("2026-05-02", plan="Sandwich", meal_type="Lunch", rating=4)
    make_meal_plan("2026-05-03", plan="Steak", meal_type="Dinner", rating=4)

    hist = client.get(
        "/api/meal-planner/previous", params={"meal_type": ["Breakfast", "Lunch"]}
    ).json()["items"]

    assert sorted(p["plan"] for p in hist) == ["Eggs", "Sandwich"]


def test_previous_meal_type_composes_with_min_rating(client, make_meal_plan, frozen_now):
    frozen_now(TODAY)
    make_meal_plan("2026-05-01", plan="GoodDinner", meal_type="Dinner", rating=5)
    make_meal_plan("2026-05-02", plan="BadDinner", meal_type="Dinner", rating=2)
    make_meal_plan("2026-05-03", plan="GoodLunch", meal_type="Lunch", rating=5)

    hist = client.get(
        "/api/meal-planner/previous",
        params={"meal_type": "Dinner", "min_rating": 3},
    ).json()["items"]

    assert [p["plan"] for p in hist] == ["GoodDinner"]


def test_previous_invalid_meal_type_returns_422(client, make_meal_plan, frozen_now):
    frozen_now(TODAY)
    make_meal_plan("2026-05-01", plan="Eggs", meal_type="Breakfast", rating=4)

    resp = client.get("/api/meal-planner/previous", params={"meal_type": "Brunch"})

    assert resp.status_code == 422


def test_previous_respects_local_timezone(client, make_meal_plan, frozen_now, local_timezone):
    # At 02:00Z the local date in Kolkata (+05:30) is already the next day, so a
    # plan dated that local day counts as "today" and is excluded from previous meals.
    frozen_now(datetime(2026, 5, 10, 2, 0, tzinfo=UTC))
    local_timezone("Asia/Kolkata")  # local date is 2026-05-10
    make_meal_plan("2026-05-09", plan="Yesterday", rating=4)
    make_meal_plan("2026-05-10", plan="LocalToday", rating=4)

    dates = [p["date"] for p in client.get("/api/meal-planner/previous").json()["items"]]

    assert dates == ["2026-05-09"]


def test_previous_search_matches_meal_text(client, make_meal_plan, frozen_now):
    frozen_now(TODAY)
    make_meal_plan("2026-05-01", plan="Grilled Salmon", rating=4)
    make_meal_plan("2026-05-02", plan="Tacos", rating=4)

    page = client.get("/api/meal-planner/previous", params={"search": "salmon"}).json()

    assert [p["plan"] for p in page["items"]] == ["Grilled Salmon"]
    assert page["total"] == 1


def test_previous_search_matches_review_text(client, make_meal_plan, frozen_now):
    frozen_now(TODAY)
    make_meal_plan("2026-05-01", plan="Tacos", rating=5, review="Better than the salmon")
    make_meal_plan("2026-05-02", plan="Pizza", rating=3, review="Fine")

    page = client.get("/api/meal-planner/previous", params={"search": "SALMON"}).json()

    assert [p["plan"] for p in page["items"]] == ["Tacos"]


def test_previous_search_with_no_match_is_empty(client, make_meal_plan, frozen_now):
    frozen_now(TODAY)
    make_meal_plan("2026-05-01", plan="Tacos", rating=5)

    page = client.get("/api/meal-planner/previous", params={"search": "sushi"}).json()

    assert page == {"items": [], "has_more": False, "total": 0}


def test_previous_search_excludes_today_and_later(client, make_meal_plan, frozen_now):
    frozen_now(TODAY)
    make_meal_plan("2026-05-01", plan="Past chili", rating=4)
    make_meal_plan("2026-05-10", plan="Today chili")

    page = client.get("/api/meal-planner/previous", params={"search": "chili"}).json()

    assert [p["plan"] for p in page["items"]] == ["Past chili"]


def test_previous_pages_with_has_more_and_total(client, make_meal_plan, frozen_now):
    frozen_now(TODAY)
    for day in range(1, 6):
        make_meal_plan(f"2026-05-0{day}", plan=f"Meal {day}", rating=4)

    first = client.get("/api/meal-planner/previous", params={"sort": "date_asc", "limit": 2}).json()
    last = client.get(
        "/api/meal-planner/previous", params={"sort": "date_asc", "limit": 2, "offset": 4}
    ).json()

    assert [p["plan"] for p in first["items"]] == ["Meal 1", "Meal 2"]
    assert first["has_more"] is True
    assert first["total"] == 5
    assert [p["plan"] for p in last["items"]] == ["Meal 5"]
    assert last["has_more"] is False
    assert last["total"] == 5


@pytest.mark.parametrize("sort", ["rating_desc", "date_desc", "date_asc"])
def test_previous_paging_across_ties_returns_each_meal_once(
    client, make_meal_plan, frozen_now, sort
):
    """Five 5-star dinners on one date tie on every sort key but id; paging
    one at a time must still return each exactly once."""
    frozen_now(TODAY)
    ids = {
        make_meal_plan("2026-05-01", plan=f"Dinner {n}", meal_type="Dinner", rating=5).id
        for n in range(5)
    }

    seen = []
    for offset in range(5):
        page = client.get(
            "/api/meal-planner/previous", params={"sort": sort, "limit": 1, "offset": offset}
        ).json()
        seen.extend(p["id"] for p in page["items"])

    assert sorted(seen) == sorted(ids)


def test_previous_rejects_a_limit_over_200(client):
    assert client.get("/api/meal-planner/previous", params={"limit": 201}).status_code == 422


def test_history_endpoint_is_gone(client):
    assert client.get("/api/meal-planner/history").status_code in (404, 422)
    assert client.get("/api/dinner-plans/history").status_code == 404


# --- Moving a meal onto the planner discards its rating/review -----------------
#
# A rating/review only makes sense for a past meal (Previous Meals). Editing a
# meal's date to today or later moves it onto the Meal Planner, which clears any
# rating and review. The frozen "today" here is TODAY (2026-05-10, UTC).


def test_update_date_to_today_discards_rating_and_review(client, make_meal_plan, frozen_now):
    frozen_now(TODAY)
    plan = make_meal_plan("2026-05-01", plan="Tacos", rating=5, review="Great")

    body = client.put(f"/api/meal-planner/{plan.id}", json={"date": "2026-05-10"}).json()

    assert body["date"] == "2026-05-10"
    assert body["rating"] is None
    assert body["review"] is None


def test_update_date_to_future_discards_rating_and_review(client, make_meal_plan, frozen_now):
    frozen_now(TODAY)
    plan = make_meal_plan("2026-05-01", plan="Tacos", rating=4, review="Yum")

    body = client.put(f"/api/meal-planner/{plan.id}", json={"date": "2026-06-01"}).json()

    assert body["rating"] is None
    assert body["review"] is None


def test_update_date_still_past_keeps_rating_and_review(client, make_meal_plan, frozen_now):
    frozen_now(TODAY)
    plan = make_meal_plan("2026-05-01", plan="Tacos", rating=5, review="Great")

    body = client.put(f"/api/meal-planner/{plan.id}", json={"date": "2026-05-05"}).json()

    assert body["date"] == "2026-05-05"
    assert body["rating"] == 5
    assert body["review"] == "Great"


def test_update_without_date_change_keeps_rating_and_review(client, make_meal_plan, frozen_now):
    frozen_now(TODAY)
    plan = make_meal_plan("2026-05-01", plan="Tacos", rating=5, review="Great")

    # No date field in the payload -> rating/review untouched even though the
    # meal's date remains in the past.
    body = client.put(f"/api/meal-planner/{plan.id}", json={"plan": "Tacos al pastor"}).json()

    assert body["plan"] == "Tacos al pastor"
    assert body["rating"] == 5
    assert body["review"] == "Great"


def test_update_respects_local_timezone_for_planner_boundary(
    client, make_meal_plan, frozen_now, local_timezone
):
    # At 02:00Z the local date in Kolkata (+05:30) is already 2026-05-10, so
    # editing a meal to 2026-05-10 moves it onto the planner and clears its rating.
    frozen_now(datetime(2026, 5, 10, 2, 0, tzinfo=UTC))
    local_timezone("Asia/Kolkata")
    plan = make_meal_plan("2026-05-08", plan="Tacos", rating=5, review="Great")

    body = client.put(f"/api/meal-planner/{plan.id}", json={"date": "2026-05-10"}).json()

    assert body["rating"] is None
    assert body["review"] is None


# --- DB-level rating constraint ------------------------------------------------


def test_rating_check_constraint_rejects_out_of_range(db_session):
    db_session.add(MealPlan(date="2026-05-01", plan="X", rating=6))
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()
