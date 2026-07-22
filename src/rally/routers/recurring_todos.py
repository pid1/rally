"""Recurring todos router for Rally."""

from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from rally.database import get_db
from rally.models import RecurringTodo, Setting, Todo
from rally.schemas import UNSET, RecurringTodoCreate, RecurringTodoResponse, RecurringTodoUpdate
from rally.utils.timezone import ensure_utc, now_utc

router = APIRouter(prefix="/api/recurring-todos", tags=["recurring-todos"])


def format_local_completion(completed_at: datetime, local_tz: ZoneInfo) -> str:
    local_dt = completed_at.astimezone(local_tz)
    today = now_utc().astimezone(local_tz).date()
    if local_dt.date() == today:
        date_label = "Today"
    elif local_dt.date() == today.replace(day=today.day) - __import__("datetime").timedelta(days=1):
        date_label = "Yesterday"
    else:
        suffix = (
            "th"
            if 11 <= local_dt.day % 100 <= 13
            else {1: "st", 2: "nd", 3: "rd"}.get(local_dt.day % 10, "th")
        )
        date_label = f"{local_dt.strftime('%b')} {local_dt.day}{suffix}, {local_dt.year}"
    time_label = local_dt.strftime("%I:%M %p").lstrip("0")
    return f"{date_label} at {time_label}"


@router.get("", response_model=list[RecurringTodoResponse])
def list_recurring_todos(db: Session = Depends(get_db)):
    """List all recurring todo templates."""
    rts = db.query(RecurringTodo).order_by(RecurringTodo.created_at.desc()).all()

    completed_rows = (
        db.query(
            Todo.recurring_todo_id,
            func.max(func.coalesce(Todo.completed_at, Todo.updated_at)).label("last_completed_at"),
        )
        .filter(
            Todo.completed == True,  # noqa: E712
            Todo.recurring_todo_id.isnot(None),
        )
        .group_by(Todo.recurring_todo_id)
        .all()
    )

    tz_row = db.query(Setting).filter(Setting.key == "local_timezone").first()
    local_tz = ZoneInfo(tz_row.value if tz_row and tz_row.value else "UTC")

    last_completed_map: dict[int, datetime] = {}
    last_completed_date_map: dict[int, str] = {}
    for row in completed_rows:
        completed_at = row.last_completed_at
        if isinstance(completed_at, str):
            completed_at = datetime.fromisoformat(completed_at.replace("Z", "+00:00"))
        utc_dt = ensure_utc(completed_at)
        local_dt = utc_dt.astimezone(local_tz)
        last_completed_map[row.recurring_todo_id] = utc_dt
        last_completed_date_map[row.recurring_todo_id] = local_dt.date().isoformat()

    results = []
    for rt in rts:
        response = RecurringTodoResponse.model_validate(rt)
        last_completed_at = last_completed_map.get(rt.id)
        if last_completed_at:
            response.last_completed_at = last_completed_at
            response.last_completed_date = last_completed_date_map[rt.id]
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
