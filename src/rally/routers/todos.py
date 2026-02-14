"""Todos router for Rally."""

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from rally.database import get_db
from rally.schemas import TodoCreate, TodoResponse, TodoUpdate

router = APIRouter(prefix="/api/todos", tags=["todos"])

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


@router.get("", response_model=list[TodoResponse])
def list_todos(
    completed: bool | None = None,
    db: Session = Depends(get_db),
):
    """List all todos, optionally filtered by completion status."""
    # TODO: Implement when crud.py is ready
    return []


@router.post("", response_model=TodoResponse, status_code=201)
def create_todo(todo: TodoCreate, db: Session = Depends(get_db)):
    """Create a new todo."""
    # TODO: Implement when crud.py is ready
    raise HTTPException(status_code=501, detail="Not implemented yet")


@router.get("/{todo_id}", response_model=TodoResponse)
def get_todo(todo_id: int, db: Session = Depends(get_db)):
    """Get a specific todo by ID."""
    # TODO: Implement when crud.py is ready
    raise HTTPException(status_code=501, detail="Not implemented yet")


@router.put("/{todo_id}", response_model=TodoResponse)
def update_todo(
    todo_id: int,
    todo: TodoUpdate,
    db: Session = Depends(get_db),
):
    """Update a todo."""
    # TODO: Implement when crud.py is ready
    raise HTTPException(status_code=501, detail="Not implemented yet")


@router.delete("/{todo_id}", status_code=204)
def delete_todo(todo_id: int, db: Session = Depends(get_db)):
    """Delete a todo."""
    # TODO: Implement when crud.py is ready
    raise HTTPException(status_code=501, detail="Not implemented yet")
