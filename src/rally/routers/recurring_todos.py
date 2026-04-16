"""Recurring todos router for Rally."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from rally.database import get_db
from rally.models import RecurringTodo, Todo
from rally.schemas import UNSET, RecurringTodoCreate, RecurringTodoResponse, RecurringTodoUpdate
from rally.utils.timezone import ensure_utc

router = APIRouter(prefix="/api/recurring-todos", tags=["recurring-todos"])


@router.get("", response_model=list[RecurringTodoResponse])
def list_recurring_todos(db: Session = Depends(get_db)):
    """List all recurring todo templates."""
    rts = db.query(RecurringTodo).order_by(RecurringTodo.created_at.desc()).all()

    last_completed_rows = (
        db.query(Todo.recurring_todo_id, func.max(Todo.updated_at).label("last_completed_at"))
        .filter(Todo.completed == True, Todo.recurring_todo_id.isnot(None))  # noqa: E712
        .group_by(Todo.recurring_todo_id)
        .all()
    )
    last_completed_map = {row.recurring_todo_id: row.last_completed_at for row in last_completed_rows}

    results = []
    for rt in rts:
        response = RecurringTodoResponse.model_validate(rt)
        last_dt = last_completed_map.get(rt.id)
        if last_dt:
            response.last_completed_date = ensure_utc(last_dt).date().isoformat()
        results.append(response)

    return results


@router.post("", response_model=RecurringTodoResponse, status_code=201)
def create_recurring_todo(rt: RecurringTodoCreate, db: Session = Depends(get_db)):
    """Create a new recurring todo template."""
    db_rt = RecurringTodo(
        title=rt.title,
        description=rt.description,
        recurrence_type=rt.recurrence_type,
        recurrence_day=rt.recurrence_day,
        assigned_to=rt.assigned_to,
        has_due_date=rt.has_due_date,
        remind_days_before=rt.remind_days_before,
        custom_rule=rt.custom_rule,
    )
    db.add(db_rt)
    db.commit()
    db.refresh(db_rt)
    return db_rt


@router.get("/{rt_id}", response_model=RecurringTodoResponse)
def get_recurring_todo(rt_id: int, db: Session = Depends(get_db)):
    """Get a specific recurring todo template."""
    rt = db.query(RecurringTodo).filter(RecurringTodo.id == rt_id).first()
    if not rt:
        raise HTTPException(status_code=404, detail="Recurring todo not found")
    return rt


@router.put("/{rt_id}", response_model=RecurringTodoResponse)
def update_recurring_todo(rt_id: int, rt: RecurringTodoUpdate, db: Session = Depends(get_db)):
    """Update a recurring todo template."""
    db_rt = db.query(RecurringTodo).filter(RecurringTodo.id == rt_id).first()
    if not db_rt:
        raise HTTPException(status_code=404, detail="Recurring todo not found")

    if rt.title is not None:
        db_rt.title = rt.title
    if rt.description is not None:
        db_rt.description = rt.description
    if rt.recurrence_type is not None:
        db_rt.recurrence_type = rt.recurrence_type
    if rt.recurrence_day is not None:
        db_rt.recurrence_day = rt.recurrence_day
    if rt.assigned_to is not UNSET:
        db_rt.assigned_to = rt.assigned_to
    if rt.has_due_date is not None:
        db_rt.has_due_date = rt.has_due_date
    if rt.remind_days_before is not UNSET:
        db_rt.remind_days_before = rt.remind_days_before
    if rt.active is not None:
        db_rt.active = rt.active
    if rt.custom_rule is not UNSET:
        db_rt.custom_rule = rt.custom_rule

    db.commit()
    db.refresh(db_rt)
    return db_rt


@router.delete("/{rt_id}", status_code=204)
def delete_recurring_todo(rt_id: int, db: Session = Depends(get_db)):
    """Delete a recurring todo template."""
    db_rt = db.query(RecurringTodo).filter(RecurringTodo.id == rt_id).first()
    if not db_rt:
        raise HTTPException(status_code=404, detail="Recurring todo not found")

    db.delete(db_rt)
    db.commit()
    return None
