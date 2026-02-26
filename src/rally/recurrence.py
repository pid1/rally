"""Recurring todo processing for Rally.

Checks active recurring todo templates and generates todo instances
when the recurrence is due and no open instance exists.
"""

import calendar as cal_module
from datetime import date, timedelta

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


def get_next_recurrence_date(rt: RecurringTodo, after_date: date) -> date:
    """Get the next recurrence date strictly after the given date.

    Used to calculate when the next instance should be created after
    completing or deleting a recurring todo instance.
    """
    if rt.recurrence_type == "daily":
        return after_date + timedelta(days=1)
    elif rt.recurrence_type == "weekly":
        day = rt.recurrence_day or 0
        days_until = (day - after_date.weekday()) % 7
        if days_until == 0:
            days_until = 7  # Strictly after: advance to next week
        return after_date + timedelta(days=days_until)
    elif rt.recurrence_type == "monthly":
        day = rt.recurrence_day or 1
        # Check if this month's recurrence date is still after after_date
        clamped_this = min(day, cal_module.monthrange(after_date.year, after_date.month)[1])
        this_month_date = after_date.replace(day=clamped_this)
        if this_month_date > after_date:
            return this_month_date
        # Otherwise, next month
        if after_date.month == 12:
            next_year, next_month = after_date.year + 1, 1
        else:
            next_year, next_month = after_date.year, after_date.month + 1
        clamped = min(day, cal_module.monthrange(next_year, next_month)[1])
        return date(next_year, next_month, clamped)
    return after_date + timedelta(days=1)


def get_first_recurrence_date(rt: RecurringTodo, today: date) -> date:
    """Get the first recurrence date for a newly created template.

    Unlike get_last_recurrence_date, this always uses the current period
    (current month/week) so the first instance isn't backdated to a
    previous period.
    """
    if rt.recurrence_type == "daily":
        return today
    elif rt.recurrence_type == "weekly":
        day = rt.recurrence_day or 0
        days_until = (day - today.weekday()) % 7
        return today + timedelta(days=days_until)
    elif rt.recurrence_type == "monthly":
        day = rt.recurrence_day or 1
        clamped = min(day, cal_module.monthrange(today.year, today.month)[1])
        return today.replace(day=clamped)
    return today


def _resolve_reference_date(rt: RecurringTodo, db: Session) -> date | None:
    """Determine the reference date for calculating the next instance.

    Returns the recurrence date of the most recently generated instance,
    or None if no instances have ever been created.
    """
    if rt.last_generated_date:
        return date.fromisoformat(rt.last_generated_date)

    # Backfill: find the most recent instance by due_date (or created_at)
    latest = (
        db.query(Todo)
        .filter(Todo.recurring_todo_id == rt.id)
        .order_by(Todo.due_date.desc().nulls_last(), Todo.created_at.desc())
        .first()
    )
    if latest and latest.due_date:
        return date.fromisoformat(latest.due_date)
    if latest:
        return latest.created_at.date()
    return None


def process_recurring_todos(db: Session) -> int:
    """Check all active recurring todos and create instances where due.

    Uses last_generated_date on each template to track what was already
    generated. This prevents duplicate creation when an instance is
    completed or deleted, and ensures the next instance advances to the
    correct recurrence period.

    Returns the number of new todos created.
    """
    today = today_utc()
    created_count = 0

    recurring = db.query(RecurringTodo).filter(RecurringTodo.active == True).all()  # noqa: E712

    for rt in recurring:
        # Skip if there's an open (incomplete) instance
        open_todo = (
            db.query(Todo)
            .filter(
                Todo.recurring_todo_id == rt.id,
                Todo.completed == False,  # noqa: E712
            )
            .first()
        )
        if open_todo:
            continue

        # Determine what recurrence date was last generated
        ref_date = _resolve_reference_date(rt, db)

        if ref_date:
            # Calculate the next recurrence date after the last generated one
            recurrence_date = get_next_recurrence_date(rt, ref_date)
        else:
            # First instance: use the current period so it is not backdated
            recurrence_date = get_first_recurrence_date(rt, today)

        # Create new todo instance
        due_date = str(recurrence_date) if rt.has_due_date else None
        new_todo = Todo(
            title=rt.title,
            description=rt.description,
            assigned_to=rt.assigned_to,
            due_date=due_date,
            remind_days_before=rt.remind_days_before if rt.has_due_date else None,
            recurring_todo_id=rt.id,
            completed=False,
        )
        db.add(new_todo)

        # Track the recurrence date so future runs know what was generated
        rt.last_generated_date = str(recurrence_date)

        created_count += 1

    if created_count > 0:
        db.commit()

    return created_count
