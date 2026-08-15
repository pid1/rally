"""Family members router for Rally."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from rally.database import get_db
from rally.models import Calendar, FamilyMember
from rally.schemas import UNSET, FamilyMemberCreate, FamilyMemberResponse, FamilyMemberUpdate

router = APIRouter(prefix="/api/family", tags=["family"])


@router.get("", response_model=list[FamilyMemberResponse])
def list_family_members(db: Session = Depends(get_db)):
    """List all family members."""
    return db.query(FamilyMember).order_by(FamilyMember.name.asc()).all()


@router.post("", response_model=FamilyMemberResponse, status_code=201)
def create_family_member(member: FamilyMemberCreate, db: Session = Depends(get_db)):
    """Create a new family member."""
    db_member = FamilyMember(
        name=member.name,
        color=member.color,
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
    return db_member


@router.get("/{member_id}", response_model=FamilyMemberResponse)
def get_family_member(member_id: int, db: Session = Depends(get_db)):
    """Get a specific family member by ID."""
    member = db.query(FamilyMember).filter(FamilyMember.id == member_id).first()
    if not member:
        raise HTTPException(status_code=404, detail="Family member not found")
    return member


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
    return db_member


@router.delete("/{member_id}", status_code=204)
def delete_family_member(member_id: int, db: Session = Depends(get_db)):
    """Delete a family member."""
    db_member = db.query(FamilyMember).filter(FamilyMember.id == member_id).first()
    if not db_member:
        raise HTTPException(status_code=404, detail="Family member not found")

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
