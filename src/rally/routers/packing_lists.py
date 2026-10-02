"""Packing lists router for Rally.

Several prefixes, because they are different kinds of thing:

* ``/api/packing-lists`` is the packing list template: its details and its items.
  Every edit here reaches every day the template is on (see
  ``rally.packing_lists`` for why that needs no sync).
* ``/api/packing-list-bags`` is the household's bags, shared by every packing list.
* ``/api/packing-list-items/suggestions`` is the item history behind autocomplete.
* ``/api/packing-list-days`` is a packing list put on a date, and the checks made on
  it. Nothing here writes to the packing list. A day before today is the archive:
  readable, never writable.
* ``/api/packing-list-schedules`` puts a packing list on days by a repeating rule.
  It creates ordinary days ahead of time and has no hold over them after.

They are separate prefixes rather than ``/api/packing-lists/days`` so a day's id
can never be mistaken for a packing list's, and ``suggestions`` never for an id.
"""

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import case, func
from sqlalchemy.orm import Session

from rally import packing_lists as logic
from rally.database import get_db
from rally.models import (
    FamilyMember,
    PackingList,
    PackingListBag,
    PackingListDay,
    PackingListDayCheck,
    PackingListDayItem,
    PackingListItem,
    PackingListItemHistory,
    PackingListSchedule,
)
from rally.schemas import (
    UNSET,
    ArchivePage,
    PackingListBagCreate,
    PackingListBagResponse,
    PackingListBagUpdate,
    PackingListCreate,
    PackingListDayCreate,
    PackingListDayItemCreate,
    PackingListDayItemResponse,
    PackingListDayItemUpdate,
    PackingListDayResponse,
    PackingListDayUpdate,
    PackingListItemCreate,
    PackingListItemResponse,
    PackingListItemUpdate,
    PackingListReorder,
    PackingListResponse,
    PackingListScheduleCreate,
    PackingListScheduleResponse,
    PackingListScheduleUpdate,
    PackingListSuggestion,
    PackingListSummary,
    PackingListUpdate,
    check_date_range,
    check_recurrence_rule,
)
from rally.utils.settings import local_timezone_name, today_local_str
from rally.utils.timezone import today_local

router = APIRouter(prefix="/api/packing-lists", tags=["packing_lists"])
days_router = APIRouter(prefix="/api/packing-list-days", tags=["packing_lists"])
schedules_router = APIRouter(prefix="/api/packing-list-schedules", tags=["packing_lists"])
bags_router = APIRouter(prefix="/api/packing-list-bags", tags=["packing_lists"])
items_router = APIRouter(prefix="/api/packing-list-items", tags=["packing_lists"])

# Autocomplete returns a handful; the cap keeps a long history from flooding it.
DEFAULT_SUGGESTIONS = 8
MAX_SUGGESTIONS = 25


# --- Helpers -------------------------------------------------------------------


def _get_packing_list(db: Session, packing_list_id: int) -> PackingList:
    packing_list = db.query(PackingList).filter(PackingList.id == packing_list_id).first()
    if not packing_list:
        raise HTTPException(status_code=404, detail="Packing list not found")
    return packing_list


def _get_item(db: Session, packing_list_id: int, item_id: int) -> PackingListItem:
    item = (
        db.query(PackingListItem)
        .filter(PackingListItem.id == item_id, PackingListItem.packing_list_id == packing_list_id)
        .first()
    )
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    return item


def _get_day(db: Session, day_id: int) -> PackingListDay:
    day = db.query(PackingListDay).filter(PackingListDay.id == day_id).first()
    if not day:
        raise HTTPException(status_code=404, detail="Packing list day not found")
    return day


def _require_owner(db: Session, owner_id: int | None) -> None:
    """An owner must be a family member who exists; ``None`` is Everyone."""
    if owner_id is None:
        return
    if not db.query(FamilyMember.id).filter(FamilyMember.id == owner_id).first():
        raise HTTPException(status_code=422, detail="Unknown owner_id")


def _bag_id(db: Session, *, bag: str | None, bag_id: int | None) -> int | None:
    """The bag an item names, by name (made if new) or by id (must exist)."""
    if bag_id is not None:
        if not db.query(PackingListBag.id).filter(PackingListBag.id == bag_id).first():
            raise HTTPException(status_code=422, detail="Unknown bag_id")
        return bag_id
    found = logic.bag_named(db, bag)
    return found.id if found else None


def _apply_owner_and_bag(db: Session, target, payload) -> None:
    """Set ``owner_id`` and the bag on an item, a day's reading of one, or a
    day's own item, from a partial update. Fields left out stay as they are."""
    if payload.owner_id is not UNSET:
        _require_owner(db, payload.owner_id)
        target.owner_id = payload.owner_id
    if payload.bag is not UNSET:
        target.bag_id = _bag_id(db, bag=payload.bag, bag_id=None)
    elif payload.bag_id is not UNSET:
        target.bag_id = _bag_id(db, bag=None, bag_id=payload.bag_id)


