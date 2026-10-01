"""Checklists router for Rally.

Two prefixes, because they are two kinds of thing:

* ``/api/checklists`` is the reusable list: its details, its groups and its
  items. Everything here is the master, and every edit reaches every day the
  checklist is on (see ``rally.checklists`` for why that needs no sync).
* ``/api/checklist-days`` is a checklist put on a date, and the checks made on
  it. Nothing here writes to the checklist.

They are separate prefixes rather than ``/api/checklists/days`` so a day's id
can never be mistaken for a checklist's.
"""

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import func
from sqlalchemy.orm import Session

from rally import checklists as logic
from rally.database import get_db
from rally.models import (
    Checklist,
    ChecklistDay,
    ChecklistDayCheck,
    ChecklistGroup,
    ChecklistItem,
    FamilyMember,
)
from rally.schemas import (
    UNSET,
    ChecklistCheck,
    ChecklistCreate,
    ChecklistDayCreate,
    ChecklistDayItem,
    ChecklistDayResponse,
    ChecklistDaySummary,
    ChecklistDayUpdate,
    ChecklistGroupCreate,
    ChecklistGroupResponse,
    ChecklistGroupUpdate,
    ChecklistItemCreate,
    ChecklistItemResponse,
    ChecklistItemUpdate,
    ChecklistReorder,
    ChecklistResponse,
    ChecklistSummary,
    ChecklistUpdate,
)
from rally.utils.settings import today_local_str

router = APIRouter(prefix="/api/checklists", tags=["checklists"])
days_router = APIRouter(prefix="/api/checklist-days", tags=["checklists"])

# The Earlier list on the Checklists page is a glance back, not an archive.
PAST_DAYS_DEFAULT_LIMIT = 10


# --- Helpers -------------------------------------------------------------------


def _get_checklist(db: Session, checklist_id: int) -> Checklist:
    checklist = db.query(Checklist).filter(Checklist.id == checklist_id).first()
    if not checklist:
        raise HTTPException(status_code=404, detail="Checklist not found")
    return checklist


def _get_group(db: Session, checklist_id: int, group_id: int) -> ChecklistGroup:
    group = (
        db.query(ChecklistGroup)
        .filter(ChecklistGroup.id == group_id, ChecklistGroup.checklist_id == checklist_id)
        .first()
    )
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")
    return group


def _get_item(db: Session, checklist_id: int, item_id: int) -> ChecklistItem:
    item = (
        db.query(ChecklistItem)
        .filter(ChecklistItem.id == item_id, ChecklistItem.checklist_id == checklist_id)
        .first()
    )
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    return item


def _get_day(db: Session, day_id: int) -> ChecklistDay:
    day = db.query(ChecklistDay).filter(ChecklistDay.id == day_id).first()
    if not day:
        raise HTTPException(status_code=404, detail="Checklist day not found")
    return day


def _require_group(db: Session, checklist_id: int, group_id: int | None) -> None:
    """A group reference must be one of *this* checklist's groups.

    Checked against the checklist and not merely for existence: an item filed
    under another checklist's group would render as General here and under a
    heading on no page at all.
    """
    if group_id is None:
        return
    exists = (
        db.query(ChecklistGroup.id)
        .filter(ChecklistGroup.id == group_id, ChecklistGroup.checklist_id == checklist_id)
        .first()
    )
    if not exists:
        raise HTTPException(status_code=422, detail="Unknown group_id for this checklist")


def _name_taken(db: Session, name: str, *, exclude_id: int | None = None) -> bool:
    query = db.query(Checklist.id).filter(func.lower(Checklist.name) == name.lower())
    if exclude_id is not None:
        query = query.filter(Checklist.id != exclude_id)
    return query.first() is not None


def _group_name_taken(
    db: Session, checklist_id: int, name: str, *, exclude_id: int | None = None
) -> bool:
    query = db.query(ChecklistGroup.id).filter(
        ChecklistGroup.checklist_id == checklist_id,
        func.lower(ChecklistGroup.name) == name.lower(),
    )
    if exclude_id is not None:
        query = query.filter(ChecklistGroup.id != exclude_id)
    return query.first() is not None


def _bottom_of_group(db: Session, checklist_id: int, group_id: int | None) -> int:
    """The position that puts an item at the bottom of its group."""
    group_clause = (
        ChecklistItem.group_id.is_(None) if group_id is None else ChecklistItem.group_id == group_id
    )
    highest = (
        db.query(func.max(ChecklistItem.sort_order))
        .filter(ChecklistItem.checklist_id == checklist_id, group_clause)
        .scalar()
    )
    return 0 if highest is None else highest + 1


