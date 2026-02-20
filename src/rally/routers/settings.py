"""Settings and calendars router for Rally."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from rally.database import get_db
from rally.models import Calendar, Setting
from rally.schemas import (
    CalendarCreate,
    CalendarResponse,
    CalendarUpdate,
    SettingsResponse,
    SettingsUpdate,
)

router = APIRouter(tags=["settings"])


# --- Key-value settings ---


@router.get("/api/settings", response_model=SettingsResponse)
def get_settings(db: Session = Depends(get_db)):
    """Get all settings as a flat dict."""
    rows = db.query(Setting).all()
    return SettingsResponse(settings={r.key: r.value for r in rows})


@router.put("/api/settings", response_model=SettingsResponse)
def update_settings(payload: SettingsUpdate, db: Session = Depends(get_db)):
    """Bulk upsert settings."""
    for key, value in payload.settings.items():
        existing = db.query(Setting).filter(Setting.key == key).first()
        if existing:
            existing.value = value
        else:
            db.add(Setting(key=key, value=value))
    db.commit()

    rows = db.query(Setting).all()
    return SettingsResponse(settings={r.key: r.value for r in rows})


# --- Calendars ---


@router.get("/api/calendars", response_model=list[CalendarResponse])
def list_calendars(db: Session = Depends(get_db)):
    """List all calendar feeds."""
    return db.query(Calendar).order_by(Calendar.label.asc()).all()


@router.post("/api/calendars", response_model=CalendarResponse, status_code=201)
def create_calendar(cal: CalendarCreate, db: Session = Depends(get_db)):
    """Create a new calendar feed."""
    db_cal = Calendar(
        label=cal.label,
        url=cal.url,
        family_member_id=cal.family_member_id,
        owner_email=cal.owner_email,
    )
    db.add(db_cal)
    db.commit()
    db.refresh(db_cal)
    return db_cal


@router.get("/api/calendars/{cal_id}", response_model=CalendarResponse)
def get_calendar(cal_id: int, db: Session = Depends(get_db)):
    """Get a specific calendar by ID."""
    cal = db.query(Calendar).filter(Calendar.id == cal_id).first()
    if not cal:
        raise HTTPException(status_code=404, detail="Calendar not found")
    return cal


@router.put("/api/calendars/{cal_id}", response_model=CalendarResponse)
def update_calendar(
    cal_id: int,
    cal: CalendarUpdate,
    db: Session = Depends(get_db),
):
    """Update a calendar feed."""
    db_cal = db.query(Calendar).filter(Calendar.id == cal_id).first()
    if not db_cal:
        raise HTTPException(status_code=404, detail="Calendar not found")

    if cal.label is not None:
        db_cal.label = cal.label
    if cal.url is not None:
        db_cal.url = cal.url
    if cal.family_member_id is not None:
        db_cal.family_member_id = cal.family_member_id
    if cal.owner_email is not None:
        db_cal.owner_email = cal.owner_email

    db.commit()
    db.refresh(db_cal)
    return db_cal


@router.delete("/api/calendars/{cal_id}", status_code=204)
def delete_calendar(cal_id: int, db: Session = Depends(get_db)):
    """Delete a calendar feed."""
    db_cal = db.query(Calendar).filter(Calendar.id == cal_id).first()
    if not db_cal:
        raise HTTPException(status_code=404, detail="Calendar not found")

    db.delete(db_cal)
    db.commit()
    return None