def _name_taken(db: Session, name: str, *, exclude_id: int | None = None) -> bool:
    query = db.query(PackingList.id).filter(func.lower(PackingList.name) == name.lower())
    if exclude_id is not None:
        query = query.filter(PackingList.id != exclude_id)
    return query.first() is not None


def _summaries(db: Session, rows: list[PackingList]) -> list[PackingListSummary]:
    ids = [c.id for c in rows]
    items = logic.item_counts(db, ids)
    days = dict.fromkeys(ids, 0)
    upcoming = dict.fromkeys(ids, 0)
    if ids:
        days.update(
            db.query(PackingListDay.packing_list_id, func.count(PackingListDay.id))
            .filter(PackingListDay.packing_list_id.in_(ids))
            .group_by(PackingListDay.packing_list_id)
            .all()
        )
        upcoming.update(
            db.query(PackingListDay.packing_list_id, func.count(PackingListDay.id))
            .filter(
                PackingListDay.packing_list_id.in_(ids), PackingListDay.date >= today_local_str(db)
            )
            .group_by(PackingListDay.packing_list_id)
            .all()
        )
    return [
        PackingListSummary(
            id=c.id,
            name=c.name,
            description=c.description,
            pack_days_before=c.pack_days_before,
            item_count=items[c.id],
            day_count=days[c.id],
            upcoming_days=upcoming[c.id],
        )
        for c in rows
    ]


def _packing_list_responses(db: Session, rows: list[PackingList]) -> list[PackingListResponse]:
    """Packing lists whole: items, in order, and the schedule each repeats on."""
    schedules = {
        s.packing_list_id: s
        for s in db.query(PackingListSchedule).filter(
            PackingListSchedule.packing_list_id.in_([c.id for c in rows])
        )
    }
    responses = []
    for packing_list, summary in zip(rows, _summaries(db, rows), strict=True):
        items = logic.ordered_items(db, packing_list.id)
        schedule = schedules.get(packing_list.id)
        responses.append(
            PackingListResponse(
                **summary.model_dump(),
                items=[PackingListItemResponse.model_validate(i) for i in items],
                schedule=_schedule_response(schedule, packing_list.name) if schedule else None,
            )
        )
    return responses


def _packing_list_response(db: Session, packing_list: PackingList) -> PackingListResponse:
    return _packing_list_responses(db, [packing_list])[0]


def _day_responses(db: Session, rows: list[PackingListDay]) -> list[PackingListDayResponse]:
    """Days with their items as each day has them, every item with its check.

    The counts come from the same resolved list the rows do
    (``logic.resolve_day``), so "3 of 13 packed" always describes what is on
    screen, a day's own additions and removals included. A packing list on several
    days — the backpack, every weekday — is read once.
    """
    packing_lists = {
        c.id: c
        for c in db.query(PackingList)
        .filter(PackingList.id.in_({d.packing_list_id for d in rows}))
        .all()
    }
    contents: dict[int, list[PackingListItem]] = {}
    responses = []
    for day in rows:
        packing_list = packing_lists.get(day.packing_list_id)
        if packing_list is None:
            # A day whose packing list is gone has nothing to show. Deleting a
            # packing list removes its days, so this is a guard, not a path.
            continue
        if packing_list.id not in contents:
            contents[packing_list.id] = logic.ordered_items(db, packing_list.id)
        items = contents[packing_list.id]
        resolved = logic.resolve_day(db, day, items)
        responses.append(
            PackingListDayResponse(
                id=day.id,
                packing_list_id=packing_list.id,
                packing_list_name=packing_list.name,
                date=day.date,
                label=day.label,
                packing_list_description=packing_list.description,
                pack_days_before=logic.pack_days_before(day, packing_list),
                pack_date=logic.pack_date(day.date, logic.pack_days_before(day, packing_list)),
                schedule_id=day.schedule_id,
                total=len(resolved),
                checked=sum(1 for i in resolved if i.checked),
                changed_count=logic.change_count(db, day, items),
                items=[PackingListDayItemResponse(**vars(i)) for i in resolved],
            )
        )
    return responses


def _day_response(db: Session, day: PackingListDay) -> PackingListDayResponse:
    responses = _day_responses(db, [day])
    if not responses:
        raise HTTPException(status_code=404, detail="Packing list not found")
    return responses[0]


def _require_current(db: Session, day: PackingListDay) -> None:
    """Refuse a write to a day before today: past days are the archive.

    Enforced here and not only in the UI, the same promise Notes makes: the
    archive page offers no controls, but the endpoint is reachable regardless.
    """
    if day.date < today_local_str(db):
        raise HTTPException(status_code=403, detail="Packing lists for past days are read-only")


def _date_clash(db: Session, packing_list_id: int, day_date: str, *, exclude_id: int | None = None):
    query = db.query(PackingListDay).filter(
        PackingListDay.packing_list_id == packing_list_id, PackingListDay.date == day_date
    )
    if exclude_id is not None:
        query = query.filter(PackingListDay.id != exclude_id)
    clash = query.first()
    if clash:
        raise HTTPException(
            status_code=409,
            detail={"message": f"This packing list is already on {day_date}", "id": clash.id},
        )


