"""Recurring todos router for Rally."""

from datetime import date, datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from rally.database import get_db
from rally.models import RecurringTodo, Setting, Todo
from rally.recurrence import get_first_recurrence_date, get_next_recurrence_date
from rally.schemas import (
    UNSET,
    RecurrencePreviewRequest,
    RecurrencePreviewResponse,
    RecurringTodoCreate,
    RecurringTodoResponse,
    RecurringTodoUpdate,
)
from rally.utils.timezone import ensure_utc, now_utc, today_utc

router = APIRouter(prefix="/api/recurring-todos", tags=["recurring-todos"])

PREVIEW_OCCURRENCES = 3  # What the modal's read-back line shows: this one, then two


def format_local_completion(completed_at: datetime, local_tz: ZoneInfo) -> str:
    local_dt = completed_at.astimezone(local_tz)
    today = now_utc().astimezone(local_tz).date()
    if local_dt.date() == today:
        date_label = "Today"
    elif local_dt.date() == today.replace(day=today.day) - __import__(
        "datetime"
    ).timedelta(days=1):
        date_label = "Yesterday"
    else:
        suffix = (
            "th"
            if 11 <= local_dt.day % 100 <= 13
            else {1: "st", 2: "nd", 3: "rd"}.get(local_dt.day % 10, "th")
        )
        date_label = (
            f"{local_dt.strftime('%b')} {local_dt.day}{suffix}, {local_dt.year}"
        )
    time_label = local_dt.strftime("%I:%M %p").lstrip("0")
    return f"{date_label} at {time_label}"


def normalize_start_date(value: str | None) -> str | None:
    """Validate an optional start date, or 422.

    A stored string nothing can parse would silently gate generation forever,
    so the format is checked rather than trusted. ``date.fromisoformat`` also
    accepts compact and week-based forms, which the column is not; comparing
    the round trip keeps it to YYYY-MM-DD.
    """
    if not value:
        return None
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(
            status_code=422, detail="start_date must be YYYY-MM-DD"
        ) from exc
    if parsed.isoformat() != value:
        raise HTTPException(status_code=422, detail="start_date must be YYYY-MM-DD")
    return value


def has_completed_instance(db: Session, rt_id: int) -> bool:
    """Whether any instance of a template has ever been completed."""
    return (
        db.query(Todo.id)
        .filter(
            Todo.recurring_todo_id == rt_id,
            Todo.completed == True,  # noqa: E712
        )
        .first()
        is not None
    )


def reschedule_open_instance(db: Session, db_rt: RecurringTodo) -> None:
    """Move a generated-but-untouched series onto its new start date.

    The template owns the anchor, so moving the start date has to move both the
    open instance's due date *and* ``last_generated_date``. Editing the
    generated task by hand only ever moved that one task: the anchor stayed
    where it was and the series snapped back to it on the next completion.
    """
    if not db_rt.last_generated_date:
        return

    first = get_first_recurrence_date(db_rt, today_utc())
    open_todo = (
        db.query(Todo)
        .filter(
            Todo.recurring_todo_id == db_rt.id,
            Todo.completed == False,  # noqa: E712
        )
        .first()
    )
    if open_todo and db_rt.has_due_date:
        open_todo.due_date = str(first)
    db_rt.last_generated_date = str(first)


@router.get("", response_model=list[RecurringTodoResponse])
def list_recurring_todos(db: Session = Depends(get_db)):
    """List all recurring todo templates."""
    rts = db.query(RecurringTodo).order_by(RecurringTodo.created_at.desc()).all()

    completed_rows = (
        db.query(
            Todo.recurring_todo_id,
            func.max(func.coalesce(Todo.completed_at, Todo.updated_at)).label(
                "last_completed_at"
            ),
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
        start_date=normalize_start_date(rt.start_date),
    )
    db.add(db_rt)
    db.commit()
    db.refresh(db_rt)
    return db_rt


@router.post("/preview", response_model=RecurrencePreviewResponse)
def preview_recurrence(payload: RecurrencePreviewRequest):
    """Read back the dates an unsaved rule would actually produce.

    The modal renders whatever this returns rather than reimplementing the
    recurrence math in JavaScript: rally.recurrence stays the only place in the
    codebase that knows what "every 12 months on the first Sunday" means.

    The dates come from the rule and today, the same way a brand-new template's
    first instance is placed. A series already running from a completion anchor
    can differ — see ``_resolve_reference_date`` — which is why the modal shows
    this line as a preview of the schedule rather than a promise about one row.
    """
    probe = RecurringTodo(
        title="",
        recurrence_type=payload.recurrence_type,
        recurrence_day=payload.recurrence_day,
        custom_rule=payload.custom_rule,
        start_date=normalize_start_date(payload.start_date),
    )

    occurrences = [get_first_recurrence_date(probe, today_utc())]
    while len(occurrences) < PREVIEW_OCCURRENCES:
        occurrences.append(get_next_recurrence_date(probe, occurrences[-1]))

    return RecurrencePreviewResponse(occurrences=[str(day) for day in occurrences])


@router.get("/{rt_id}", response_model=RecurringTodoResponse)
def get_recurring_todo(rt_id: int, db: Session = Depends(get_db)):
    """Get a specific recurring todo template."""
    rt = db.query(RecurringTodo).filter(RecurringTodo.id == rt_id).first()
    if not rt:
        raise HTTPException(status_code=404, detail="Recurring todo not found")
    return rt


@router.put("/{rt_id}", response_model=RecurringTodoResponse)
def update_recurring_todo(
    rt_id: int, rt: RecurringTodoUpdate, db: Session = Depends(get_db)
):
    """Update a recurring todo template."""
    db_rt = db.query(RecurringTodo).filter(RecurringTodo.id == rt_id).first()
    if not db_rt:
        raise HTTPException(status_code=404, detail="Recurring todo not found")

    # Resolve the start date before anything is written: a series with a
    # completion has a real anchor, so its start date is history and the
    # request is refused rather than half-applied. Re-sending the value it
    # already holds is not a change and stays allowed — the modal shows the
    # field, disabled, and the rest of the form still has to save.
    new_start = UNSET
    if rt.start_date is not UNSET:
        candidate = normalize_start_date(rt.start_date)
        if candidate != db_rt.start_date:
            if has_completed_instance(db, db_rt.id):
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "start_date cannot change once an instance has been completed; "
                        "the last completion drives the series from then on"
                    ),
                )
            new_start = candidate

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

    # Applied last, so the new first occurrence is computed from the rule as it
    # now stands. Before anything is generated this is a plain field write;
    # after, it re-dates the open instance and the anchor with it.
    if new_start is not UNSET:
        db_rt.start_date = new_start
        reschedule_open_instance(db, db_rt)

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