def _summaries(db: Session, rows: list[Checklist]) -> list[ChecklistSummary]:
    ids = [c.id for c in rows]
    items = logic.item_counts(db, ids)
    groups = dict.fromkeys(ids, 0)
    days = dict.fromkeys(ids, 0)
    upcoming = dict.fromkeys(ids, 0)
    if ids:
        groups.update(
            db.query(ChecklistGroup.checklist_id, func.count(ChecklistGroup.id))
            .filter(ChecklistGroup.checklist_id.in_(ids))
            .group_by(ChecklistGroup.checklist_id)
            .all()
        )
        days.update(
            db.query(ChecklistDay.checklist_id, func.count(ChecklistDay.id))
            .filter(ChecklistDay.checklist_id.in_(ids))
            .group_by(ChecklistDay.checklist_id)
            .all()
        )
        upcoming.update(
            db.query(ChecklistDay.checklist_id, func.count(ChecklistDay.id))
            .filter(ChecklistDay.checklist_id.in_(ids), ChecklistDay.date >= today_local_str(db))
            .group_by(ChecklistDay.checklist_id)
            .all()
        )
    return [
        ChecklistSummary(
            id=c.id,
            name=c.name,
            description=c.description,
            pack_timing=c.pack_timing,
            item_count=items[c.id],
            group_count=groups[c.id],
            day_count=days[c.id],
            upcoming_days=upcoming[c.id],
        )
        for c in rows
    ]


def _checklist_response(db: Session, checklist: Checklist) -> ChecklistResponse:
    summary = _summaries(db, [checklist])[0]
    groups = logic.ordered_groups(db, checklist.id)
    items = logic.ordered_items(db, checklist.id, groups)
    return ChecklistResponse(
        **summary.model_dump(),
        groups=[ChecklistGroupResponse.model_validate(g) for g in groups],
        items=[
            ChecklistItemResponse(
                id=i.id,
                group_id=logic.effective_group_id(i, groups),
                name=i.name,
                note=i.note,
                sort_order=i.sort_order,
            )
            for i in items
        ],
    )


def _day_summaries(db: Session, rows: list[ChecklistDay]) -> list[ChecklistDaySummary]:
    checklist_ids = list({d.checklist_id for d in rows})
    checklists = {
        c.id: c for c in db.query(Checklist).filter(Checklist.id.in_(checklist_ids)).all()
    }
    totals = logic.item_counts(db, checklist_ids)
    checked = logic.checked_counts(db, [d.id for d in rows])
    summaries = []
    for day in rows:
        checklist = checklists.get(day.checklist_id)
        if checklist is None:
            # A day whose checklist is gone has nothing to show. Deleting a
            # checklist removes its days, so this is a guard, not a path.
            continue
        summaries.append(
            ChecklistDaySummary(
                id=day.id,
                checklist_id=checklist.id,
                checklist_name=checklist.name,
                date=day.date,
                label=day.label,
                pack_timing=checklist.pack_timing,
                pack_date=logic.pack_date(day.date, checklist.pack_timing),
                total=totals.get(checklist.id, 0),
                checked=checked.get(day.id, 0),
            )
        )
    return summaries


def _day_response(db: Session, day: ChecklistDay) -> ChecklistDayResponse:
    summaries = _day_summaries(db, [day])
    if not summaries:
        raise HTTPException(status_code=404, detail="Checklist not found")
    groups = logic.ordered_groups(db, day.checklist_id)
    items = logic.ordered_items(db, day.checklist_id, groups)
    checked = logic.checked_item_ids(db, day.id)
    return ChecklistDayResponse(
        **summaries[0].model_dump(),
        groups=[ChecklistGroupResponse.model_validate(g) for g in groups],
        items=[
            ChecklistDayItem(
                id=i.id,
                group_id=logic.effective_group_id(i, groups),
                name=i.name,
                note=i.note,
                sort_order=i.sort_order,
                checked=i.id in checked,
            )
            for i in items
        ],
    )


def _date_clash(db: Session, checklist_id: int, day_date: str, *, exclude_id: int | None = None):
    query = db.query(ChecklistDay).filter(
        ChecklistDay.checklist_id == checklist_id, ChecklistDay.date == day_date
    )
    if exclude_id is not None:
        query = query.filter(ChecklistDay.id != exclude_id)
    clash = query.first()
    if clash:
        raise HTTPException(
            status_code=409,
            detail={"message": f"This checklist is already on {day_date}", "id": clash.id},
        )


# --- Checklists ------------------------------------------------------------------