# --- Packing Lists ------------------------------------------------------------------


@router.get("", response_model=list[PackingListResponse])
def list_packing_lists(db: Session = Depends(get_db)):
    """Every packing list, by name, whole: its counts, items and schedule.

    Whole because the Packing Lists page edits each one in place, under its row.
    """
    rows = db.query(PackingList).order_by(func.lower(PackingList.name).asc()).all()
    return _packing_list_responses(db, rows)


@router.post("", response_model=PackingListResponse, status_code=201)
def create_packing_list(payload: PackingListCreate, db: Session = Depends(get_db)):
    """Create a packing list. ``409`` when the name is taken, ignoring case."""
    if _name_taken(db, payload.name):
        raise HTTPException(status_code=409, detail="A packing list with that name already exists")
    packing_list = PackingList(
        name=payload.name,
        description=payload.description,
        pack_days_before=payload.pack_days_before,
    )
    db.add(packing_list)
    db.commit()
    db.refresh(packing_list)
    return _packing_list_response(db, packing_list)


@router.get("/{packing_list_id}", response_model=PackingListResponse)
def get_packing_list(packing_list_id: int, db: Session = Depends(get_db)):
    """A packing list with its items, in order."""
    return _packing_list_response(db, _get_packing_list(db, packing_list_id))


@router.put("/{packing_list_id}", response_model=PackingListResponse)
def update_packing_list(
    packing_list_id: int, payload: PackingListUpdate, db: Session = Depends(get_db)
):
    """Rename a packing list, or change its description or when it is packed."""
    packing_list = _get_packing_list(db, packing_list_id)
    if payload.name is not None and payload.name != packing_list.name:
        if _name_taken(db, payload.name, exclude_id=packing_list.id):
            raise HTTPException(
                status_code=409, detail="A packing list with that name already exists"
            )
        packing_list.name = payload.name
    if payload.description is not UNSET:
        packing_list.description = payload.description
    if payload.pack_days_before is not None:
        packing_list.pack_days_before = payload.pack_days_before
    db.commit()
    db.refresh(packing_list)
    return _packing_list_response(db, packing_list)


@router.delete("/{packing_list_id}", status_code=204)
def delete_packing_list(packing_list_id: int, db: Session = Depends(get_db)):
    """Delete a packing list, its items and schedule, every day it is on and their checks."""
    logic.delete_packing_list(db, _get_packing_list(db, packing_list_id))
    db.commit()
    return Response(status_code=204)


# --- Items -------------------------------------------------------------------------


@router.post("/{packing_list_id}/items", response_model=PackingListItemResponse, status_code=201)
def create_item(
    packing_list_id: int, payload: PackingListItemCreate, db: Session = Depends(get_db)
):
    """Add an item to the bottom of the template. It is unchecked on every day.

    A bag named for the first time joins the household's bags, and the name
    joins the item history behind autocomplete.
    """
    _get_packing_list(db, packing_list_id)
    _require_owner(db, payload.owner_id)
    item = PackingListItem(
        packing_list_id=packing_list_id,
        owner_id=payload.owner_id,
        bag_id=_bag_id(db, bag=payload.bag, bag_id=payload.bag_id),
        name=payload.name,
        note=payload.note,
        sort_order=logic.bottom_position(db, packing_list_id),
    )
    db.add(item)
    logic.record_item_history(db, item.name, item.owner_id, item.bag_id)
    db.commit()
    db.refresh(item)
    return item


@router.put("/{packing_list_id}/items/{item_id}", response_model=PackingListItemResponse)
def update_item(
    packing_list_id: int,
    item_id: int,
    payload: PackingListItemUpdate,
    db: Session = Depends(get_db),
):
    """Partial update: name, note, owner, bag. The item keeps its place.

    Checks are untouched: the item is the same item on every day, whatever it
    is now called or whoever now owns it. History records adds, not edits.
    """
    item = _get_item(db, packing_list_id, item_id)
    if payload.name is not None:
        item.name = payload.name
    if payload.note is not UNSET:
        item.note = payload.note
    _apply_owner_and_bag(db, item, payload)
    db.commit()
    db.refresh(item)
    return item


@router.delete("/{packing_list_id}/items/{item_id}", status_code=204)
def delete_item(packing_list_id: int, item_id: int, db: Session = Depends(get_db)):
    """Delete an item from the template, and so from every day it is on.

    A day that had edited it loses its edit too: there is no longer a template
    item for it to be a reading of. History keeps the name.
    """
    item = _get_item(db, packing_list_id, item_id)
    for model in (PackingListDayCheck, PackingListDayItem):
        db.query(model).filter(model.item_id == item.id).delete(synchronize_session=False)
    db.delete(item)
    db.commit()
    return Response(status_code=204)


