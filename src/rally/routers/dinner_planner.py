"""Meal planner router for Rally."""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import case, nullslast
from sqlalchemy.orm import Session

from rally.database import get_db
from rally.models import DinnerPlan, Setting
from rally.schemas import (
    UNSET,
    DinnerPlanCreate,
    DinnerPlanResponse,
    DinnerPlanReviewUpdate,
    DinnerPlanUpdate,
)
from rally.utils.timezone import today_local

router = APIRouter(prefix="/api/dinner-plans", tags=["dinner-plans"])

# Fixed meal type sort order: Breakfast < Lunch < Dinner < Snacks
_MEAL_TYPE_ORDER = case(
    {"Breakfast": 0, "Lunch": 1, "Dinner": 2, "Snacks": 3},
    value=DinnerPlan.meal_type,
    else_=99,
)


@router.get("", response_model=list[DinnerPlanResponse])
def list_dinner_plans(db: Session = Depends(get_db)):
    """List all meal plans ordered by date then meal type."""
    plans = db.query(DinnerPlan).order_by(DinnerPlan.date.asc(), _MEAL_TYPE_ORDER).all()
    return plans


@router.post("", response_model=DinnerPlanResponse, status_code=201)
def create_dinner_plan(plan: DinnerPlanCreate, db: Session = Depends(get_db)):
    """Create a new meal plan. Multiple plans per date are allowed."""
    db_plan = DinnerPlan(
        date=plan.date,
        meal_type=plan.meal_type,
        plan=plan.plan,
        attendee_ids=plan.attendee_ids,
        cook_id=plan.cook_id,
    )
    db.add(db_plan)
    db.commit()
    db.refresh(db_plan)
    return db_plan


@router.get("/history", response_model=list[DinnerPlanResponse])
def list_meal_history(
    sort: str = Query("rating_desc", pattern="^(rating_desc|date_desc|date_asc)$"),
    min_rating: int | None = Query(None, ge=1, le=5),
    db: Session = Depends(get_db),
):
    """List past meal plans (before today in the user's timezone).

    Sort options:
    - rating_desc: highest rated first, null ratings last
    - date_desc: most recent first
    - date_asc: oldest first
    """
    settings = {r.key: r.value for r in db.query(Setting).all()}
    tz_name = settings.get("local_timezone", "UTC")
    today = today_local(tz_name).strftime("%Y-%m-%d")

    query = db.query(DinnerPlan).filter(DinnerPlan.date < today)

    if min_rating is not None:
        query = query.filter(DinnerPlan.rating >= min_rating)

    if sort == "rating_desc":
        query = query.order_by(nullslast(DinnerPlan.rating.desc()), DinnerPlan.date.desc())
    elif sort == "date_asc":
        query = query.order_by(DinnerPlan.date.asc(), _MEAL_TYPE_ORDER)
    else:  # date_desc
        query = query.order_by(DinnerPlan.date.desc(), _MEAL_TYPE_ORDER)

    return query.all()


@router.get("/date/{date}", response_model=list[DinnerPlanResponse])
def get_dinner_plans_by_date(date: str, db: Session = Depends(get_db)):
    """Get all meal plans for a specific date (YYYY-MM-DD)."""
    plans = db.query(DinnerPlan).filter(DinnerPlan.date == date).order_by(_MEAL_TYPE_ORDER).all()
    return plans


@router.get("/{plan_id}", response_model=DinnerPlanResponse)
def get_dinner_plan(plan_id: int, db: Session = Depends(get_db)):
    """Get a specific meal plan by ID."""
    plan = db.query(DinnerPlan).filter(DinnerPlan.id == plan_id).first()
    if not plan:
        raise HTTPException(status_code=404, detail="Meal plan not found")
    return plan


@router.put("/{plan_id}", response_model=DinnerPlanResponse)
def update_dinner_plan(
    plan_id: int,
    plan: DinnerPlanUpdate,
    db: Session = Depends(get_db),
):
    """Update a meal plan."""
    db_plan = db.query(DinnerPlan).filter(DinnerPlan.id == plan_id).first()
    if not db_plan:
        raise HTTPException(status_code=404, detail="Meal plan not found")

    if plan.date is not None:
        db_plan.date = plan.date
    if plan.meal_type is not None:
        db_plan.meal_type = plan.meal_type
    if plan.plan is not None:
        db_plan.plan = plan.plan
    if plan.attendee_ids is not UNSET:
        db_plan.attendee_ids = plan.attendee_ids
    if plan.cook_id is not UNSET:
        db_plan.cook_id = plan.cook_id

    db.commit()
    db.refresh(db_plan)
    return db_plan


@router.put("/{plan_id}/review", response_model=DinnerPlanResponse)
def review_meal(
    plan_id: int,
    review: DinnerPlanReviewUpdate,
    db: Session = Depends(get_db),
):
    """Submit or update a meal review (rating and/or text)."""
    db_plan = db.query(DinnerPlan).filter(DinnerPlan.id == plan_id).first()
    if not db_plan:
        raise HTTPException(status_code=404, detail="Meal plan not found")

    if review.rating is not None:
        if review.rating < 1 or review.rating > 5:
            raise HTTPException(status_code=422, detail="Rating must be between 1 and 5")
        db_plan.rating = review.rating
    elif review.rating is None and "rating" in review.model_fields_set:
        db_plan.rating = None

    if review.review is not None:
        db_plan.review = review.review
    elif review.review is None and "review" in review.model_fields_set:
        db_plan.review = None

    db.commit()
    db.refresh(db_plan)
    return db_plan


@router.delete("/{plan_id}", status_code=204)
def delete_dinner_plan(plan_id: int, db: Session = Depends(get_db)):
    """Delete a meal plan."""
    db_plan = db.query(DinnerPlan).filter(DinnerPlan.id == plan_id).first()
    if not db_plan:
        raise HTTPException(status_code=404, detail="Meal plan not found")

    db.delete(db_plan)
    db.commit()
    return None