@router.get("", response_model=list[ChecklistSummary])
def list_checklists(db: Session = Depends(get_db)):
    """Every checklist, by name, with what it holds and how many days use it."""
    rows = db.query(Checklist).order_by(func.lower(Checklist.name).asc()).all()
    return _summaries(db, rows)


@router.post("", response_model=ChecklistResponse, status_code=201)
def create_checklist(payload: ChecklistCreate, db: Session = Depends(get_db)):
    """Create a checklist. ``409`` when the name is taken, ignoring case."""
    if _name_taken(db, payload.name):
        raise HTTPException(status_code=409, detail="A checklist with that name already exists")
    checklist = Checklist(
        name=payload.name, description=payload.description, pack_timing=payload.pack_timing
    )
    db.add(checklist)
    db.commit()
    db.refresh(checklist)
    return _checklist_response(db, checklist)


@router.get("/{checklist_id}", response_model=ChecklistResponse)
def get_checklist(checklist_id: int, db: Session = Depends(get_db)):
    """A checklist with its groups and items, in the order they read."""
    return _checklist_response(db, _get_checklist(db, checklist_id))


@router.put("/{checklist_id}", response_model=ChecklistResponse)
def update_checklist(checklist_id: int, payload: ChecklistUpdate, db: Session = Depends(get_db)):
    """Rename a checklist, or change its description or when it is packed."""
    checklist = _get_checklist(db, checklist_id)
    if payload.name is not None and payload.name != checklist.name:
        if _name_taken(db, payload.name, exclude_id=checklist.id):
            raise HTTPException(status_code=409, detail="A checklist with that name already exists")
        checklist.name = payload.name
    if payload.description is not UNSET:
        checklist.description = payload.description
    if payload.pack_timing is not None:
        checklist.pack_timing = payload.pack_timing
    db.commit()
    db.refresh(checklist)
    return _checklist_response(db, checklist)


@router.delete("/{checklist_id}", status_code=204)
def delete_checklist(checklist_id: int, db: Session = Depends(get_db)):
    """Delete a checklist, its groups, its items, every day it is on and their checks."""
    logic.delete_checklist(db, _get_checklist(db, checklist_id))
    db.commit()
    return Response(status_code=204)


# --- Groups ------------------------------------------------------------------------


@router.post("/{checklist_id}/groups", response_model=ChecklistGroupResponse, status_code=201)
def create_group(checklist_id: int, payload: ChecklistGroupCreate, db: Session = Depends(get_db)):
    """Add a group to the end of the checklist. ``409`` when the name is taken."""
    _get_checklist(db, checklist_id)
    if _group_name_taken(db, checklist_id, payload.name):
        raise HTTPException(status_code=409, detail="This checklist already has that group")
    group = ChecklistGroup(
        checklist_id=checklist_id,
        name=payload.name,
        sort_order=_next_group_position(db, checklist_id),
    )
    db.add(group)
    db.commit()
    db.refresh(group)
    return group


def _next_group_position(db: Session, checklist_id: int) -> int:
    highest = (
        db.query(func.max(ChecklistGroup.sort_order))
        .filter(ChecklistGroup.checklist_id == checklist_id)
        .scalar()
    )
    return 0 if highest is None else highest + 1


@router.post("/{checklist_id}/groups/members", response_model=list[ChecklistGroupResponse])
def create_member_groups(checklist_id: int, db: Session = Depends(get_db)):
    """Group by person: one group per family member who does not have one yet.

    Matched by name, ignoring case, so a group somebody already typed as
    "emma" is Emma's and is not duplicated. Members are taken in the order
    ``/api/family`` lists them. Returns every group the checklist now has, in
    order, so the caller can re-render from one response. Idempotent.
    """
    _get_checklist(db, checklist_id)
    have = {
        g.name.casefold()
        for g in db.query(ChecklistGroup).filter(ChecklistGroup.checklist_id == checklist_id)
    }
    position = _next_group_position(db, checklist_id)
    for member in db.query(FamilyMember).order_by(FamilyMember.id.asc()).all():
        name = member.name.strip()
        if not name or name.casefold() in have:
            continue
        db.add(ChecklistGroup(checklist_id=checklist_id, name=name, sort_order=position))
        have.add(name.casefold())
        position += 1
    db.commit()
    return logic.ordered_groups(db, checklist_id)


@router.put("/{checklist_id}/groups/{group_id}", response_model=ChecklistGroupResponse)
def update_group(
    checklist_id: int,
    group_id: int,
    payload: ChecklistGroupUpdate,
    db: Session = Depends(get_db),
):
    """Rename a group. ``409`` when another group already has the name."""
    group = _get_group(db, checklist_id, group_id)
    if _group_name_taken(db, checklist_id, payload.name, exclude_id=group.id):
        raise HTTPException(status_code=409, detail="This checklist already has that group")
    group.name = payload.name
    db.commit()
    db.refresh(group)
    return group