@router.post("/{packing_list_id}/items/reorder", response_model=list[PackingListItemResponse])
def reorder_items(packing_list_id: int, payload: PackingListReorder, db: Session = Depends(get_db)):
    """One group, in one view, as it should now read — and who or what it is.

    Every listed item takes the group's owner (``view: owner``) or bag
    (``view: bag``), so a drag into another group is one request and cannot
    half-apply. The template keeps one order: the listed items are dealt back
    into the positions they held between them, in the new sequence, so items
    in other groups keep their places. Duplicate ids keep their first mention;
    an id not on this template is a ``404`` and nothing changes; an unknown
    owner or bag is a ``422``.
    """
    _get_packing_list(db, packing_list_id)
    if payload.view == "owner":
        _require_owner(db, payload.key)
    elif payload.key is not None:
        _bag_id(db, bag=None, bag_id=payload.key)

    ordered_ids: list[int] = []
    for item_id in payload.item_ids:
        if item_id not in ordered_ids:
            ordered_ids.append(item_id)

    items = {
        i.id: i
        for i in db.query(PackingListItem)
        .filter(
            PackingListItem.packing_list_id == packing_list_id, PackingListItem.id.in_(ordered_ids)
        )
        .all()
    }
    missing = [i for i in ordered_ids if i not in items]
    if missing:
        raise HTTPException(status_code=404, detail=f"Unknown item ids: {missing}")

    slots = sorted(items[i].sort_order for i in ordered_ids)
    for slot, item_id in zip(slots, ordered_ids, strict=True):
        item = items[item_id]
        item.sort_order = slot
        if payload.view == "owner":
            item.owner_id = payload.key
        else:
            item.bag_id = payload.key
    db.commit()
    return [items[i] for i in ordered_ids]


# --- Bags ----------------------------------------------------------------------------


def _get_bag(db: Session, bag_id: int) -> PackingListBag:
    bag = db.query(PackingListBag).filter(PackingListBag.id == bag_id).first()
    if not bag:
        raise HTTPException(status_code=404, detail="Bag not found")
    return bag


def _bag_name_taken(db: Session, name: str, *, exclude_id: int | None = None) -> bool:
    query = db.query(PackingListBag.id).filter(func.lower(PackingListBag.name) == name.lower())
    if exclude_id is not None:
        query = query.filter(PackingListBag.id != exclude_id)
    return query.first() is not None


def _bag_responses(db: Session, bags: list[PackingListBag]) -> list[PackingListBagResponse]:
    counts = dict(
        db.query(PackingListItem.bag_id, func.count(PackingListItem.id))
        .filter(PackingListItem.bag_id.isnot(None))
        .group_by(PackingListItem.bag_id)
        .all()
    )
    return [
        PackingListBagResponse(id=b.id, name=b.name, item_count=counts.get(b.id, 0)) for b in bags
    ]


@bags_router.get("", response_model=list[PackingListBagResponse])
def list_bags(db: Session = Depends(get_db)):
    """The household's bags, A to Z, each with how many template items it holds."""
    return _bag_responses(db, logic.ordered_bags(db))


@bags_router.post("", response_model=PackingListBagResponse, status_code=201)
def create_bag(payload: PackingListBagCreate, db: Session = Depends(get_db)):
    """Add a bag. ``409`` when the name is taken, ignoring case."""
    if _bag_name_taken(db, payload.name):
        raise HTTPException(status_code=409, detail="A bag with that name already exists")
    bag = PackingListBag(name=payload.name)
    db.add(bag)
    db.commit()
    db.refresh(bag)
    return _bag_responses(db, [bag])[0]


@bags_router.put("/{bag_id}", response_model=PackingListBagResponse)
def rename_bag(bag_id: int, payload: PackingListBagUpdate, db: Session = Depends(get_db)):
    """Rename a bag, everywhere it is used. ``409`` on another bag's name."""
    bag = _get_bag(db, bag_id)
    if _bag_name_taken(db, payload.name, exclude_id=bag.id):
        raise HTTPException(status_code=409, detail="A bag with that name already exists")
    bag.name = payload.name
    db.commit()
    db.refresh(bag)
    return _bag_responses(db, [bag])[0]


@bags_router.delete("/{bag_id}", status_code=204)
def delete_bag(bag_id: int, db: Session = Depends(get_db)):
    """Delete a bag. Whatever was in it goes to No bag rather than going with it.

    SQLite does not enforce the reference, so the items are moved by hand —
    on every template, on every day's own items and readings, and in history —
    the same reason deleting a shopping store moves its items to Anywhere.
    """
    bag = _get_bag(db, bag_id)
    for model in (PackingListItem, PackingListDayItem, PackingListItemHistory):
        db.query(model).filter(model.bag_id == bag.id).update(
            {model.bag_id: None}, synchronize_session=False
        )
    db.delete(bag)
    db.commit()
    return Response(status_code=204)


# --- Item history ----------------------------------------------------------------------


def _escape_like(term: str) -> str:
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


