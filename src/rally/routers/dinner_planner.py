"""Meal planner router for Rally."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import case
from sqlalchemy.orm import Session

from rally.database import get_db
from rally.models import DinnerPlan
from rally.schemas import UNSET, DinnerPlanCreate, DinnerPlanResponse, DinnerPlanUpdate

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


@router.get("/date/{date}", response_model=list[DinnerPlanResponse])
def get_dinner_plans_by_date(date: str, db: Session = Depends(get_db)):
    """Get all meal plans for a specific date (YYYY-MM-DD)."""
    plans = (
        db.query(DinnerPlan)
        .filter(DinnerPlan.date == date)
        .order_by(_MEAL_TYPE_ORDER)
        .all()
    )
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


@router.delete("/{plan_id}", status_code=204)
def delete_dinner_plan(plan_id: int, db: Session = Depends(get_db)):
    """Delete a meal plan."""
    db_plan = db.query(DinnerPlan).filter(DinnerPlan.id == plan_id).first()
    if not db_plan:
        raise HTTPException(status_code=404, detail="Meal plan not found")

    db.delete(db_plan)
    db.commit()
    return None
