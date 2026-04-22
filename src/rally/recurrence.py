"""Recurring todo processing for Rally.

Checks active recurring todo templates and generates todo instances
when the recurrence is due and no open instance exists.
"""

import calendar as cal_module
from datetime import date, timedelta

from sqlalchemy.orm import Session

from rally.models import RecurringTodo, Todo
from rally.utils.timezone import today_utc


# ── Custom rule helpers ──────────────────────────────────────────────────────


def _advance_months(year: int, month: int, interval: int) -> tuple[int, int]:
    """Advance year/month by interval months."""
    month += interval
    year += (month - 1) // 12
    month = ((month - 1) % 12) + 1
    return year, month


def _find_nth_weekday_in_month(year: int, month: int, ordinal: str, weekday: int) -> date:
    """Find the nth occurrence of a weekday in a month.

    ordinal: "first", "second", "third", "fourth", "last"
    weekday: 0=Monday … 6=Sunday

    Falls back to the last valid occurrence when the requested ordinal doesn't
    exist (e.g. "fifth Monday").
    """
    num_days = cal_module.monthrange(year, month)[1]
    occurrences = [date(year, month, d) for d in range(1, num_days + 1) if date(year, month, d).weekday() == weekday]

    ordinal_map = {"first": 0, "second": 1, "third": 2, "fourth": 3, "last": -1}
    idx = ordinal_map.get(ordinal, 0)

    if idx == -1 or idx >= len(occurrences):
        return occurrences[-1]
    return occurrences[idx]


def _next_custom(rule: dict, after_date: date) -> date:
    """Next occurrence of a custom rule strictly after after_date."""
    freq = rule["freq"]
    interval = int(rule.get("interval", 1))

    if freq == "daily":
        return after_date + timedelta(days=interval)

    if freq == "weekly":
        weekdays = sorted(rule["weekdays"])
        current_wd = after_date.weekday()
        later_this_week = [w for w in weekdays if w > current_wd]
        if later_this_week:
            return after_date + timedelta(days=later_this_week[0] - current_wd)
        # Advance to the first listed weekday of the next N-week cycle
        week_start = after_date - timedelta(days=current_wd)
        return week_start + timedelta(weeks=interval, days=weekdays[0])

    if freq == "monthly":
        mode = rule.get("mode", "day")
        if mode == "day":
            day = int(rule["day"])
            clamped = min(day, cal_module.monthrange(after_date.year, after_date.month)[1])
            candidate = after_date.replace(day=clamped)
            if candidate > after_date:
                return candidate
            ny, nm = _advance_months(after_date.year, after_date.month, interval)
            return date(ny, nm, min(day, cal_module.monthrange(ny, nm)[1]))

        if mode == "weekday":
            ordinal = rule["ordinal"]
            weekday = int(rule["weekday"])
            candidate = _find_nth_weekday_in_month(after_date.year, after_date.month, ordinal, weekday)
            if candidate > after_date:
                return candidate
            ny, nm = _advance_months(after_date.year, after_date.month, interval)
            return _find_nth_weekday_in_month(ny, nm, ordinal, weekday)

    return after_date + timedelta(days=1)


def _last_custom(rule: dict, today: date) -> date:
    """Most recent occurrence of a custom rule on or before today."""
    freq = rule["freq"]
    interval = int(rule.get("interval", 1))

    if freq == "daily":
        return today

    if freq == "weekly":
        weekdays = sorted(rule["weekdays"])
        current_wd = today.weekday()
        past = [w for w in weekdays if w <= current_wd]
        if past:
            return today - timedelta(days=current_wd - past[-1])
        # Previous N-week cycle — last listed weekday
        week_start = today - timedelta(days=current_wd)
        prev_week_start = week_start - timedelta(weeks=interval)
        return prev_week_start + timedelta(days=weekdays[-1])

    if freq == "monthly":
        mode = rule.get("mode", "day")
        if mode == "day":
            day = int(rule["day"])
            clamped = min(day, cal_module.monthrange(today.year, today.month)[1])
            if today.day >= clamped:
                return today.replace(day=clamped)
            first = today.replace(day=1)
            prev_end = first - timedelta(days=1)
            return prev_end.replace(day=min(day, cal_module.monthrange(prev_end.year, prev_end.month)[1]))

        if mode == "weekday":
            ordinal = rule["ordinal"]
            weekday = int(rule["weekday"])
            candidate = _find_nth_weekday_in_month(today.year, today.month, ordinal, weekday)
            if candidate <= today:
                return candidate
            first = today.replace(day=1)
            prev_end = first - timedelta(days=1)
            return _find_nth_weekday_in_month(prev_end.year, prev_end.month, ordinal, weekday)

    return today


def _first_custom(rule: dict, today: date) -> date:
    """First occurrence of a custom rule on or after today (for new templates)."""
    freq = rule["freq"]

    if freq == "daily":
        return today

    if freq == "weekly":
        weekdays = sorted(rule["weekdays"])
        current_wd = today.weekday()
        upcoming = [w for w in weekdays if w >= current_wd]
        if upcoming:
            return today + timedelta(days=upcoming[0] - current_wd)
        # Next week — take the first listed weekday (interval doesn't backdate first occurrence)
        week_start = today - timedelta(days=current_wd)
        return week_start + timedelta(days=7 + weekdays[0])

    if freq == "monthly":
        mode = rule.get("mode", "day")
        if mode == "day":
            day = int(rule["day"])
            clamped = min(day, cal_module.monthrange(today.year, today.month)[1])
            candidate = today.replace(day=clamped)
            if candidate >= today:
                return candidate
            interval = int(rule.get("interval", 1))
            ny, nm = _advance_months(today.year, today.month, interval)
            return date(ny, nm, min(day, cal_module.monthrange(ny, nm)[1]))

        if mode == "weekday":
            ordinal = rule["ordinal"]
            weekday = int(rule["weekday"])
            candidate = _find_nth_weekday_in_month(today.year, today.month, ordinal, weekday)
            if candidate >= today:
                return candidate
            interval = int(rule.get("interval", 1))
            ny, nm = _advance_months(today.year, today.month, interval)
            return _find_nth_weekday_in_month(ny, nm, ordinal, weekday)

    return today


# ── Public recurrence API ────────────────────────────────────────────────────


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
    elif rt.recurrence_type == "custom" and rt.custom_rule:
        return _last_custom(rt.custom_rule, today)
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
    elif rt.recurrence_type == "custom" and rt.custom_rule:
        return _next_custom(rt.custom_rule, after_date)
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
    elif rt.recurrence_type == "custom" and rt.custom_rule:
        return _first_custom(rt.custom_rule, today)
    return today


def _resolve_reference_date(rt: RecurringTodo, db: Session) -> date | None:
    """Determine the reference date for calculating the next instance.

    Returns the recurrence date of the most recently generated instance,
    or None if no instances have ever been created.
    """
    if rt.last_generated_date:
        ref = date.fromisoformat(rt.last_generated_date)
        # If a completed instance was rescheduled to a later date, advance the
        # reference so the next occurrence doesn't land on the same day.
        latest_completed = (
            db.query(Todo)
            .filter(
                Todo.recurring_todo_id == rt.id,
                Todo.completed == True,  # noqa: E712
                Todo.due_date.isnot(None),
            )
            .order_by(Todo.due_date.desc())
            .first()
        )
        if latest_completed:
            return max(ref, date.fromisoformat(latest_completed.due_date))
        return ref

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