@items_router.get("/suggestions", response_model=list[PackingListSuggestion])
def list_suggestions(
    q: str | None = Query(None, description="Substring to match anywhere in the item name"),
    limit: int = Query(DEFAULT_SUGGESTIONS, ge=1),
    db: Session = Depends(get_db),
):
    """Autocomplete from every item name ever used, the shopping list's rules.

    Substring, not prefix, so ``towel`` finds "Beach towel"; prefix matches
    rank first, then by use count, then by how recently used. An empty ``q``
    returns the most used. Each suggestion carries the owner and bag it last
    had, so the form can fill them in when nobody has chosen yet.
    """
    limit = min(limit, MAX_SUGGESTIONS)
    query = db.query(PackingListItemHistory)
    ranking = (
        PackingListItemHistory.times_added.desc(),
        PackingListItemHistory.last_added_at.desc(),
        PackingListItemHistory.name.asc(),
    )
    term = (q or "").strip()
    if term:
        escaped = _escape_like(term.lower())
        lowered = func.lower(PackingListItemHistory.name)
        query = query.filter(lowered.like(f"%{escaped}%", escape="\\"))
        prefix_first = case((lowered.like(f"{escaped}%", escape="\\"), 0), else_=1)
        query = query.order_by(prefix_first, *ranking)
    else:
        query = query.order_by(*ranking)

    bags = {b.id: b.name for b in logic.ordered_bags(db)}
    return [
        PackingListSuggestion(
            id=row.id,
            name=row.name,
            owner_id=row.owner_id,
            bag_id=row.bag_id if row.bag_id in bags else None,
            bag_name=bags.get(row.bag_id),
            times_added=row.times_added,
        )
        for row in query.limit(limit).all()
    ]


