"""Devices, and the per-person behavioral settings answered on one.

Rally has no logins, so a *device* is the closest thing it has to a session:
one browser, one token it minted itself and keeps in its own storage, one set
of answers about how Rally should behave there. Everything here is addressed by
that token.

There is no enrollment and nothing to pair. The household is already behind one
front door, and a device that turns up with an id Rally has not seen is a family
member opening the app, not an intruder — so the first request carrying a token
creates the record.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from rally import member_prefs
from rally.database import get_db
from rally.models import FamilyMember
from rally.schemas import (
    DeviceAnnounce,
    DevicePreferencesResponse,
    DeviceResponse,
    MemberPreferencesResponse,
    MemberPreferencesUpdate,
)

router = APIRouter(prefix="/api/devices", tags=["devices"])


def _response(device, counts: dict[str, int]) -> DeviceResponse:
    body = DeviceResponse.model_validate(device)
    body.answer_count = counts.get(device.id, 0)
    return body


@router.get("", response_model=list[DeviceResponse])
def list_devices(db: Session = Depends(get_db)):
    """Every device Rally has heard from, most recently seen first.

    The list exists so the household can see what is being remembered on its
    behalf and throw away what is not. A browser that clears its storage comes
    back as a new device and leaves the old row behind, so without somewhere to
    see them these accumulate silently and unreachably.
    """
    counts = member_prefs.answer_counts(db)
    return [_response(device, counts) for device in member_prefs.devices(db)]


@router.put("/{device_id}", response_model=DeviceResponse)
def announce_device(device_id: str, body: DeviceAnnounce, db: Session = Depends(get_db)):
    """Register this device, or bump when it was last seen, and name it.

    A PUT to a known URL rather than a POST that assigns an id: the browser
    minted the token and is addressing its own record. Omitting ``label``
    leaves a stored name alone, so a page saying hello on load cannot overwrite
    one somebody typed.
    """
    device_id = (device_id or "").strip()
    if not device_id:
        raise HTTPException(status_code=422, detail="A device id is required")

    device = member_prefs.touch_device(db, device_id, body.label)
    return _response(device, member_prefs.answer_counts(db))


@router.delete("/{device_id}", status_code=204)
def forget_device(device_id: str, db: Session = Depends(get_db)):
    """Forget a device and every answer stored for it.

    The answers go with the device deliberately. Keeping them would mean a
    forgotten device that silently came back — same browser, same token — found
    its old preferences waiting, which is not what "forget" says. A browser
    whose device is forgotten under it simply falls back to ``auto``, which is
    what Rally does for a device it has never met.
    """
    from rally.models import Device

    device = db.query(Device).filter(Device.id == device_id).first()
    member_prefs.delete_device_preferences(db, device_id)
    if device is not None:
        db.delete(device)
        db.commit()
    return None


@router.get("/{device_id}/preferences", response_model=DevicePreferencesResponse)
def device_preferences(device_id: str, db: Session = Depends(get_db)):
    """Every member's answers on this device, resolved, keyed by member id.

    Answers for a device Rally has never seen are the defaults rather than a
    404: a brand new browser asking what it should do is the ordinary first
    request, not an error, and the honest answer is "whatever Rally does by
    default".
    """
    return DevicePreferencesResponse(
        device_id=device_id,
        members={
            str(member_id): values
            for member_id, values in member_prefs.preferences_for_device(db, device_id).items()
        },
    )


@router.put(
    "/{device_id}/preferences/{member_id}",
    response_model=MemberPreferencesResponse,
)
def set_member_preferences(
    device_id: str,
    member_id: int,
    body: MemberPreferencesUpdate,
    db: Session = Depends(get_db),
):
    """Write one member's answers on this device.

    Partial: a setting left out keeps its answer, so one dropdown saves itself
    without overwriting the others with whatever the page last rendered. The
    device record is created here if it does not exist, because saving a
    preference is the strongest possible statement that the device is real —
    requiring a separate announce first would only add a way for the two to get
    out of step.
    """
    device_id = (device_id or "").strip()
    if not device_id:
        raise HTTPException(status_code=422, detail="A device id is required")
    if not db.query(FamilyMember).filter(FamilyMember.id == member_id).first():
        raise HTTPException(status_code=404, detail="Family member not found")

    member_prefs.touch_device(db, device_id)
    values = member_prefs.set_preferences(db, member_id, device_id, body.values)
    return MemberPreferencesResponse(device_id=device_id, family_member_id=member_id, values=values)
