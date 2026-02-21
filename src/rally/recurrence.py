"""Recurring todo processing for Rally.

Checks active recurring todo templates and generates todo instances
when the recurrence is due and no open instance exists.
"""

import calendar as cal_module
from datetime import UTC, date, datetime, timedelta

from sqlalchemy.orm import Session

from rally.models import RecurringTodo, Todo
from rally.utils.timezone import today_utc


def get_last_recurrence_date(rt: RecurringTodo, today: date) -> date:
    """Get the most recent date this recurrence should have fired."""
    if rt.recurrence_type == "daily":
        return today
    elif rt.recurrence_type == "weekly":
        day = rt.recurrence_day or 0
        days_since = (today.weekday() - day) % 7
        return today - timedelta(days=days_since)
    elif rt.recurrence_type == "monthly":
        day = rt.recurrence_day or 1
        if today.day >= day:
            clamped = min(day, cal_module.monthrange(today.year, today.month)[1])
            return today.replace(day=clamped)
        else:
            first_of_month = today.replace(day=1)
            last_month_end = first_of_month - timedelta(days=1)
            clamped = min(day, cal_module.monthrange(last_month_end.year, last_month_end.month)[1])
            return last_month_end.replace(day=clamped)
    return today


def process_recurring_todos(db: Session) -> int:
    """Check all active recurring todos and create instances where due.

    Returns the number of new todos created.
    """
    today = today_utc()
    created_count = 0

    recurring = db.query(RecurringTodo).filter(RecurringTodo.active == True).all()  # noqa: E712

    for rt in recurring:
        # Skip if there's an open (incomplete) todo for this template
        open_todo = db.query(Todo).filter(
            Todo.recurring_todo_id == rt.id,
            Todo.completed == False,  # noqa: E712
        ).first()
        if open_todo:
            continue

        # Get the most recent recurrence date
        last_recurrence = get_last_recurrence_date(rt, today)

        # Check if a todo was already created for this recurrence period
        cutoff = datetime(last_recurrence.year, last_recurrence.month, last_recurrence.day, tzinfo=UTC)
        existing = db.query(Todo).filter(
            Todo.recurring_todo_id == rt.id,
            Todo.created_at >= cutoff,
        ).first()
        if existing:
            continue

        # Create new todo instance
        due_date = str(last_recurrence) if rt.has_due_date else None
        new_todo = Todo(
            title=rt.title,
            description=rt.description,
            assigned_to=rt.assigned_to,
            due_date=due_date,
            recurring_todo_id=rt.id,
            completed=False,
        )
        db.add(new_todo)
        created_count += 1

    if created_count > 0:
        db.commit()

    return created_count
