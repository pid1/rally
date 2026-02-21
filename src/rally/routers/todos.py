"""Todos router for Rally."""

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from rally.database import get_db
from rally.models import Todo
from rally.recurrence import process_recurring_todos
from rally.schemas import UNSET, TodoCreate, TodoResponse, TodoUpdate
from rally.utils.timezone import now_utc

router = APIRouter(prefix="/api/todos", tags=["todos"])


@router.get("", response_model=list[TodoResponse])
def list_todos(
    include_hidden: bool = Query(False, description="Include completed todos older than 24 hours"),
    db: Session = Depends(get_db),
):
    """List all todos with 24-hour visibility for completed items.

    By default, completed todos older than 24 hours are hidden.
    Use include_hidden=true to show all todos.
    """
    # Process any due recurring todos before listing
    process_recurring_todos(db)

    query = db.query(Todo)

    if not include_hidden:
        # Show incomplete todos OR completed within last 24 hours
        cutoff = now_utc() - timedelta(hours=24)
        query = query.filter(
            (Todo.completed == False) | (Todo.updated_at > cutoff)  # noqa: E712
        )

    # Sort by created_at DESC (newest first)
    todos = query.order_by(Todo.created_at.desc()).all()
    return todos


@router.post("", response_model=TodoResponse, status_code=201)
def create_todo(todo: TodoCreate, db: Session = Depends(get_db)):
    """Create a new todo."""
    db_todo = Todo(
        title=todo.title,
        description=todo.description,
        due_date=todo.due_date,
        assigned_to=todo.assigned_to,
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
    if todo.due_date is not None:
        db_todo.due_date = todo.due_date
    if todo.assigned_to is not UNSET:
        db_todo.assigned_to = todo.assigned_to
    if todo.completed is not None:
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
