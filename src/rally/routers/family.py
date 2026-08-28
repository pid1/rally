"""Family members router for Rally."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from rally import member_colors, notification_prefs
from rally.database import get_db
from rally.models import Calendar, FamilyMember
from rally.schemas import UNSET, FamilyMemberCreate, FamilyMemberResponse, FamilyMemberUpdate

router = APIRouter(prefix="/api/family", tags=["family"])


def _response(db: Session, member: FamilyMember) -> FamilyMemberResponse:
    """A member plus their resolved notification preferences.

    Resolved rather than raw: an absent row means the kind's default, and
    making every client work that out for itself is how two of them end up
    disagreeing about what "not set" means.
    """
    body = FamilyMemberResponse.model_validate(member)
    body.notifications = notification_prefs.preferences(db, member.id)
    return body


@router.get("", response_model=list[FamilyMemberResponse])
def list_family_members(db: Session = Depends(get_db)):
    """List all family members."""
    members = db.query(FamilyMember).order_by(FamilyMember.name.asc()).all()
    return [_response(db, member) for member in members]


@router.post("", response_model=FamilyMemberResponse, status_code=201)
def create_family_member(member: FamilyMemberCreate, db: Session = Depends(get_db)):
    """Create a new family member.

    A caller who says nothing about color gets the first palette entry nobody
    is using. The schema has a default, so "omitted" and "sent the default" are
    only distinguishable through ``model_fields_set`` — and they mean different
    things here: a family should never have to think about color to end up with
    distinct dots, which is the failure this whole feature exists to prevent.
    """
    if "color" in member.model_fields_set:
        color = member.color
    else:
        taken = [value for (value,) in db.query(FamilyMember.color).all()]
        color = member_colors.next_unused(taken)

    db_member = FamilyMember(
        name=member.name,
        color=color,
        pushover_user_key=(member.pushover_user_key or None),
        pushover_device=(member.pushover_device or None),
    )
    db.add(db_member)
    db.commit()
    db.refresh(db_member)

    # Every family member gets somewhere to put an event. The migration seeds
    # one per existing member; this is the same guarantee for new ones.
    db.add(
        Calendar(
            label=f"{db_member.name}'s Calendar",
            url="",
            family_member_id=db_member.id,
            cal_type="native",
        )
    )
    db.commit()

    # Nothing is written when the caller says nothing: a new member starts on
    # the catalogue's defaults, which is everything on except shopping
    # additions.
    if member.notifications:
        notification_prefs.set_preferences(db, db_member.id, member.notifications)
    return _response(db, db_member)


@router.get("/{member_id}", response_model=FamilyMemberResponse)
def get_family_member(member_id: int, db: Session = Depends(get_db)):
    """Get a specific family member by ID."""
    member = db.query(FamilyMember).filter(FamilyMember.id == member_id).first()
    if not member:
        raise HTTPException(status_code=404, detail="Family member not found")
    return _response(db, member)


@router.put("/{member_id}", response_model=FamilyMemberResponse)
def update_family_member(
    member_id: int,
    member: FamilyMemberUpdate,
    db: Session = Depends(get_db),
):
    """Update a family member."""
    db_member = db.query(FamilyMember).filter(FamilyMember.id == member_id).first()
    if not db_member:
        raise HTTPException(status_code=404, detail="Family member not found")

    if member.name is not None:
        db_member.name = member.name
    if member.color is not None:
        db_member.color = member.color
    if member.pushover_user_key is not UNSET:
        db_member.pushover_user_key = (member.pushover_user_key or "").strip() or None
    if member.pushover_device is not UNSET:
        db_member.pushover_device = (member.pushover_device or "").strip() or None

    db.commit()
    db.refresh(db_member)

    # A partial map: kinds the caller left out are left where they are. An
    # unknown kind never reaches here — the schema rejects it with a 422 rather
    # than storing a preference nothing will ever read.
    if member.notifications is not UNSET and member.notifications:
        notification_prefs.set_preferences(db, db_member.id, member.notifications)
    return _response(db, db_member)


@router.delete("/{member_id}", status_code=204)
def delete_family_member(member_id: int, db: Session = Depends(get_db)):
    """Delete a family member."""
    db_member = db.query(FamilyMember).filter(FamilyMember.id == member_id).first()
    if not db_member:
        raise HTTPException(status_code=404, detail="Family member not found")

    # Nothing enforces the reference, so the preference rows have to be cleared
    # by hand — the same reason deleting an event cascades its own attendees.
    notification_prefs.delete_preferences(db, member_id)
    db.delete(db_member)
    db.commit()
    return None


@router.post("/{member_id}/test-pushover")
def test_member_pushover(member_id: int, db: Session = Depends(get_db)):
    """Send a real push to this member's Pushover profile.

    Actually delivering a message is the only honest test: a well-formed key
    that belongs to somebody else's account looks identical to a correct one
    until a phone buzzes.
    """
    from rally.notifications import PushoverError, app_token, send_pushover

    db_member = db.query(FamilyMember).filter(FamilyMember.id == member_id).first()
    if not db_member:
        raise HTTPException(status_code=404, detail="Family member not found")

    token = app_token(db)
    if not token:
        return {"success": False, "error": "No Pushover application token configured"}
    user_key = (db_member.pushover_user_key or "").strip()
    if not user_key:
        return {"success": False, "error": f"{db_member.name} has no Pushover user key"}

    try:
        send_pushover(
            token,
            user_key,
            "Rally is connected. Event reminders will arrive here.",
            title="Rally",
            device=(db_member.pushover_device or "").strip() or None,
        )
    except PushoverError as exc:
        return {"success": False, "error": str(exc)}
    return {"success": True, "message": f"Test notification sent to {db_member.name}"}
