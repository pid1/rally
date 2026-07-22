"""Todos router for Rally."""

from datetime import UTC, datetime, time
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import false, nullslast, or_
from sqlalchemy.orm import Session

from rally.database import get_db
from rally.models import FamilyMember, Setting, Todo
from rally.recurrence import process_recurring_todos
from rally.schemas import UNSET, CompletedTodoPage, TodoCreate, TodoResponse, TodoUpdate
from rally.utils.timezone import now_utc, today_local

router = APIRouter(prefix="/api/todos", tags=["todos"])

COMPLETED_SORTS = (
    "completed-newest",
    "completed-oldest",
    "due-soonest",
    "due-furthest",
    "assignee",
    "newest",
    "oldest",
)


def today_start_utc(db: Session) -> datetime:
    """UTC instant of local midnight today, in the user's configured timezone.

    This is the boundary between "current" todos (shown on /todo) and
    "previously completed" todos (shown on /todo/completed). Both views must
    use this same helper or todos would appear on both pages or neither.
    """
    setting = db.query(Setting).filter(Setting.key == "local_timezone").first()
    tz_name = setting.value if setting and setting.value else "UTC"
    local_midnight = datetime.combine(today_local(tz_name), time.min, tzinfo=ZoneInfo(tz_name))
    return local_midnight.astimezone(UTC)


@router.get("", response_model=list[TodoResponse])
def list_todos(
    include_hidden: bool = Query(
        False, description="Include completed todos older than today (local time)"
    ),
    db: Session = Depends(get_db),
):
    """List all todos with local time visibility for completed items.

    By default, completed todos prior to today's local date are hidden.
    Use include_hidden=true to show all todos.
    """
    # Process any due recurring todos before listing
    process_recurring_todos(db)

    query = db.query(Todo)

    if not include_hidden:
        # Show incomplete todos OR completed today (local time)
        cutoff = today_start_utc(db)
        query = query.filter(
            (Todo.completed == False) | (Todo.completed_at >= cutoff)  # noqa: E712
        )

    # Sort by created_at DESC (newest first)
    todos = query.order_by(Todo.created_at.desc()).all()
    return todos


@router.get("/completed", response_model=CompletedTodoPage)
def list_completed_todos(
    sort: str = Query("completed-newest", pattern=f"^({'|'.join(COMPLETED_SORTS)})$"),
    assignee: list[str] = Query(
        default=[],
        description='Family member IDs to filter to, and/or "unassigned". Empty means all.',
    ),
    search: str | None = Query(
        None, description="Case-insensitive keyword matched against title and description."
    ),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """List todos completed before today (local time), newest completion first.

    This is the exact complement of the default `GET /api/todos` listing: a todo
    completed today stays on the current tasks page and appears here only once
    the local date rolls over. Read-only — recurring processing is deliberately
    not run here.

    Sorting, searching and pagination are server-side because the client only
    ever holds one page of results.
    """
    cutoff = today_start_utc(db)

    query = db.query(Todo).filter(
        Todo.completed == True,  # noqa: E712
        # A completed todo with no completed_at (pre-migration-013 data that the
        # backfill missed) is already hidden from the current tasks page, so it
        # belongs here rather than falling through the gap between the two views.
        (Todo.completed_at < cutoff) | (Todo.completed_at.is_(None)),
    )

    # Assignee filter: multi-select OR, matching the current tasks page. Filtering
    # is server-side because the client only holds the pages it has loaded.
    if assignee:
        member_ids = [int(a) for a in assignee if a.isdigit()]
        clauses = []
        if "unassigned" in assignee:
            clauses.append(Todo.assigned_to.is_(None))
        if member_ids:
            clauses.append(Todo.assigned_to.in_(member_ids))
        query = query.filter(or_(*clauses)) if clauses else query.filter(false())

    # Keyword search: case-insensitive match against title OR description.
    if search and search.strip():
        term = f"%{search.strip()}%"
        query = query.filter(or_(Todo.title.ilike(term), Todo.description.ilike(term)))

    # Total matches for the current query, computed before ordering/joining so the
    # count is unaffected by the assignee-sort outer join. Drives the search
    # results-count indicator on the client.
    total = query.count()

    newest_completed_first = (nullslast(Todo.completed_at.desc()), Todo.id.desc())

    if sort == "completed-oldest":
        query = query.order_by(nullslast(Todo.completed_at.asc()), Todo.id.asc())
    elif sort == "due-soonest":
        query = query.order_by(nullslast(Todo.due_date.asc()), *newest_completed_first)
    elif sort == "due-furthest":
        query = query.order_by(nullslast(Todo.due_date.desc()), *newest_completed_first)
    elif sort == "assignee":
        query = query.outerjoin(FamilyMember, Todo.assigned_to == FamilyMember.id).order_by(
            nullslast(FamilyMember.name.asc()), *newest_completed_first
        )
    elif sort == "newest":
        query = query.order_by(Todo.created_at.desc(), Todo.id.desc())
    elif sort == "oldest":
        query = query.order_by(Todo.created_at.asc(), Todo.id.asc())
    else:  # completed-newest (default)
        query = query.order_by(*newest_completed_first)

    # Fetch one extra row to determine whether another page exists.
    rows = query.offset(offset).limit(limit + 1).all()
    return CompletedTodoPage(items=rows[:limit], has_more=len(rows) > limit, total=total)


@router.post("", response_model=TodoResponse, status_code=201)
def create_todo(todo: TodoCreate, db: Session = Depends(get_db)):
    """Create a new todo."""
    db_todo = Todo(
        title=todo.title,
        description=todo.description,
        due_date=todo.due_date,
        assigned_to=todo.assigned_to,
        remind_days_before=todo.remind_days_before,
        completed=False,
    )
    db.add(db_todo)
    db.commit()
    db.refresh(db_todo)
    return db_todo


@router.get("/{todo_id}", response_model=TodoResponse)
def get_todo(todo_id: int, db: Session = Depends(get_db)):
    """Get a specific todo by ID."""
    todo = db.query(Todo).filter(Todo.id == todo_id).first()
    if not todo:
        raise HTTPException(status_code=404, detail="Todo not found")
    return todo


@router.put("/{todo_id}", response_model=TodoResponse)
def update_todo(
    todo_id: int,
    todo: TodoUpdate,
    db: Session = Depends(get_db),
):
    """Update a todo."""
    db_todo = db.query(Todo).filter(Todo.id == todo_id).first()
    if not db_todo:
        raise HTTPException(status_code=404, detail="Todo not found")

    # Update only provided fields
    if todo.title is not None:
        db_todo.title = todo.title
    if todo.description is not None:
        db_todo.description = todo.description
    if todo.due_date is not UNSET:
        db_todo.due_date = todo.due_date
    if todo.assigned_to is not UNSET:
        db_todo.assigned_to = todo.assigned_to
    if todo.remind_days_before is not UNSET:
        db_todo.remind_days_before = todo.remind_days_before
    if todo.completed is not None:
        if todo.completed and not db_todo.completed:
            db_todo.completed_at = now_utc()
        elif not todo.completed:
            db_todo.completed_at = None
        db_todo.completed = todo.completed

    # updated_at is automatically set by SQLAlchemy via onupdate
    db.commit()
    db.refresh(db_todo)
    return db_todo


@router.delete("/{todo_id}", status_code=204)
def delete_todo(todo_id: int, db: Session = Depends(get_db)):
    """Delete a todo."""
    db_todo = db.query(Todo).filter(Todo.id == todo_id).first()
    if not db_todo:
        raise HTTPException(status_code=404, detail="Todo not found")

    db.delete(db_todo)
    db.commit()
    return None