@items_router.delete("/suggestions/{history_id}", status_code=204)
def delete_suggestion(history_id: int, db: Session = Depends(get_db)):
    """Forget a suggestion: a typo would otherwise be offered forever.
    Packing lists are left alone."""
    row = db.query(PackingListItemHistory).filter(PackingListItemHistory.id == history_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Suggestion not found")
    db.delete(row)
    db.commit()
    return Response(status_code=204)


# --- Days ----------------------------------------------------------------------------


@days_router.get("", response_model=list[PackingListDayResponse])
def list_days(db: Session = Depends(get_db)):
    """Every day's packing list from today on, soonest first, with its items.

    Puts every active schedule's packing list on its coming days first, the same
    arrangement ``GET /api/todos`` has with recurring tasks: this is what the
    Packing Lists page loads, so a schedule's days are there whenever anybody
    looks.
    """
    logic.process_schedules(db, today_local(local_timezone_name(db)))
    today = today_local_str(db)
    rows = (
        db.query(PackingListDay)
        .filter(PackingListDay.date >= today)
        .order_by(PackingListDay.date.asc(), PackingListDay.id.asc())
        .all()
    )
    return _day_responses(db, rows)


@days_router.get("/previous", response_model=ArchivePage[PackingListDayResponse])
def list_previous_days(
    search: str | None = Query(
        None,
        description="Case-insensitive keyword matched against the packing list's name or label.",
    ),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """Days before today, newest first: the archive.

    The exact complement of the default listing, on the same
    ``today_local_str`` boundary. Searching and paging are server-side, and
    ``total`` counts every match, the same contract as previous notes.
    """
    today = today_local_str(db)
    query = (
        db.query(PackingListDay)
        .join(PackingList, PackingList.id == PackingListDay.packing_list_id)
        .filter(PackingListDay.date < today)
    )
    if search and search.strip():
        pattern = f"%{search.strip()}%"
        query = query.filter(PackingList.name.ilike(pattern) | PackingListDay.label.ilike(pattern))

    total = query.count()
    # One extra row answers "is there another page" without a second count.
    rows = (
        query.order_by(PackingListDay.date.desc(), PackingListDay.id.desc())
        .offset(offset)
        .limit(limit + 1)
        .all()
    )
    return ArchivePage[PackingListDayResponse](
        items=_day_responses(db, rows[:limit]), has_more=len(rows) > limit, total=total
    )


@days_router.post("", response_model=PackingListDayResponse, status_code=201)
def create_day(payload: PackingListDayCreate, db: Session = Depends(get_db)):
    """Put a packing list on a day, starting with nothing checked.

    A day before today is a ``422``: nobody packs for yesterday. A packing list
    already on that day is a ``409`` carrying the existing day's id, so the
    page can open it instead — the same shape Notes uses.
    """
    packing_list = db.query(PackingList).filter(PackingList.id == payload.packing_list_id).first()
    if not packing_list:
        raise HTTPException(status_code=422, detail="Unknown packing_list_id")
    if payload.date < today_local_str(db):
        raise HTTPException(status_code=422, detail="Pick today or a later day")
    _date_clash(db, packing_list.id, payload.date)
    day = PackingListDay(packing_list_id=packing_list.id, date=payload.date, label=payload.label)
    db.add(day)
    db.commit()
    db.refresh(day)
    return _day_response(db, day)


@days_router.get("/{day_id}", response_model=PackingListDayResponse)
def get_day(day_id: int, db: Session = Depends(get_db)):
    """A day's packing list: its items as this day has them, each with its check."""
    return _day_response(db, _get_day(db, day_id))


@days_router.put("/{day_id}", response_model=PackingListDayResponse)
def update_day(day_id: int, payload: PackingListDayUpdate, db: Session = Depends(get_db)):
    """Move a day's packing list to another date, relabel it, or give it its own
    lead time (``pack_days_before``; ``null`` follows the template again).
    Checks stay.

    A label set here is the day's own from then on: a schedule's relabel
    passes over it.
    """
    day = _get_day(db, day_id)
    _require_current(db, day)
    if payload.date is not None and payload.date != day.date:
        if payload.date < today_local_str(db):
            raise HTTPException(status_code=422, detail="Pick today or a later day")
        _date_clash(db, day.packing_list_id, payload.date, exclude_id=day.id)
        day.date = payload.date
    if payload.label is not UNSET:
        day.label = payload.label
        day.label_edited = True
    if payload.pack_days_before is not UNSET:
        day.pack_days_before = payload.pack_days_before
    db.commit()
    db.refresh(day)
    return _day_response(db, day)


@days_router.delete("/{day_id}", status_code=204)
def delete_day(day_id: int, db: Session = Depends(get_db)):
    """Take a packing list off a day. The packing list itself is untouched.

    A day a schedule made stays off: the schedule never goes back over a date
    it has already handled.
    """
    day = _get_day(db, day_id)
    _require_current(db, day)
    for model in (PackingListDayCheck, PackingListDayItem):
        db.query(model).filter(model.day_id == day.id).delete(synchronize_session=False)
    db.delete(day)
    db.commit()
    return Response(status_code=204)


def _day_change(db: Session, day: PackingListDay, item: PackingListItem) -> PackingListDayItem:
    """This day's reading of a packing list item, created from the item if new.

    A new reading copies the item whole — name, note, owner and bag — so from
    then on the day keeps all four, whichever one it set out to change.
    """
    change = (
        db.query(PackingListDayItem)
        .filter(PackingListDayItem.day_id == day.id, PackingListDayItem.item_id == item.id)
        .first()
    )
    if change is None:
        change = PackingListDayItem(
            day_id=day.id,
            item_id=item.id,
            name=item.name,
            note=item.note,
            owner_id=item.owner_id,
            bag_id=item.bag_id,
        )
        db.add(change)
    return change


def _edits(payload) -> bool:
    """Whether a day item update changes the item, not only its check."""
    return payload.name is not None or any(
        getattr(payload, field) is not UNSET for field in ("note", "owner_id", "bag", "bag_id")
    )


@days_router.put("/{day_id}/items/{item_id}", response_model=PackingListDayResponse)
def update_day_item(
    day_id: int, item_id: int, payload: PackingListDayItemUpdate, db: Session = Depends(get_db)
):
    """Check, uncheck or edit a template item on one day. Idempotent.

    An edit (``name``, ``note``, owner, bag) is this day's only: the template
    keeps its own, and from then on a change to that item on the template no
    longer reaches this day. Returns the whole day so the caller's progress
    line comes from the server rather than from counting checkboxes.
    """
    day = _get_day(db, day_id)
    _require_current(db, day)
    item = _get_item(db, day.packing_list_id, item_id)
    if payload.checked is not None:
        existing = (
            db.query(PackingListDayCheck)
            .filter(PackingListDayCheck.day_id == day.id, PackingListDayCheck.item_id == item_id)
            .first()
        )
        if payload.checked and not existing:
            db.add(PackingListDayCheck(day_id=day.id, item_id=item_id))
        elif not payload.checked and existing:
            db.delete(existing)
    if _edits(payload):
        change = _day_change(db, day, item)
        if payload.name is not None:
            change.name = payload.name
        if payload.note is not UNSET:
            change.note = payload.note
        _apply_owner_and_bag(db, change, payload)
    db.commit()
    return _day_response(db, day)


@days_router.delete("/{day_id}/items/{item_id}", response_model=PackingListDayResponse)
def remove_day_item(day_id: int, item_id: int, db: Session = Depends(get_db)):
    """Take a packing list item off one day. The packing list, and every other day, keep it."""
    day = _get_day(db, day_id)
    _require_current(db, day)
    item = _get_item(db, day.packing_list_id, item_id)
    _day_change(db, day, item).removed = True
    db.query(PackingListDayCheck).filter(
        PackingListDayCheck.day_id == day.id, PackingListDayCheck.item_id == item.id
    ).delete(synchronize_session=False)
    db.commit()
    return _day_response(db, day)


def _get_own_item(db: Session, day: PackingListDay, own_id: int) -> PackingListDayItem:
    own = (
        db.query(PackingListDayItem)
        .filter(
            PackingListDayItem.id == own_id,
            PackingListDayItem.day_id == day.id,
            PackingListDayItem.item_id.is_(None),
        )
        .first()
    )
    if not own:
        raise HTTPException(status_code=404, detail="Item not found on this day")
    return own


def _bottom_of_day(db: Session, day: PackingListDay) -> int:
    """The position after every item a day has added for itself."""
    highest = (
        db.query(func.max(PackingListDayItem.sort_order))
        .filter(PackingListDayItem.day_id == day.id, PackingListDayItem.item_id.is_(None))
        .scalar()
    )
    return 0 if highest is None else highest + 1


@days_router.post("/{day_id}/day-items", response_model=PackingListDayResponse, status_code=201)
def create_own_item(day_id: int, payload: PackingListDayItemCreate, db: Session = Depends(get_db)):
    """Add an item to this day only, after everything else on it.

    Like an item added to a template, a new bag name joins the household's
    bags and the item's name joins the autocomplete history.
    """
    day = _get_day(db, day_id)
    _require_current(db, day)
    _require_owner(db, payload.owner_id)
    own = PackingListDayItem(
        day_id=day.id,
        owner_id=payload.owner_id,
        bag_id=_bag_id(db, bag=payload.bag, bag_id=payload.bag_id),
        name=payload.name,
        note=payload.note,
        sort_order=_bottom_of_day(db, day),
    )
    db.add(own)
    logic.record_item_history(db, own.name, own.owner_id, own.bag_id)
    db.commit()
    return _day_response(db, day)


@days_router.put("/{day_id}/day-items/{own_id}", response_model=PackingListDayResponse)
def update_own_item(
    day_id: int, own_id: int, payload: PackingListDayItemUpdate, db: Session = Depends(get_db)
):
    """Edit or check an item only this day has."""
    day = _get_day(db, day_id)
    _require_current(db, day)
    own = _get_own_item(db, day, own_id)
    if payload.name is not None:
        own.name = payload.name
    if payload.note is not UNSET:
        own.note = payload.note
    _apply_owner_and_bag(db, own, payload)
    if payload.checked is not None:
        own.checked = payload.checked
    db.commit()
    return _day_response(db, day)


@days_router.delete("/{day_id}/day-items/{own_id}", response_model=PackingListDayResponse)
def delete_own_item(day_id: int, own_id: int, db: Session = Depends(get_db)):
    """Delete an item only this day has."""
    day = _get_day(db, day_id)
    _require_current(db, day)
    db.delete(_get_own_item(db, day, own_id))
    db.commit()
    return _day_response(db, day)


@days_router.post("/{day_id}/reset", response_model=PackingListDayResponse)
def reset_day(day_id: int, db: Session = Depends(get_db)):
    """Uncheck everything on one day, its own items included."""
    day = _get_day(db, day_id)
    _require_current(db, day)
    db.query(PackingListDayCheck).filter(PackingListDayCheck.day_id == day.id).delete(
        synchronize_session=False
    )
    db.query(PackingListDayItem).filter(PackingListDayItem.day_id == day.id).update(
        {PackingListDayItem.checked: False}, synchronize_session=False
    )
    db.commit()
    return _day_response(db, day)


@days_router.post("/{day_id}/check-all", response_model=PackingListDayResponse)
def check_all(day_id: int, db: Session = Depends(get_db)):
    """Check everything on one day, its own items included. Idempotent.

    "Everything" is the day as it reads: an item it removed is not on it, so
    nothing is checked for it.
    """
    day = _get_day(db, day_id)
    _require_current(db, day)
    resolved = logic.resolve_day(db, day, logic.ordered_items(db, day.packing_list_id))
    for item in resolved:
        if item.source == "packing_list" and not item.checked:
            db.add(PackingListDayCheck(day_id=day.id, item_id=item.id))
    db.query(PackingListDayItem).filter(
        PackingListDayItem.day_id == day.id, PackingListDayItem.item_id.is_(None)
    ).update({PackingListDayItem.checked: True}, synchronize_session=False)
    db.commit()
    return _day_response(db, day)


# --- Schedules ---------------------------------------------------------------------


def _get_schedule(db: Session, schedule_id: int) -> PackingListSchedule:
    schedule = db.query(PackingListSchedule).filter(PackingListSchedule.id == schedule_id).first()
    if not schedule:
        raise HTTPException(status_code=404, detail="Schedule not found")
    return schedule


def _require_packing_list(db: Session, packing_list_id: int) -> PackingList:
    packing_list = db.query(PackingList).filter(PackingList.id == packing_list_id).first()
    if not packing_list:
        raise HTTPException(status_code=422, detail="Unknown packing_list_id")
    return packing_list


def _schedule_clash(db: Session, packing_list_id: int, *, exclude_id: int | None = None) -> None:
    """A packing list repeats on one schedule or none: ``409`` naming the one it has."""
    query = db.query(PackingListSchedule).filter(
        PackingListSchedule.packing_list_id == packing_list_id
    )
    if exclude_id is not None:
        query = query.filter(PackingListSchedule.id != exclude_id)
    clash = query.first()
    if clash:
        raise HTTPException(
            status_code=409,
            detail={"message": "This packing list already repeats", "id": clash.id},
        )


def _schedule_response(schedule: PackingListSchedule, name: str) -> PackingListScheduleResponse:
    return PackingListScheduleResponse(
        id=schedule.id,
        packing_list_id=schedule.packing_list_id,
        packing_list_name=name,
        recurrence_type=schedule.recurrence_type,
        recurrence_day=schedule.recurrence_day,
        custom_rule=schedule.custom_rule,
        start_date=schedule.start_date,
        end_date=schedule.end_date,
        label=schedule.label,
        active=schedule.active,
        last_generated_date=schedule.last_generated_date,
    )


@schedules_router.get("", response_model=list[PackingListScheduleResponse])
def list_schedules(db: Session = Depends(get_db)):
    """Every schedule, paused ones included, by packing list name."""
    rows = (
        db.query(PackingListSchedule, PackingList.name)
        .join(PackingList, PackingList.id == PackingListSchedule.packing_list_id)
        .order_by(func.lower(PackingList.name), PackingListSchedule.id)
        .all()
    )
    return [_schedule_response(schedule, name) for schedule, name in rows]


@schedules_router.post("", response_model=PackingListScheduleResponse, status_code=201)
def create_schedule(payload: PackingListScheduleCreate, db: Session = Depends(get_db)):
    """Start putting a packing list on days by a rule. ``422`` for an unknown packing list.

    Nothing is put on a day here; the next listing of days does it.
    """
    packing_list = _require_packing_list(db, payload.packing_list_id)
    _schedule_clash(db, packing_list.id)
    schedule = PackingListSchedule(**payload.model_dump())
    db.add(schedule)
    db.commit()
    db.refresh(schedule)
    return _schedule_response(schedule, packing_list.name)


@schedules_router.get("/{schedule_id}", response_model=PackingListScheduleResponse)
def get_schedule(schedule_id: int, db: Session = Depends(get_db)):
    schedule = _get_schedule(db, schedule_id)
    return _schedule_response(schedule, _require_packing_list(db, schedule.packing_list_id).name)


@schedules_router.put("/{schedule_id}", response_model=PackingListScheduleResponse)
def update_schedule(
    schedule_id: int, payload: PackingListScheduleUpdate, db: Session = Depends(get_db)
):
    """Edit, pause (``{"active": false}``) or resume a schedule.

    Days it has already put on the calendar keep their dates either way: a
    change to the rule only shapes days it has not reached yet. The label is
    the exception — a schedule's label names its days, so a new one relabels
    every day the schedule added from today on. Past days are the archive and
    keep theirs. The rule is re-checked against the merged schedule, so a patch
    that only changes the type still has to leave a schedule something can read.
    """
    schedule = _get_schedule(db, schedule_id)
    # Read the fields off the model rather than through ``model_dump``, which
    # would try to serialize the UNSET sentinel. ``None`` on a non-nullable
    # field means "not sent", the same as UNSET on a nullable one.
    changes = {
        field: value
        for field in PackingListScheduleUpdate.model_fields
        if (value := getattr(payload, field)) is not UNSET
        and not (value is None and field in ("packing_list_id", "recurrence_type", "active"))
    }
    merged = {
        field: changes.get(field, getattr(schedule, field))
        for field in ("recurrence_type", "recurrence_day", "custom_rule", "start_date", "end_date")
    }
    try:
        check_recurrence_rule(
            merged["recurrence_type"], merged["recurrence_day"], merged["custom_rule"]
        )
        check_date_range(merged["start_date"], merged["end_date"])
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    packing_list = _require_packing_list(
        db, changes.get("packing_list_id", schedule.packing_list_id)
    )
    _schedule_clash(db, packing_list.id, exclude_id=schedule.id)
    for field, value in changes.items():
        setattr(schedule, field, value)
    if "label" in changes:
        db.query(PackingListDay).filter(
            PackingListDay.schedule_id == schedule.id,
            PackingListDay.date >= today_local_str(db),
            PackingListDay.label_edited == False,  # noqa: E712 — a hand edit is the day's own
        ).update({PackingListDay.label: changes["label"]}, synchronize_session=False)
    db.commit()
    db.refresh(schedule)
    return _schedule_response(schedule, packing_list.name)


@schedules_router.delete("/{schedule_id}", status_code=204)
def delete_schedule(schedule_id: int, db: Session = Depends(get_db)):
    """Delete a schedule. The days it made stay, as days added by hand."""
    schedule = _get_schedule(db, schedule_id)
    db.query(PackingListDay).filter(PackingListDay.schedule_id == schedule.id).update(
        {PackingListDay.schedule_id: None}, synchronize_session=False
    )
    db.delete(schedule)
    db.commit()
    return Response(status_code=204)
