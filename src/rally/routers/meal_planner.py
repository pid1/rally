"""Meal planner router for Rally."""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import case, nullslast, or_
from sqlalchemy.orm import Session

from rally.database import get_db
from rally.models import MealPlan
from rally.schemas import (
    MEAL_TYPES,
    UNSET,
    ArchivePage,
    MealPlanCreate,
    MealPlanResponse,
    MealPlanReviewUpdate,
    MealPlanUpdate,
)
from rally.utils.settings import today_local_str

router = APIRouter(prefix="/api/meal-planner", tags=["meal-planner"])

# Fixed meal type sort order: Breakfast < Lunch < Dinner < Snacks
_MEAL_TYPE_ORDER = case(
    {"Breakfast": 0, "Lunch": 1, "Dinner": 2, "Snacks": 3},
    value=MealPlan.meal_type,
    else_=99,
)


@router.get("", response_model=list[MealPlanResponse])
def list_meal_plans(db: Session = Depends(get_db)):
    """List all meal plans ordered by date then meal type."""
    plans = db.query(MealPlan).order_by(MealPlan.date.asc(), _MEAL_TYPE_ORDER).all()
    return plans


@router.post("", response_model=MealPlanResponse, status_code=201)
def create_meal_plan(plan: MealPlanCreate, db: Session = Depends(get_db)):
    """Create a new meal plan. Multiple plans per date are allowed."""
    db_plan = MealPlan(
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


@router.get("/previous", response_model=ArchivePage[MealPlanResponse])
def list_previous_meals(
    sort: str = Query("rating_desc", pattern="^(rating_desc|date_desc|date_asc)$"),
    min_rating: int | None = Query(None, ge=1, le=5),
    meal_type: list[str] | None = Query(None),
    search: str | None = Query(
        None, description="Case-insensitive keyword matched against the meal and its review."
    ),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """List past meal plans (before today in the user's timezone).

    Sort options:
    - rating_desc: highest rated first, null ratings last
    - date_desc: most recent first
    - date_asc: oldest first

    Every sort ends in ``id``: two dinners on one date, or two 5-star meals on
    one date, otherwise tie, and offset paging over a tie can show a meal twice
    or skip it.

    ``meal_type`` may be repeated to filter to any of the given types (e.g.
    ``?meal_type=Breakfast&meal_type=Lunch``); each value must be a known meal
    type.

    Searching and pagination are server-side because the client only ever holds
    the pages it has loaded. ``total`` counts matches across every page.
    """
    if meal_type:
        invalid = [m for m in meal_type if m not in MEAL_TYPES]
        if invalid:
            raise HTTPException(status_code=422, detail=f"Invalid meal_type value(s): {invalid}")

    today = today_local_str(db)

    query = db.query(MealPlan).filter(MealPlan.date < today)

    if min_rating is not None:
        query = query.filter(MealPlan.rating >= min_rating)

    if meal_type:
        query = query.filter(MealPlan.meal_type.in_(meal_type))

    if search and search.strip():
        term = f"%{search.strip()}%"
        query = query.filter(or_(MealPlan.plan.ilike(term), MealPlan.review.ilike(term)))

    total = query.count()

    if sort == "rating_desc":
        query = query.order_by(
            nullslast(MealPlan.rating.desc()), MealPlan.date.desc(), MealPlan.id.desc()
        )
    elif sort == "date_asc":
        query = query.order_by(MealPlan.date.asc(), _MEAL_TYPE_ORDER, MealPlan.id.asc())
    else:  # date_desc
        query = query.order_by(MealPlan.date.desc(), _MEAL_TYPE_ORDER, MealPlan.id.desc())

    # One extra row answers "is there another page" without a second count.
    rows = query.offset(offset).limit(limit + 1).all()
    return ArchivePage[MealPlanResponse](
        items=rows[:limit], has_more=len(rows) > limit, total=total
    )


@router.get("/date/{date}", response_model=list[MealPlanResponse])
def get_meal_plans_by_date(date: str, db: Session = Depends(get_db)):
    """Get all meal plans for a specific date (YYYY-MM-DD)."""
    plans = db.query(MealPlan).filter(MealPlan.date == date).order_by(_MEAL_TYPE_ORDER).all()
    return plans


@router.get("/{plan_id}", response_model=MealPlanResponse)
def get_meal_plan(plan_id: int, db: Session = Depends(get_db)):
    """Get a specific meal plan by ID."""
    plan = db.query(MealPlan).filter(MealPlan.id == plan_id).first()
    if not plan:
        raise HTTPException(status_code=404, detail="Meal plan not found")
    return plan


@router.put("/{plan_id}", response_model=MealPlanResponse)
def update_meal_plan(
    plan_id: int,
    plan: MealPlanUpdate,
    db: Session = Depends(get_db),
):
    """Update a meal plan."""
    db_plan = db.query(MealPlan).filter(MealPlan.id == plan_id).first()
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

    # A rating/review only makes sense for a past meal. If the date is moved onto
    # the Meal Planner (today or later, out of Previous Meals), discard any existing
    # rating and review. The client warns and confirms before sending such a
    # change; enforcing it here keeps the invariant regardless of the caller.
    if plan.date is not None and db_plan.date >= today_local_str(db):
        db_plan.rating = None
        db_plan.review = None

    db.commit()
    db.refresh(db_plan)
    return db_plan


@router.put("/{plan_id}/review", response_model=MealPlanResponse)
def review_meal(
    plan_id: int,
    review: MealPlanReviewUpdate,
    db: Session = Depends(get_db),
):
    """Submit or update a meal review (rating and/or text)."""
    db_plan = db.query(MealPlan).filter(MealPlan.id == plan_id).first()
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
def delete_meal_plan(plan_id: int, db: Session = Depends(get_db)):
    """Delete a meal plan."""
    db_plan = db.query(MealPlan).filter(MealPlan.id == plan_id).first()
    if not db_plan:
        raise HTTPException(status_code=404, detail="Meal plan not found")

    db.delete(db_plan)
    db.commit()
    return None
