"""Dinner planner router for Rally."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from rally.database import get_db
from rally.models import DinnerPlan
from rally.schemas import DinnerPlanCreate, DinnerPlanResponse, DinnerPlanUpdate

router = APIRouter(prefix="/api/dinner-plans", tags=["dinner-plans"])


@router.get("", response_model=list[DinnerPlanResponse])
def list_dinner_plans(db: Session = Depends(get_db)):
    """List all dinner plans."""
    plans = db.query(DinnerPlan).order_by(DinnerPlan.date.asc()).all()
    return plans


@router.post("", response_model=DinnerPlanResponse, status_code=201)
def create_dinner_plan(plan: DinnerPlanCreate, db: Session = Depends(get_db)):
    """Create a new dinner plan or update if date already exists."""
    # Check if plan already exists for this date
    existing = db.query(DinnerPlan).filter(DinnerPlan.date == plan.date).first()

    if existing:
        # Update existing plan
        existing.plan = plan.plan
        db.commit()
        db.refresh(existing)
        return existing

    # Create new plan
    db_plan = DinnerPlan(
        date=plan.date,
        plan=plan.plan,
    )
    db.add(db_plan)
    db.commit()
    db.refresh(db_plan)
    return db_plan


@router.get("/date/{date}", response_model=DinnerPlanResponse)
def get_dinner_plan_by_date(date: str, db: Session = Depends(get_db)):
    """Get dinner plan for a specific date (YYYY-MM-DD)."""
    plan = db.query(DinnerPlan).filter(DinnerPlan.date == date).first()
    if not plan:
        raise HTTPException(status_code=404, detail="No dinner plan for this date")
    return plan


@router.get("/{plan_id}", response_model=DinnerPlanResponse)
def get_dinner_plan(plan_id: int, db: Session = Depends(get_db)):
    """Get a specific dinner plan by ID."""
    plan = db.query(DinnerPlan).filter(DinnerPlan.id == plan_id).first()
    if not plan:
        raise HTTPException(status_code=404, detail="Dinner plan not found")
    return plan


@router.put("/{plan_id}", response_model=DinnerPlanResponse)
def update_dinner_plan(
    plan_id: int,
    plan: DinnerPlanUpdate,
    db: Session = Depends(get_db),
):
    """Update a dinner plan."""
    db_plan = db.query(DinnerPlan).filter(DinnerPlan.id == plan_id).first()
    if not db_plan:
        raise HTTPException(status_code=404, detail="Dinner plan not found")

    # Update only provided fields
    if plan.date is not None:
        db_plan.date = plan.date
    if plan.plan is not None:
        db_plan.plan = plan.plan

    db.commit()
    db.refresh(db_plan)
    return db_plan


@router.delete("/{plan_id}", status_code=204)
def delete_dinner_plan(plan_id: int, db: Session = Depends(get_db)):
    """Delete a dinner plan."""
    db_plan = db.query(DinnerPlan).filter(DinnerPlan.id == plan_id).first()
    if not db_plan:
        raise HTTPException(status_code=404, detail="Dinner plan not found")

    db.delete(db_plan)
    db.commit()
    return None
