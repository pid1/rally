"""Notes router for Rally."""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from rally.database import get_db
from rally.models import Note
from rally.schemas import NoteCreate, NotePage, NoteResponse, NoteUpdate
from rally.utils.settings import today_local_str

router = APIRouter(prefix="/api/notes", tags=["notes"])


def _get_or_404(note_id: int, db: Session) -> Note:
    note = db.query(Note).filter(Note.id == note_id).first()
    if not note:
        raise HTTPException(status_code=404, detail="Note not found")
    return note


def _reject_if_past(note: Note, db: Session) -> None:
    """A day that has passed is a record, not a draft.

    Enforced here and not only in the UI: the previous-notes page renders
    without an Edit button, but the endpoint is reachable regardless and the
    read-only promise has to hold where the write happens.
    """
    if note.date < today_local_str(db):
        raise HTTPException(status_code=403, detail="Notes for past days are read-only")


@router.get("", response_model=list[NoteResponse])
def list_notes(db: Session = Depends(get_db)):
    """List notes from today onward, oldest first.

    No upper bound: the planner runs to the end of the list, matching the Meal
    Planner rather than capping at a week.
    """
    today = today_local_str(db)
    return db.query(Note).filter(Note.date >= today).order_by(Note.date.asc()).all()


@router.get("/previous", response_model=NotePage)
def list_previous_notes(
    search: str | None = Query(
        None, description="Case-insensitive keyword matched against the note body."
    ),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """List notes for days before today, newest first.

    The exact complement of the default listing: a note for today stays on the
    Notes page and appears here only once the local date rolls over.

    Searching and pagination are server-side because the client only ever holds
    one page. ``total`` counts matches across every page, which is what the
    results count reports — a per-page count would say "50 matching notes" no
    matter how many there are.
    """
    today = today_local_str(db)
    query = db.query(Note).filter(Note.date < today)

    if search and search.strip():
        query = query.filter(Note.body.ilike(f"%{search.strip()}%"))

    total = query.count()
    # One extra row answers "is there another page" without a second count.
    rows = query.order_by(Note.date.desc()).offset(offset).limit(limit + 1).all()
    return NotePage(items=rows[:limit], has_more=len(rows) > limit, total=total)


@router.get("/{note_id}", response_model=NoteResponse)
def get_note(note_id: int, db: Session = Depends(get_db)):
    """Get one note."""
    return _get_or_404(note_id, db)


@router.post("", response_model=NoteResponse, status_code=201)
def create_note(note: NoteCreate, db: Session = Depends(get_db)):
    """Create a note for a day that does not have one.

    A date that already has a note is a `409` carrying that note's id, so the
    modal can switch to editing it rather than refusing or overwriting. The
    page checks its loaded list first; this is the guard for the case where two
    people write the same day at once.
    """
    if note.date < today_local_str(db):
        raise HTTPException(status_code=403, detail="Notes for past days are read-only")

    existing = db.query(Note).filter(Note.date == note.date).first()
    if existing:
        raise HTTPException(
            status_code=409,
            detail={"message": f"A note already exists for {note.date}", "id": existing.id},
        )

    db_note = Note(date=note.date, body=note.body)
    db.add(db_note)
    db.commit()
    db.refresh(db_note)
    return db_note


@router.put("/{note_id}", response_model=NoteResponse)
def update_note(note_id: int, update: NoteUpdate, db: Session = Depends(get_db)):
    """Update a note's text, its date, or both."""
    db_note = _get_or_404(note_id, db)
    _reject_if_past(db_note, db)

    if update.date is not None and update.date != db_note.date:
        if update.date < today_local_str(db):
            raise HTTPException(status_code=403, detail="Notes cannot be moved into the past")
        clash = db.query(Note).filter(Note.date == update.date, Note.id != note_id).first()
        if clash:
            raise HTTPException(
                status_code=409,
                detail={"message": f"A note already exists for {update.date}", "id": clash.id},
            )
        db_note.date = update.date

    if update.body is not None:
        db_note.body = update.body

    db.commit()
    db.refresh(db_note)
    return db_note


@router.delete("/{note_id}")
def delete_note(note_id: int, db: Session = Depends(get_db)):
    """Delete a note for today or a future day."""
    db_note = _get_or_404(note_id, db)
    _reject_if_past(db_note, db)
    db.delete(db_note)
    db.commit()
    return {"status": "success"}