@router.delete("/{checklist_id}/groups/{group_id}", status_code=204)
def delete_group(checklist_id: int, group_id: int, db: Session = Depends(get_db)):
    """Delete a group. Its items move to General rather than going with it.

    They go to the bottom of General, in the order they had, so a group
    deleted by mistake leaves its items together and findable.
    """
    group = _get_group(db, checklist_id, group_id)
    position = _bottom_of_group(db, checklist_id, None)
    moving = (
        db.query(ChecklistItem)
        .filter(ChecklistItem.checklist_id == checklist_id, ChecklistItem.group_id == group.id)
        .order_by(ChecklistItem.sort_order.asc(), ChecklistItem.id.asc())
        .all()
    )
    for offset, item in enumerate(moving):
        item.group_id = None
        item.sort_order = position + offset
    db.delete(group)
    db.commit()
    return Response(status_code=204)


# --- Items -------------------------------------------------------------------------


@router.post("/{checklist_id}/items", response_model=ChecklistItemResponse, status_code=201)
def create_item(checklist_id: int, payload: ChecklistItemCreate, db: Session = Depends(get_db)):
    """Add an item to the bottom of its group. It is unchecked on every day."""
    _get_checklist(db, checklist_id)
    _require_group(db, checklist_id, payload.group_id)
    item = ChecklistItem(
        checklist_id=checklist_id,
        group_id=payload.group_id,
        name=payload.name,
        note=payload.note,
        sort_order=_bottom_of_group(db, checklist_id, payload.group_id),
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


@router.put("/{checklist_id}/items/{item_id}", response_model=ChecklistItemResponse)
def update_item(
    checklist_id: int,
    item_id: int,
    payload: ChecklistItemUpdate,
    db: Session = Depends(get_db),
):
    """Partial update. A changed group puts the item at the bottom of the new one.

    Checks are untouched: the item is the same item on every day, whatever it
    is now called or wherever it is now filed.
    """
    item = _get_item(db, checklist_id, item_id)
    if payload.name is not None:
        item.name = payload.name
    if payload.note is not UNSET:
        item.note = payload.note
    if payload.group_id is not UNSET and payload.group_id != item.group_id:
        _require_group(db, checklist_id, payload.group_id)
        item.group_id = payload.group_id
        item.sort_order = _bottom_of_group(db, checklist_id, payload.group_id)
    db.commit()
    db.refresh(item)
    return item


@router.delete("/{checklist_id}/items/{item_id}", status_code=204)
def delete_item(checklist_id: int, item_id: int, db: Session = Depends(get_db)):
    """Delete an item from the checklist, and so from every day it is on."""
    item = _get_item(db, checklist_id, item_id)
    db.query(ChecklistDayCheck).filter(ChecklistDayCheck.item_id == item.id).delete(
        synchronize_session=False
    )
    db.delete(item)
    db.commit()
    return Response(status_code=204)


@router.post("/{checklist_id}/items/reorder", response_model=list[ChecklistItemResponse])
def reorder_items(checklist_id: int, payload: ChecklistReorder, db: Session = Depends(get_db)):
    """Rewrite one group's order, moving in any item that isn't there yet.

    The same contract as ``POST /api/shopping/items/reorder``: every listed
    item is filed under ``group_id`` and numbered by its index, so a drag into
    another group is one request and cannot half-apply. Duplicate ids keep
    their first mention. An id that is not one of this checklist's items is a
    ``404`` and nothing changes. The group an item left is not renumbered,
    since positions are only ever compared.
    """
    _get_checklist(db, checklist_id)
    _require_group(db, checklist_id, payload.group_id)

    ordered_ids: list[int] = []
    for item_id in payload.item_ids:
        if item_id not in ordered_ids:
            ordered_ids.append(item_id)

    items = {
        i.id: i
        for i in db.query(ChecklistItem)
        .filter(ChecklistItem.checklist_id == checklist_id, ChecklistItem.id.in_(ordered_ids))
        .all()
    }
    missing = [i for i in ordered_ids if i not in items]
    if missing:
        raise HTTPException(status_code=404, detail=f"Unknown item ids: {missing}")

    for position, item_id in enumerate(ordered_ids):
        items[item_id].group_id = payload.group_id
        items[item_id].sort_order = position
    db.commit()
    return [items[i] for i in ordered_ids]


# --- Days ----------------------------------------------------------------------------


@days_router.get("", response_model=list[ChecklistDaySummary])
def list_days(
    when: str = Query("upcoming", pattern="^(upcoming|past)$"),
    limit: int | None = Query(None, ge=1, le=200),
    db: Session = Depends(get_db),
):
    """Days' checklists. ``upcoming`` is today on, soonest first; ``past`` is
    before today, newest first, ten by default."""
    today = today_local_str(db)
    query = db.query(ChecklistDay)
    if when == "upcoming":
        query = query.filter(ChecklistDay.date >= today).order_by(
            ChecklistDay.date.asc(), ChecklistDay.id.asc()
        )
    else:
        query = query.filter(ChecklistDay.date < today).order_by(
            ChecklistDay.date.desc(), ChecklistDay.id.desc()
        )
        limit = limit or PAST_DAYS_DEFAULT_LIMIT
    if limit:
        query = query.limit(limit)
    return _day_summaries(db, query.all())


@days_router.post("", response_model=ChecklistDayResponse, status_code=201)
def create_day(payload: ChecklistDayCreate, db: Session = Depends(get_db)):
    """Put a checklist on a day, starting with nothing checked.

    A day before today is a ``422``: nobody packs for yesterday. A checklist
    already on that day is a ``409`` carrying the existing day's id, so the
    page can open it instead — the same shape Notes uses.
    """
    checklist = db.query(Checklist).filter(Checklist.id == payload.checklist_id).first()
    if not checklist:
        raise HTTPException(status_code=422, detail="Unknown checklist_id")
    if payload.date < today_local_str(db):
        raise HTTPException(status_code=422, detail="Pick today or a later day")
    _date_clash(db, checklist.id, payload.date)
    day = ChecklistDay(checklist_id=checklist.id, date=payload.date, label=payload.label)
    db.add(day)
    db.commit()
    db.refresh(day)
    return _day_response(db, day)


@days_router.get("/{day_id}", response_model=ChecklistDayResponse)
def get_day(day_id: int, db: Session = Depends(get_db)):
    """A day's checklist: the checklist's groups and items, each with its check."""
    return _day_response(db, _get_day(db, day_id))


@days_router.put("/{day_id}", response_model=ChecklistDayResponse)
def update_day(day_id: int, payload: ChecklistDayUpdate, db: Session = Depends(get_db)):
    """Move a day's checklist to another date, or change its label. Checks stay."""
    day = _get_day(db, day_id)
    if payload.date is not None and payload.date != day.date:
        if payload.date < today_local_str(db):
            raise HTTPException(status_code=422, detail="Pick today or a later day")
        _date_clash(db, day.checklist_id, payload.date, exclude_id=day.id)
        day.date = payload.date
    if payload.label is not UNSET:
        day.label = payload.label
    db.commit()
    db.refresh(day)
    return _day_response(db, day)


@days_router.delete("/{day_id}", status_code=204)
def delete_day(day_id: int, db: Session = Depends(get_db)):
    """Take a checklist off a day. The checklist itself is untouched."""
    day = _get_day(db, day_id)
    db.query(ChecklistDayCheck).filter(ChecklistDayCheck.day_id == day.id).delete(
        synchronize_session=False
    )
    db.delete(day)
    db.commit()
    return Response(status_code=204)


@days_router.put("/{day_id}/items/{item_id}", response_model=ChecklistDayResponse)
def set_check(day_id: int, item_id: int, payload: ChecklistCheck, db: Session = Depends(get_db)):
    """Check or uncheck one item on one day. Idempotent.

    Returns the whole day so the caller's progress line comes from the server
    rather than from counting checkboxes on the page.
    """
    day = _get_day(db, day_id)
    _get_item(db, day.checklist_id, item_id)
    existing = (
        db.query(ChecklistDayCheck)
        .filter(ChecklistDayCheck.day_id == day.id, ChecklistDayCheck.item_id == item_id)
        .first()
    )
    if payload.checked and not existing:
        db.add(ChecklistDayCheck(day_id=day.id, item_id=item_id))
    elif not payload.checked and existing:
        db.delete(existing)
    db.commit()
    return _day_response(db, day)


@days_router.post("/{day_id}/reset", response_model=ChecklistDayResponse)
def reset_day(day_id: int, db: Session = Depends(get_db)):
    """Uncheck everything on one day."""
    day = _get_day(db, day_id)
    db.query(ChecklistDayCheck).filter(ChecklistDayCheck.day_id == day.id).delete(
        synchronize_session=False
    )
    db.commit()
    return _day_response(db, day)
