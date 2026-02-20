"""Family members router for Rally."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from rally.database import get_db
from rally.models import FamilyMember
from rally.schemas import FamilyMemberCreate, FamilyMemberResponse, FamilyMemberUpdate

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
    )
    db.add(db_member)
    db.commit()
    db.refresh(db_member)
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
