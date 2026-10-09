"""Packing lists router for Rally.

Several prefixes, because they are different kinds of thing:

* ``/api/packing-list-templates`` is the packing list template: its details
  and its items. Every edit here reaches every day the template is on (see
  ``rally.packing_lists`` for why that needs no sync).
* ``/api/packing-list-bags`` is the household's bags, shared by every
  template and every day.
* ``/api/packing-list-items/suggestions`` is the item history behind
  autocomplete.
* ``/api/packing-list-days`` is a packing list on a date — a template put on
  a day, or a day with no template — and the checks made on it. Nothing here
  writes to a template. A day before today is the archive: readable, never
  writable.
* ``/api/packing-list-template-schedules`` puts a template on days by a
  repeating rule. It creates ordinary days ahead of time and has no hold over
  them after.

They are separate prefixes rather than ``/api/packing-list-templates/days`` so
a day's id can never be mistaken for a template's, and ``suggestions`` never
for an id.
"""

from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import case, func, or_
from sqlalchemy.orm import Session

from rally import packing_lists as logic
from rally.database import get_db
from rally.models import (
    FamilyMember,
    PackingListBag,
    PackingListDay,
    PackingListDayBag,
    PackingListDayBagCheck,
    PackingListDayCheck,
    PackingListDayItem,
    PackingListItemHistory,
    PackingListTemplate,
    PackingListTemplateBag,
    PackingListTemplateItem,
    PackingListTemplateSchedule,
)
from rally.schemas import (
    UNSET,
    ArchivePage,
    PackingListBagCreate,
    PackingListBagOnListResponse,
    PackingListBagReadingUpdate,
    PackingListBagResponse,
    PackingListBagUpdate,
    PackingListDayBagUpdate,
    PackingListDayCreate,
    PackingListDayItemCreate,
    PackingListDayItemResponse,
    PackingListDayItemUpdate,
    PackingListDayReorder,
    PackingListDayResponse,
    PackingListDayResync,
    PackingListDayUpdate,
    PackingListItemCreate,
    PackingListItemUpdate,
    PackingListSuggestion,
    PackingListTemplateCreate,
    PackingListTemplateItemResponse,
    PackingListTemplateReorder,
    PackingListTemplateResponse,
    PackingListTemplateScheduleCreate,
    PackingListTemplateScheduleResponse,
    PackingListTemplateScheduleUpdate,
    PackingListTemplateSummary,
    PackingListTemplateUpdate,
    check_date_range,
    check_recurrence_rule,
)
from rally.utils.settings import local_timezone_name, today_local_str
from rally.utils.timezone import today_local

templates_router = APIRouter(prefix="/api/packing-list-templates", tags=["packing_lists"])
days_router = APIRouter(prefix="/api/packing-list-days", tags=["packing_lists"])
template_schedules_router = APIRouter(
    prefix="/api/packing-list-template-schedules", tags=["packing_lists"]
)
bags_router = APIRouter(prefix="/api/packing-list-bags", tags=["packing_lists"])
items_router = APIRouter(prefix="/api/packing-list-items", tags=["packing_lists"])

# Autocomplete returns a handful; the cap keeps a long history from flooding it.
DEFAULT_SUGGESTIONS = 8
MAX_SUGGESTIONS = 25


# --- Helpers -------------------------------------------------------------------


def _get_template(db: Session, template_id: int) -> PackingListTemplate:
    template = db.query(PackingListTemplate).filter(PackingListTemplate.id == template_id).first()
    if not template:
        raise HTTPException(status_code=404, detail="Packing list template not found")
    return template


def _get_template_item(
    db: Session, template_id: int | None, template_item_id: int
) -> PackingListTemplateItem:
    """An item on this template. A templateless day (``template_id`` is
    ``None``) has no template items, so every id is a ``404`` there."""
    item = None
    if template_id is not None:
        item = (
            db.query(PackingListTemplateItem)
            .filter(
                PackingListTemplateItem.id == template_item_id,
                PackingListTemplateItem.packing_list_template_id == template_id,
            )
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


def _bag_id(
    db: Session, *, bag: str | None, bag_id: int | None, owner_id: int | None = None
) -> int | None:
    """The bag an item names, by name or by id (which must exist).

    A name means the item owner's bag of that name, else the ownerless one,
    else a new ownerless bag (``logic.bag_named``): two people can each have a
    "Backpack", so the owner is what tells them apart.
    """
    if bag_id is not None:
        if not db.query(PackingListBag.id).filter(PackingListBag.id == bag_id).first():
            raise HTTPException(status_code=422, detail="Unknown bag_id")
        return bag_id
    found = logic.bag_named(db, bag, owner_id)
    return found.id if found else None


def _apply_owner_and_bag(db: Session, target, payload) -> None:
    """Set ``owner_id`` and the bag on an item, a day's reading of one, or a
    day's own item, from a partial update. Fields left out stay as they are.
    The owner goes first, since a bag named here is found by it."""
    if payload.owner_id is not UNSET:
        _require_owner(db, payload.owner_id)
        target.owner_id = payload.owner_id
    if payload.bag is not UNSET:
        target.bag_id = _bag_id(db, bag=payload.bag, bag_id=None, owner_id=target.owner_id)
    elif payload.bag_id is not UNSET:
        target.bag_id = _bag_id(db, bag=None, bag_id=payload.bag_id)


def _rename(db: Session, target, name: str | None) -> None:
    """Rename an item — a template's, a day's reading of one, or a day's own —
    and remember the new name for autocomplete (``logic.record_rename``)."""
    if name is None:
        return
    old = target.name
    target.name = name
    logic.record_rename(db, old, name, target.owner_id, target.bag_id)


def _validate_group_key(db: Session, view: str, key: int | None) -> None:
    """A reorder's group: an owner that exists, a bag that exists, or none."""
    if view == "owner":
        _require_owner(db, key)
    elif key is not None:
        _bag_id(db, bag=None, bag_id=key)


def _name_taken(db: Session, name: str, *, exclude_id: int | None = None) -> bool:
    query = db.query(PackingListTemplate.id).filter(
        func.lower(PackingListTemplate.name) == name.lower()
    )
    if exclude_id is not None:
        query = query.filter(PackingListTemplate.id != exclude_id)
    return query.first() is not None


def _summaries(db: Session, rows: list[PackingListTemplate]) -> list[PackingListTemplateSummary]:
    ids = [t.id for t in rows]
    items = logic.template_item_counts(db, ids)
    days = dict.fromkeys(ids, 0)
    upcoming = dict.fromkeys(ids, 0)
    if ids:
        days.update(
            db.query(PackingListDay.packing_list_template_id, func.count(PackingListDay.id))
            .filter(PackingListDay.packing_list_template_id.in_(ids))
            .group_by(PackingListDay.packing_list_template_id)
            .all()
        )
        upcoming.update(
            db.query(PackingListDay.packing_list_template_id, func.count(PackingListDay.id))
            .filter(
                PackingListDay.packing_list_template_id.in_(ids),
                PackingListDay.date >= today_local_str(db),
            )
            .group_by(PackingListDay.packing_list_template_id)
            .all()
        )
    return [
        PackingListTemplateSummary(
            id=t.id,
            name=t.name,
            description=t.description,
            pack_days_before=t.pack_days_before,
            item_count=items[t.id],
            day_count=days[t.id],
            upcoming_days=upcoming[t.id],
        )
        for t in rows
    ]


def _template_responses(
    db: Session, rows: list[PackingListTemplate]
) -> list[PackingListTemplateResponse]:
    """Templates whole: items, in order, and the schedule each repeats on."""
    schedules = {
        s.packing_list_template_id: s
        for s in db.query(PackingListTemplateSchedule).filter(
            PackingListTemplateSchedule.packing_list_template_id.in_([t.id for t in rows])
        )
    }
    responses = []
    for template, summary in zip(rows, _summaries(db, rows), strict=True):
        items = logic.ordered_template_items(db, template.id)
        schedule = schedules.get(template.id)
        responses.append(
            PackingListTemplateResponse(
                **summary.model_dump(),
                items=[PackingListTemplateItemResponse.model_validate(i) for i in items],
                bags=_bag_on_list_responses(logic.resolve_template_bags(db, template.id, items)),
                schedule=_schedule_response(schedule, template.name) if schedule else None,
            )
        )
    return responses


def _template_response(db: Session, template: PackingListTemplate) -> PackingListTemplateResponse:
    return _template_responses(db, [template])[0]


def _bag_on_list_responses(bags: list[logic.BagOnList]) -> list[PackingListBagOnListResponse]:
    return [
        PackingListBagOnListResponse(
            id=b.id,
            name=b.name,
            owner_id=b.owner_id,
            parent_bag_id=b.parent_bag_id,
            changed=b.changed,
            checked=b.checked,
        )
        for b in bags
    ]


def _day_responses(db: Session, rows: list[PackingListDay]) -> list[PackingListDayResponse]:
    """Days with their items as each day has them, every item with its check,
    and the bags on each day with whether each was grabbed.

    The counts come from the same resolved list the rows do
    (``logic.resolve_day``), so "3 of 13 packed" always describes what is on
    screen, a day's own additions and removals included. A template on several
    days — the backpack, every weekday — is read once. A templateless day
    reads its own name, description and items.

    A grab check whose bag has left a day from today on is deleted here
    (``logic.prune_bag_checks``), the one place every day is read through, so
    a bag that comes back comes back unchecked. A past day is the archive and
    keeps its checks.
    """
    today = today_local_str(db)
    pruned = False
    templates = {
        t.id: t
        for t in db.query(PackingListTemplate)
        .filter(PackingListTemplate.id.in_({d.packing_list_template_id for d in rows}))
        .all()
    }
    contents: dict[int | None, list[PackingListTemplateItem]] = {}
    linked = logic.day_events(db, [d.id for d in rows])
    responses = []
    for day in rows:
        template = templates.get(day.packing_list_template_id)
        if day.packing_list_template_id is not None and template is None:
            # A day pointing at a template that is gone. Deleting a template
            # makes its days templateless first, so this is a guard, not a path.
            continue
        if day.packing_list_template_id not in contents:
            contents[day.packing_list_template_id] = logic.ordered_template_items(
                db, day.packing_list_template_id
            )
        items = contents[day.packing_list_template_id]
        resolved = logic.resolve_day(db, day, items)
        bags = logic.resolve_day_bags(db, day, resolved)
        if day.date >= today and logic.prune_bag_checks(db, day, bags):
            pruned = True
        lead = logic.pack_days_before(day, template)
        responses.append(
            PackingListDayResponse(
                id=day.id,
                packing_list_template_id=day.packing_list_template_id,
                name=logic.day_name(day, template),
                description=logic.day_description(day, template),
                date=day.date,
                label=day.label,
                pack_days_before=lead,
                pack_date=logic.pack_date(day.date, lead),
                schedule_id=day.schedule_id,
                total=len(resolved),
                checked=sum(1 for i in resolved if i.checked),
                bags_total=len(bags),
                bags_checked=sum(1 for b in bags if b.checked),
                changed_count=logic.change_count(db, day, items),
                items=[
                    PackingListDayItemResponse(
                        id=i.id,
                        source=i.source,
                        owner_id=i.owner_id,
                        bag_id=i.bag_id,
                        name=i.name,
                        note=i.note,
                        sort_order=i.sort_order,
                        checked=i.checked,
                        changed=i.changed,
                    )
                    for i in resolved
                ],
                bags=_bag_on_list_responses(bags),
                events=linked.get(day.id, []),
            )
        )
    if pruned:
        db.commit()
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


def _date_clash(
    db: Session, template_id: int | None, day_date: str, *, exclude_id: int | None = None
):
    """A template is on a date once: ``409`` naming the day it is already on.

    A templateless day never clashes. Filtering on ``None`` would compare
    ``IS NULL`` and call every other templateless day on that date a clash.
    """
    if template_id is None:
        return
    query = db.query(PackingListDay).filter(
        PackingListDay.packing_list_template_id == template_id, PackingListDay.date == day_date
    )
    if exclude_id is not None:
        query = query.filter(PackingListDay.id != exclude_id)
    clash = query.first()
    if clash:
        raise HTTPException(
            status_code=409,
            detail={"message": f"This packing list is already on {day_date}", "id": clash.id},
        )


def _run_daily_passes(db: Session) -> None:
    """What the Packing Lists page's listing does before it reads: put
    schedules' templates on their days, and count the days that are over
    into item history, and fill in calendar events' packing lists as the
    lookahead moves forward. All cheap when there is nothing to do."""
    tz_name = local_timezone_name(db)
    today = today_local(tz_name)
    logic.process_schedules(db, today)
    logic.process_event_packing_lists(db, today, ZoneInfo(tz_name))
    logic.count_packed_days(db, today)


# --- Templates --------------------------------------------------------------------


@templates_router.get("", response_model=list[PackingListTemplateResponse])
def list_templates(db: Session = Depends(get_db)):
    """Every template, by name, whole: its counts, items and schedule.

    Whole because the Packing Lists page edits each one in place, under its row.
    """
    rows = db.query(PackingListTemplate).order_by(func.lower(PackingListTemplate.name).asc()).all()
    return _template_responses(db, rows)


@templates_router.post("", response_model=PackingListTemplateResponse, status_code=201)
def create_template(payload: PackingListTemplateCreate, db: Session = Depends(get_db)):
    """Create a template. ``409`` when the name is taken, ignoring case.

    ``copy_from_template_id`` starts it with a copy of that template's items,
    each taken whole — name, note, owner and bag — in the source's order, and
    its readings of bags (whose each is, what each goes in), so the copy's
    bags read as the source's did. Only the items and those readings come
    over: the name, description and lead time are the body's
    own, and nothing links the copy back to its source, so a later edit or
    deletion on either leaves the other alone. Copying is not typing, so item
    history is untouched, as it is for a day's copy (``_create_one_off``). An
    unknown source is a ``422``; the name check runs first, so neither failure
    leaves anything behind.
    """
    if _name_taken(db, payload.name):
        raise HTTPException(
            status_code=409, detail="A packing list template with that name already exists"
        )
    source = None
    if payload.copy_from_template_id is not None:
        source = (
            db.query(PackingListTemplate)
            .filter(PackingListTemplate.id == payload.copy_from_template_id)
            .first()
        )
        if not source:
            raise HTTPException(status_code=422, detail="Unknown copy_from_template_id")
    template = PackingListTemplate(
        name=payload.name,
        description=payload.description,
        pack_days_before=payload.pack_days_before,
    )
    db.add(template)
    db.flush()
    if source is not None:
        for position, item in enumerate(logic.ordered_template_items(db, source.id)):
            db.add(
                PackingListTemplateItem(
                    packing_list_template_id=template.id,
                    owner_id=item.owner_id,
                    bag_id=item.bag_id,
                    name=item.name,
                    note=item.note,
                    sort_order=position,
                )
            )
        for row in _template_bag_rows(db, source.id):
            db.add(
                PackingListTemplateBag(
                    packing_list_template_id=template.id,
                    bag_id=row.bag_id,
                    owner_id=row.owner_id,
                    parent_bag_id=row.parent_bag_id,
                )
            )
    db.commit()
    db.refresh(template)
    return _template_response(db, template)


@templates_router.get("/{template_id}", response_model=PackingListTemplateResponse)
def get_template(template_id: int, db: Session = Depends(get_db)):
    """A template with its items, in order."""
    return _template_response(db, _get_template(db, template_id))


@templates_router.put("/{template_id}", response_model=PackingListTemplateResponse)
def update_template(
    template_id: int, payload: PackingListTemplateUpdate, db: Session = Depends(get_db)
):
    """Rename a template, or change its description or when it is packed."""
    template = _get_template(db, template_id)
    if payload.name is not None and payload.name != template.name:
        if _name_taken(db, payload.name, exclude_id=template.id):
            raise HTTPException(
                status_code=409, detail="A packing list template with that name already exists"
            )
        template.name = payload.name
    if payload.description is not UNSET:
        template.description = payload.description
    if payload.pack_days_before is not None:
        template.pack_days_before = payload.pack_days_before
    db.commit()
    db.refresh(template)
    return _template_response(db, template)


@templates_router.delete("/{template_id}", status_code=204)
def delete_template(template_id: int, db: Session = Depends(get_db)):
    """Delete a template, its items and its schedule.

    Every day it is on is kept, made templateless first so it reads exactly as
    it did (``logic.delete_template``): deleting a template never erases what
    was packed or what is coming up.
    """
    logic.delete_template(db, _get_template(db, template_id))
    db.commit()
    return Response(status_code=204)


# --- Template items -----------------------------------------------------------------


@templates_router.post(
    "/{template_id}/items", response_model=PackingListTemplateItemResponse, status_code=201
)
def create_template_item(
    template_id: int, payload: PackingListItemCreate, db: Session = Depends(get_db)
):
    """Add an item to the bottom of the template. It is unchecked on every day.

    A bag named for the first time joins the household's bags, and the name
    joins the item history behind autocomplete.
    """
    _get_template(db, template_id)
    _require_owner(db, payload.owner_id)
    item = PackingListTemplateItem(
        packing_list_template_id=template_id,
        owner_id=payload.owner_id,
        bag_id=_bag_id(db, bag=payload.bag, bag_id=payload.bag_id, owner_id=payload.owner_id),
        name=payload.name,
        note=payload.note,
        sort_order=logic.template_bottom_position(db, template_id),
    )
    db.add(item)
    logic.record_item_history(db, item.name, item.owner_id, item.bag_id)
    db.commit()
    db.refresh(item)
    return item


@templates_router.put(
    "/{template_id}/items/{template_item_id}", response_model=PackingListTemplateItemResponse
)
def update_template_item(
    template_id: int,
    template_item_id: int,
    payload: PackingListItemUpdate,
    db: Session = Depends(get_db),
):
    """Partial update: name, note, owner, bag. The item keeps its place.

    Checks are untouched: the item is the same item on every day, whatever it
    is now called or whoever now owns it. A new name is remembered for
    autocomplete, as a typed name is.
    """
    item = _get_template_item(db, template_id, template_item_id)
    if payload.note is not UNSET:
        item.note = payload.note
    _apply_owner_and_bag(db, item, payload)
    _rename(db, item, payload.name)
    db.commit()
    db.refresh(item)
    return item


@templates_router.delete("/{template_id}/items/{template_item_id}", status_code=204)
def delete_template_item(template_id: int, template_item_id: int, db: Session = Depends(get_db)):
    """Delete an item from the template, and so from every day it is on.

    A day that had edited it loses its edit too: there is no longer a template
    item for it to be a reading of. History keeps the name.
    """
    item = _get_template_item(db, template_id, template_item_id)
    for model in (PackingListDayCheck, PackingListDayItem):
        db.query(model).filter(model.template_item_id == item.id).delete(synchronize_session=False)
    db.delete(item)
    db.commit()
    return Response(status_code=204)


@templates_router.post(
    "/{template_id}/items/reorder", response_model=list[PackingListTemplateItemResponse]
)
def reorder_template_items(
    template_id: int, payload: PackingListTemplateReorder, db: Session = Depends(get_db)
):
    """One group, in one view, as it should now read — and who or what it is.

    Every listed item takes the group's owner (``view: owner``) or bag
    (``view: bag``), so a drag into another group is one request and cannot
    half-apply. The template keeps one order: the listed items are dealt back
    into the positions they held between them, in the new sequence, so items
    in other groups keep their places. Duplicate ids keep their first mention;
    an id not on this template is a ``404`` and nothing changes; an unknown
    owner or bag is a ``422``.

    The template's order wins: every day it is on from today on drops the
    order it was arranged in by hand and reads in the new one.
    """
    _get_template(db, template_id)
    _validate_group_key(db, payload.view, payload.key)

    ordered_ids: list[int] = []
    for item_id in payload.item_ids:
        if item_id not in ordered_ids:
            ordered_ids.append(item_id)

    items = {
        i.id: i
        for i in db.query(PackingListTemplateItem)
        .filter(
            PackingListTemplateItem.packing_list_template_id == template_id,
            PackingListTemplateItem.id.in_(ordered_ids),
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
    db.query(PackingListDay).filter(
        PackingListDay.packing_list_template_id == template_id,
        PackingListDay.date >= today_local_str(db),
    ).update({PackingListDay.item_order: None}, synchronize_session=False)
    db.commit()
    return [items[i] for i in ordered_ids]


# --- Bags ----------------------------------------------------------------------------


def _get_bag(db: Session, bag_id: int) -> PackingListBag:
    bag = db.query(PackingListBag).filter(PackingListBag.id == bag_id).first()
    if not bag:
        raise HTTPException(status_code=404, detail="Bag not found")
    return bag


def _bag_clash(db: Session, name: str, owner_id: int | None) -> HTTPException:
    """The ``409`` for a second bag with this name and owner, saying whose."""
    member = (
        db.query(FamilyMember).filter(FamilyMember.id == owner_id).first() if owner_id else None
    )
    whose = f"{member.name} already has a" if member else "There's already an unowned"
    return HTTPException(status_code=409, detail=f'{whose} bag called "{name}".')


def _require_bag_name_free(
    db: Session, name: str, owner_id: int | None, *, exclude_id: int | None = None
) -> None:
    """A bag is unique by its name, ignoring case, and its owner: Emma and Jake
    can each have a "Backpack", but Emma cannot have two (``409``)."""
    query = db.query(PackingListBag.id).filter(func.lower(PackingListBag.name) == name.lower())
    query = query.filter(
        PackingListBag.owner_id.is_(None)
        if owner_id is None
        else PackingListBag.owner_id == owner_id
    )
    if exclude_id is not None:
        query = query.filter(PackingListBag.id != exclude_id)
    if query.first() is not None:
        raise _bag_clash(db, name, owner_id)


def _require_reading_free(
    db: Session,
    bag: PackingListBag,
    owner_id: int | None,
    *,
    template_id: int | None,
    day_id: int | None = None,
) -> None:
    """A list cannot read two bags as one name and owner, any more than the
    household can hold them: giving the shared Cooler to Emma on a list where
    Emma's own Cooler is hers is a ``409``."""
    others = (
        db.query(PackingListBag.id)
        .filter(func.lower(PackingListBag.name) == bag.name.lower(), PackingListBag.id != bag.id)
        .all()
    )
    if not others:
        return
    _, readings, _ = logic.readings_at(db, template_id=template_id, day_id=day_id)
    if any(readings[other_id][0] == owner_id for (other_id,) in others):
        raise _bag_clash(db, bag.name, owner_id)


def _bag_responses(db: Session, bags: list[PackingListBag]) -> list[PackingListBagResponse]:
    counts = dict(
        db.query(PackingListTemplateItem.bag_id, func.count(PackingListTemplateItem.id))
        .filter(PackingListTemplateItem.bag_id.isnot(None))
        .group_by(PackingListTemplateItem.bag_id)
        .all()
    )
    return [
        PackingListBagResponse(
            id=b.id,
            name=b.name,
            owner_id=b.owner_id,
            parent_bag_id=b.parent_bag_id,
            item_count=counts.get(b.id, 0),
        )
        for b in bags
    ]


def _check_nesting(
    db: Session,
    bag_id: int,
    parent_bag_id: int | None,
    *,
    template_id: int | None = None,
    day_id: int | None = None,
) -> None:
    """A bag's parent must be a bag, and not the bag or one inside it, as bags
    nest where it is being set: the household, a template, or a day (``422``)."""
    if parent_bag_id is None:
        return
    if not db.query(PackingListBag.id).filter(PackingListBag.id == parent_bag_id).first():
        raise HTTPException(status_code=422, detail="Unknown parent_bag_id")
    if logic.nests_in_itself(db, bag_id, parent_bag_id, template_id=template_id, day_id=day_id):
        raise HTTPException(
            status_code=422, detail="A bag can't go inside itself or a bag that's inside it."
        )


@bags_router.get("", response_model=list[PackingListBagResponse])
def list_bags(db: Session = Depends(get_db)):
    """The household's bags, A to Z, each with its default owner and the bag it
    goes in, and how many template items it holds."""
    return _bag_responses(db, logic.ordered_bags(db))


@bags_router.post("", response_model=PackingListBagResponse, status_code=201)
def create_bag(payload: PackingListBagCreate, db: Session = Depends(get_db)):
    """Add a bag, with no owner. ``409`` when an ownerless bag already has the
    name, ignoring case."""
    _require_bag_name_free(db, payload.name, None)
    bag = PackingListBag(name=payload.name)
    db.add(bag)
    db.commit()
    db.refresh(bag)
    return _bag_responses(db, [bag])[0]


@bags_router.put("/{bag_id}", response_model=PackingListBagResponse)
def update_bag(bag_id: int, payload: PackingListBagUpdate, db: Session = Depends(get_db)):
    """Partial: rename a bag everywhere it is used, or change the household's
    default owner and the bag it goes in. A template or day that reads the bag
    differently keeps its own reading.

    ``409`` when the name and owner it would end up with are another bag's;
    an unknown owner or bag, or a bag put inside itself, is a ``422``.
    """
    bag = _get_bag(db, bag_id)
    name = payload.name if payload.name is not None else bag.name
    owner_id = payload.owner_id if payload.owner_id is not UNSET else bag.owner_id
    if payload.owner_id is not UNSET:
        _require_owner(db, owner_id)
    if name.lower() != bag.name.lower() or owner_id != bag.owner_id:
        _require_bag_name_free(db, name, owner_id, exclude_id=bag.id)
    bag.name = name
    bag.owner_id = owner_id
    if payload.parent_bag_id is not UNSET:
        _check_nesting(db, bag.id, payload.parent_bag_id)
        bag.parent_bag_id = payload.parent_bag_id
    db.commit()
    db.refresh(bag)
    return _bag_responses(db, [bag])[0]


@bags_router.delete("/{bag_id}", status_code=204)
def delete_bag(bag_id: int, db: Session = Depends(get_db)):
    """Delete a bag. Whatever was in it goes to No bag rather than going with it,
    and the bags that went in it go in nothing.

    SQLite does not enforce the reference, so this is done by hand — items on
    every template, every day's own items and readings, and history; the bags
    inside it, by default and in every reading, and any day's record of a bag
    taken out of it — the same reason deleting a
    shopping store moves its items to Anywhere. Readings of the bag itself and
    its grab checks go with it.
    """
    bag = _get_bag(db, bag_id)
    for model in (PackingListTemplateItem, PackingListDayItem, PackingListItemHistory):
        db.query(model).filter(model.bag_id == bag.id).update(
            {model.bag_id: None}, synchronize_session=False
        )
    for model in (PackingListBag, PackingListTemplateBag, PackingListDayBag):
        db.query(model).filter(model.parent_bag_id == bag.id).update(
            {model.parent_bag_id: None}, synchronize_session=False
        )
    db.query(PackingListDayBag).filter(PackingListDayBag.removed_from_bag_id == bag.id).update(
        {PackingListDayBag.removed_from_bag_id: None}, synchronize_session=False
    )
    for model in (PackingListTemplateBag, PackingListDayBag, PackingListDayBagCheck):
        db.query(model).filter(model.bag_id == bag.id).delete(synchronize_session=False)
    db.delete(bag)
    db.commit()
    return Response(status_code=204)


# --- Bags on a list ----------------------------------------------------------------------
#
# A template or a day can read a bag differently from the household — whose it
# is, what it goes in — and can take a bag off itself, which moves what was in
# it to No bag. Nothing here ever takes an item off a list.


def _template_bag_rows(db: Session, template_id: int) -> list[PackingListTemplateBag]:
    return (
        db.query(PackingListTemplateBag)
        .filter(PackingListTemplateBag.packing_list_template_id == template_id)
        .all()
    )


def _require_on_list(bags: list[logic.BagOnList], bag_id: int) -> None:
    if bag_id not in {b.id for b in bags}:
        raise HTTPException(status_code=404, detail="That bag isn't on this packing list")


def _template_bags(db: Session, template: PackingListTemplate) -> list[logic.BagOnList]:
    return logic.resolve_template_bags(
        db, template.id, logic.ordered_template_items(db, template.id)
    )


def _set_template_reading(
    db: Session, template_id: int, bag_id: int, owner_id: int | None, parent_bag_id: int | None
) -> None:
    row = (
        db.query(PackingListTemplateBag)
        .filter(
            PackingListTemplateBag.packing_list_template_id == template_id,
            PackingListTemplateBag.bag_id == bag_id,
        )
        .first()
    )
    if row is None:
        row = PackingListTemplateBag(packing_list_template_id=template_id, bag_id=bag_id)
        db.add(row)
    row.owner_id = owner_id
    row.parent_bag_id = parent_bag_id


def _set_day_reading(
    db: Session,
    day_id: int,
    bag_id: int,
    owner_id: int | None,
    parent_bag_id: int | None,
    *,
    removed_from_bag_id: int | None = None,
) -> None:
    """Write a day's reading of a bag, whole. ``removed_from_bag_id`` is set
    only by taking a bag off the day; every other write clears it, which is
    what keeps a resync from undoing a choice somebody made by hand."""
    row = (
        db.query(PackingListDayBag)
        .filter(PackingListDayBag.day_id == day_id, PackingListDayBag.bag_id == bag_id)
        .first()
    )
    if row is None:
        row = PackingListDayBag(day_id=day_id, bag_id=bag_id)
        db.add(row)
    row.owner_id = owner_id
    row.parent_bag_id = parent_bag_id
    row.removed_from_bag_id = removed_from_bag_id


@templates_router.put("/{template_id}/bags/{bag_id}", response_model=PackingListTemplateResponse)
def update_template_bag(
    template_id: int,
    bag_id: int,
    payload: PackingListBagReadingUpdate,
    db: Session = Depends(get_db),
):
    """Read a bag differently on this template: whose it is and what it goes in.

    Every day the template is on reads it too, except a day with its own
    reading of that bag. A bag not on the template is a ``404``; an unknown
    owner or bag, or a bag put inside itself, is a ``422``; an owner that would
    make it read as another bag's name and owner here is a ``409``.
    """
    template = _get_template(db, template_id)
    bag = _get_bag(db, bag_id)
    _require_on_list(_template_bags(db, template), bag_id)
    _require_owner(db, payload.owner_id)
    _require_reading_free(db, bag, payload.owner_id, template_id=template.id)
    _check_nesting(db, bag_id, payload.parent_bag_id, template_id=template.id)
    _set_template_reading(db, template.id, bag_id, payload.owner_id, payload.parent_bag_id)
    db.commit()
    return _template_response(db, template)


@templates_router.delete(
    "/{template_id}/bags/{bag_id}/reading", response_model=PackingListTemplateResponse
)
def reset_template_bag(template_id: int, bag_id: int, db: Session = Depends(get_db)):
    """Reset: the bag reads as the household has it again, on this template.
    Idempotent."""
    template = _get_template(db, template_id)
    _get_bag(db, bag_id)
    db.query(PackingListTemplateBag).filter(
        PackingListTemplateBag.packing_list_template_id == template.id,
        PackingListTemplateBag.bag_id == bag_id,
    ).delete(synchronize_session=False)
    db.commit()
    return _template_response(db, template)


@templates_router.post(
    "/{template_id}/bags/{bag_id}/remove", response_model=PackingListTemplateResponse
)
def remove_template_bag(template_id: int, bag_id: int, db: Session = Depends(get_db)):
    """Take a bag off a template. Its items stay, in No bag.

    The template's items in it go to No bag, which reaches every day it is on
    except an item a day reads for itself. The bags that went in it go in
    nothing on this template. Item history is untouched: this is a move.
    """
    template = _get_template(db, template_id)
    _get_bag(db, bag_id)
    bags = _template_bags(db, template)
    _require_on_list(bags, bag_id)
    db.query(PackingListTemplateItem).filter(
        PackingListTemplateItem.packing_list_template_id == template.id,
        PackingListTemplateItem.bag_id == bag_id,
    ).update({PackingListTemplateItem.bag_id: None}, synchronize_session=False)
    for inner in bags:
        if inner.parent_bag_id == bag_id:
            _set_template_reading(db, template.id, inner.id, inner.owner_id, None)
    db.commit()
    return _template_response(db, template)


# --- Item history ----------------------------------------------------------------------


def _escape_like(term: str) -> str:
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


@items_router.get("/suggestions", response_model=list[PackingListSuggestion])
def list_suggestions(
    q: str | None = Query(None, description="Substring to match anywhere in the item name"),
    limit: int = Query(DEFAULT_SUGGESTIONS, ge=1),
    db: Session = Depends(get_db),
):
    """Autocomplete from every item name somebody has typed, the shopping list's rules.

    Substring, not prefix, so ``towel`` finds "Beach towel"; prefix matches
    rank first, then by how many past days the name was packed on, then by
    how recently it was typed. An empty ``q`` returns the most packed. Each
    suggestion carries the owner and bag it last had, so the form can fill
    them in when nobody has chosen yet.
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

    Puts every active schedule's template on its coming days first, the same
    arrangement ``GET /api/todos`` has with recurring tasks: this is what the
    Packing Lists page loads, so a schedule's days are there whenever anybody
    looks. Counts the days that are over into item history too, for the same
    reason (``logic.count_packed_days``).
    """
    _run_daily_passes(db)
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
    ``total`` counts every match, the same contract as previous notes. An
    outer join, so a templateless day is in the archive too, found by its own
    name.
    """
    today = today_local_str(db)
    query = (
        db.query(PackingListDay)
        .outerjoin(
            PackingListTemplate, PackingListTemplate.id == PackingListDay.packing_list_template_id
        )
        .filter(PackingListDay.date < today)
    )
    if search and search.strip():
        pattern = f"%{search.strip()}%"
        query = query.filter(
            or_(
                PackingListTemplate.name.ilike(pattern),
                PackingListDay.name.ilike(pattern),
                PackingListDay.label.ilike(pattern),
            )
        )

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

    Three forms (``PackingListDayCreate``): a template kept in sync, a blank
    one-off, or a one-off holding a copy of a template's items. A day before
    today is a ``422``: nobody packs for yesterday. A template already on that
    day is a ``409`` carrying the existing day's id, so the page can open it
    instead — the same shape Notes uses. A one-off never clashes: its name is
    its own, and any number of them can share a date.
    """
    if payload.is_one_off:
        return _create_one_off(db, payload)
    template = (
        db.query(PackingListTemplate)
        .filter(PackingListTemplate.id == payload.packing_list_template_id)
        .first()
    )
    if not template:
        raise HTTPException(status_code=422, detail="Unknown packing_list_template_id")
    if payload.date < today_local_str(db):
        raise HTTPException(status_code=422, detail="Pick today or a later day")
    _date_clash(db, template.id, payload.date)
    day = PackingListDay(
        packing_list_template_id=template.id,
        date=payload.date,
        label=payload.label,
        pack_days_before=payload.pack_days_before,
    )
    db.add(day)
    db.commit()
    db.refresh(day)
    return _day_response(db, day)


def _create_one_off(db: Session, payload: PackingListDayCreate) -> PackingListDayResponse:
    """A templateless day of its own: blank, or holding a copy of a template's
    items (``copy_from_template_id``).

    A copy takes each item whole — name, note, owner and bag — in the
    template's order, unchecked, as the day's own items, and the template's
    readings of bags as the day's own readings. Nothing points back
    at the template, so a later edit to it, or its deletion, leaves the copy
    alone. Copying is not typing, so item history is untouched: the day counts
    once its date is over (``logic.count_packed_days``), like any other.
    """
    source = None
    if payload.copy_from_template_id is not None:
        source = (
            db.query(PackingListTemplate)
            .filter(PackingListTemplate.id == payload.copy_from_template_id)
            .first()
        )
        if not source:
            raise HTTPException(status_code=422, detail="Unknown copy_from_template_id")
    if payload.date < today_local_str(db):
        raise HTTPException(status_code=422, detail="Pick today or a later day")
    day = PackingListDay(
        packing_list_template_id=None,
        name=payload.name,
        date=payload.date,
        label=payload.label,
        pack_days_before=payload.pack_days_before,
    )
    db.add(day)
    db.flush()
    if source is not None:
        for position, item in enumerate(logic.ordered_template_items(db, source.id)):
            db.add(
                PackingListDayItem(
                    day_id=day.id,
                    template_item_id=None,
                    owner_id=item.owner_id,
                    bag_id=item.bag_id,
                    name=item.name,
                    note=item.note,
                    sort_order=position,
                )
            )
        for row in _template_bag_rows(db, source.id):
            db.add(
                PackingListDayBag(
                    day_id=day.id,
                    bag_id=row.bag_id,
                    owner_id=row.owner_id,
                    parent_bag_id=row.parent_bag_id,
                )
            )
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
    lead time (``pack_days_before``; ``null`` follows the template again, and
    is a ``422`` on a templateless day, which has no template to follow).
    Rename a templateless day (``name``; a ``422`` on a templated day).
    Checks stay.

    A label set here is the day's own from then on: a schedule's relabel
    passes over it.
    """
    day = _get_day(db, day_id)
    _require_current(db, day)
    if payload.name is not None:
        if day.packing_list_template_id is not None:
            raise HTTPException(
                status_code=422,
                detail="This packing list is called what its template is called.",
            )
        day.name = payload.name
    if payload.date is not None and payload.date != day.date:
        events = logic.day_events(db, [day.id]).get(day.id, [])
        if events:
            titles = logic.join_names([event["title"] for event in events])
            raise HTTPException(
                status_code=422,
                detail=(f"This packing list moves with {titles}. Change the date on the calendar."),
            )
        if payload.date < today_local_str(db):
            raise HTTPException(status_code=422, detail="Pick today or a later day")
        _date_clash(db, day.packing_list_template_id, payload.date, exclude_id=day.id)
        day.date = payload.date
    if payload.label is not UNSET:
        day.label = payload.label
        day.label_edited = True
    if payload.pack_days_before is not UNSET:
        if payload.pack_days_before is None and day.packing_list_template_id is None:
            raise HTTPException(
                status_code=422,
                detail="This packing list has no template, so it needs its own lead time.",
            )
        day.pack_days_before = payload.pack_days_before
    db.commit()
    db.refresh(day)
    return _day_response(db, day)


@days_router.delete("/{day_id}", status_code=204)
def delete_day(day_id: int, db: Session = Depends(get_db)):
    """Take a packing list off a day. Its template, if it has one, is untouched.

    A day a schedule made stays off: the schedule never goes back over a date
    it has already handled. Item history needs nothing undone, because a day
    is only counted once its date is over, and a past day can't be deleted.
    """
    day = _get_day(db, day_id)
    _require_current(db, day)
    # Off every event it belongs to too, so none of them puts it back.
    logic.unlink_day(db, day)
    logic.clear_day_state(db, day)
    db.delete(day)
    db.commit()
    return Response(status_code=204)


@days_router.post("/{day_id}/items/reorder", response_model=PackingListDayResponse)
def reorder_day_items(day_id: int, payload: PackingListDayReorder, db: Session = Depends(get_db)):
    """One group of a day's packing list, in one view, as it should now read.

    The day's version of ``POST /api/packing-list-templates/{id}/items/reorder``:
    the listed items are dealt back into the places they held between them,
    so items in other groups keep theirs, and the day's whole order is stored
    in ``item_order``. Template items and the day's own share that one order.

    A drag within a group changes only the order, so nothing is marked
    changed. A drag into another group gives the item that group's owner or
    bag on this day only: a template item through the day's reading of it
    (so it reads ``(changed)``), a day's own item directly. Duplicates keep
    their first mention; an item not on the day is a ``404`` and nothing
    changes; an unknown owner or bag is a ``422``; a past day is a ``403``.
    """
    day = _get_day(db, day_id)
    _require_current(db, day)
    _validate_group_key(db, payload.view, payload.key)

    template_items = logic.ordered_template_items(db, day.packing_list_template_id)
    resolved = logic.resolve_day(db, day, template_items)
    by_key = {item.key: item for item in resolved}
    listed: list[str] = []
    for ref in payload.items:
        key = logic.item_key(ref.source, ref.id)
        if key not in listed:
            listed.append(key)
    missing = [key for key in listed if key not in by_key]
    if missing:
        raise HTTPException(status_code=404, detail=f"Unknown items: {missing}")

    order = [item.key for item in resolved]
    slots = sorted(order.index(key) for key in listed)
    for slot, key in zip(slots, listed, strict=True):
        order[slot] = key
    day.item_order = order

    field = "owner_id" if payload.view == "owner" else "bag_id"
    templates_by_id = {item.id: item for item in template_items}
    for key in listed:
        item = by_key[key]
        if getattr(item, field) == payload.key:
            continue
        if item.source == logic.TEMPLATE:
            setattr(_day_change(db, day, templates_by_id[item.id]), field, payload.key)
        else:
            setattr(_get_own_item(db, day, item.id), field, payload.key)
    db.commit()
    return _day_response(db, day)


def _day_change(
    db: Session, day: PackingListDay, item: PackingListTemplateItem
) -> PackingListDayItem:
    """This day's reading of a template item, created from the item if new.

    A new reading copies the item whole — name, note, owner and bag — so from
    then on the day keeps all four, whichever one it set out to change.
    """
    change = (
        db.query(PackingListDayItem)
        .filter(PackingListDayItem.day_id == day.id, PackingListDayItem.template_item_id == item.id)
        .first()
    )
    if change is None:
        change = PackingListDayItem(
            day_id=day.id,
            template_item_id=item.id,
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


@days_router.put(
    "/{day_id}/template-items/{template_item_id}", response_model=PackingListDayResponse
)
def update_day_template_item(
    day_id: int,
    template_item_id: int,
    payload: PackingListDayItemUpdate,
    db: Session = Depends(get_db),
):
    """Check, uncheck or edit a template item on one day. Idempotent.

    An edit (``name``, ``note``, owner, bag) is this day's only: the template
    keeps its own, and from then on a change to that item on the template no
    longer reaches this day. Returns the whole day so the caller's progress
    line comes from the server rather than from counting checkboxes.
    """
    day = _get_day(db, day_id)
    _require_current(db, day)
    item = _get_template_item(db, day.packing_list_template_id, template_item_id)
    if payload.checked is not None:
        existing = (
            db.query(PackingListDayCheck)
            .filter(
                PackingListDayCheck.day_id == day.id,
                PackingListDayCheck.template_item_id == item.id,
            )
            .first()
        )
        if payload.checked and not existing:
            db.add(PackingListDayCheck(day_id=day.id, template_item_id=item.id))
        elif not payload.checked and existing:
            db.delete(existing)
    if _edits(payload):
        change = _day_change(db, day, item)
        if payload.note is not UNSET:
            change.note = payload.note
        _apply_owner_and_bag(db, change, payload)
        _rename(db, change, payload.name)
    db.commit()
    return _day_response(db, day)


@days_router.delete(
    "/{day_id}/template-items/{template_item_id}", response_model=PackingListDayResponse
)
def remove_day_template_item(day_id: int, template_item_id: int, db: Session = Depends(get_db)):
    """Take a template item off one day. The template, and every other day, keep it."""
    day = _get_day(db, day_id)
    _require_current(db, day)
    item = _get_template_item(db, day.packing_list_template_id, template_item_id)
    _day_change(db, day, item).removed = True
    db.query(PackingListDayCheck).filter(
        PackingListDayCheck.day_id == day.id, PackingListDayCheck.template_item_id == item.id
    ).delete(synchronize_session=False)
    db.commit()
    return _day_response(db, day)


def _get_own_item(db: Session, day: PackingListDay, own_id: int) -> PackingListDayItem:
    own = (
        db.query(PackingListDayItem)
        .filter(
            PackingListDayItem.id == own_id,
            PackingListDayItem.day_id == day.id,
            PackingListDayItem.template_item_id.is_(None),
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
        .filter(PackingListDayItem.day_id == day.id, PackingListDayItem.template_item_id.is_(None))
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
        bag_id=_bag_id(db, bag=payload.bag, bag_id=payload.bag_id, owner_id=payload.owner_id),
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
    if payload.note is not UNSET:
        own.note = payload.note
    _apply_owner_and_bag(db, own, payload)
    _rename(db, own, payload.name)
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
    """Uncheck everything on one day, its own items and its bags included."""
    day = _get_day(db, day_id)
    _require_current(db, day)
    for model in (PackingListDayCheck, PackingListDayBagCheck):
        db.query(model).filter(model.day_id == day.id).delete(synchronize_session=False)
    db.query(PackingListDayItem).filter(PackingListDayItem.day_id == day.id).update(
        {PackingListDayItem.checked: False}, synchronize_session=False
    )
    db.commit()
    return _day_response(db, day)


@days_router.post("/{day_id}/check-all", response_model=PackingListDayResponse)
def check_all(day_id: int, db: Session = Depends(get_db)):
    """Check everything on one day, its own items and its bags included.
    Idempotent.

    "Everything" is the day as it reads: an item it removed is not on it, so
    nothing is checked for it, and a bag is grabbed only if it is on the day.
    """
    day = _get_day(db, day_id)
    _require_current(db, day)
    resolved = logic.resolve_day(
        db, day, logic.ordered_template_items(db, day.packing_list_template_id)
    )
    for item in resolved:
        if item.source == logic.TEMPLATE and not item.checked:
            db.add(PackingListDayCheck(day_id=day.id, template_item_id=item.id))
    db.query(PackingListDayItem).filter(
        PackingListDayItem.day_id == day.id, PackingListDayItem.template_item_id.is_(None)
    ).update({PackingListDayItem.checked: True}, synchronize_session=False)
    for bag in logic.resolve_day_bags(db, day, resolved):
        if not bag.checked:
            db.add(PackingListDayBagCheck(day_id=day.id, bag_id=bag.id))
    db.commit()
    return _day_response(db, day)


@days_router.post("/{day_id}/resync", response_model=PackingListDayResponse)
def resync_day(day_id: int, payload: PackingListDayResync, db: Session = Depends(get_db)):
    """Bring one day back in line with its template (``logic.resync_day``):
    ``items`` undoes its item changes, ``all`` everything it does differently.

    Idempotent. A past day is a ``403``; a templateless day, which has no
    template to resync with, a ``422``.
    """
    day = _get_day(db, day_id)
    _require_current(db, day)
    if day.packing_list_template_id is None:
        raise HTTPException(
            status_code=422, detail="This packing list has no template to resync with."
        )
    logic.resync_day(db, day, payload.scope)
    db.commit()
    return _day_response(db, day)


def _day_bags(db: Session, day: PackingListDay) -> tuple[list, list[logic.BagOnList]]:
    """A day's items and bags as it reads them."""
    resolved = logic.resolve_day(
        db, day, logic.ordered_template_items(db, day.packing_list_template_id)
    )
    return resolved, logic.resolve_day_bags(db, day, resolved)


@days_router.put("/{day_id}/bags/{bag_id}", response_model=PackingListDayResponse)
def update_day_bag(
    day_id: int, bag_id: int, payload: PackingListDayBagUpdate, db: Session = Depends(get_db)
):
    """Grab a bag on one day (``checked``), or read it differently on that day
    only (``owner_id``, ``parent_bag_id``). Idempotent.

    A reading is whole: setting one field copies the other from how the bag
    reads on this day now. Returns the whole day. A bag not on the day is a
    ``404``; an unknown owner or bag, or a bag put inside itself, a ``422``;
    an owner that would make it read as another bag's name and owner on this
    day a ``409``; a past day a ``403``.
    """
    day = _get_day(db, day_id)
    _require_current(db, day)
    bag = _get_bag(db, bag_id)
    _, bags = _day_bags(db, day)
    _require_on_list(bags, bag_id)
    if payload.owner_id is not UNSET or payload.parent_bag_id is not UNSET:
        owner_id, parent_bag_id = logic.bag_reading(
            db, bag, template_id=day.packing_list_template_id, day_id=day.id
        )
        if payload.owner_id is not UNSET:
            _require_owner(db, payload.owner_id)
            _require_reading_free(
                db,
                bag,
                payload.owner_id,
                template_id=day.packing_list_template_id,
                day_id=day.id,
            )
            owner_id = payload.owner_id
        if payload.parent_bag_id is not UNSET:
            _check_nesting(
                db,
                bag_id,
                payload.parent_bag_id,
                template_id=day.packing_list_template_id,
                day_id=day.id,
            )
            parent_bag_id = payload.parent_bag_id
        _set_day_reading(db, day.id, bag_id, owner_id, parent_bag_id)
    if payload.checked is not None:
        existing = (
            db.query(PackingListDayBagCheck)
            .filter(
                PackingListDayBagCheck.day_id == day.id, PackingListDayBagCheck.bag_id == bag_id
            )
            .first()
        )
        if payload.checked and not existing:
            db.add(PackingListDayBagCheck(day_id=day.id, bag_id=bag_id))
        elif not payload.checked and existing:
            db.delete(existing)
    db.commit()
    return _day_response(db, day)


@days_router.delete("/{day_id}/bags/{bag_id}/reading", response_model=PackingListDayResponse)
def reset_day_bag(day_id: int, bag_id: int, db: Session = Depends(get_db)):
    """Reset: the bag reads as its template (or the household) has it again, on
    this day. Idempotent; the bag's grab check stays."""
    day = _get_day(db, day_id)
    _require_current(db, day)
    _get_bag(db, bag_id)
    db.query(PackingListDayBag).filter(
        PackingListDayBag.day_id == day.id, PackingListDayBag.bag_id == bag_id
    ).delete(synchronize_session=False)
    db.commit()
    return _day_response(db, day)


@days_router.post("/{day_id}/bags/{bag_id}/remove", response_model=PackingListDayResponse)
def remove_day_bag(day_id: int, bag_id: int, db: Session = Depends(get_db)):
    """Take a bag off one day. Its items stay on the day, in No bag.

    Each item in it moves to No bag on this day only: a template item through
    the day's reading of it (so it reads ``(changed)``), one of the day's own
    directly. The bags that went in it go in nothing on this day, each reading
    recording the bag it came out of (``removed_from_bag_id``) so a resync can
    put it back, and its grab check goes. The template, and every other day,
    keep the bag. Item history is untouched: this is a move.
    """
    day = _get_day(db, day_id)
    _require_current(db, day)
    _get_bag(db, bag_id)
    resolved, bags = _day_bags(db, day)
    _require_on_list(bags, bag_id)
    template_items = {
        i.id: i for i in logic.ordered_template_items(db, day.packing_list_template_id)
    }
    for item in resolved:
        if item.bag_id != bag_id:
            continue
        if item.source == logic.TEMPLATE:
            _day_change(db, day, template_items[item.id]).bag_id = None
        else:
            _get_own_item(db, day, item.id).bag_id = None
    for inner in bags:
        if inner.parent_bag_id == bag_id:
            _set_day_reading(db, day.id, inner.id, inner.owner_id, None, removed_from_bag_id=bag_id)
    db.query(PackingListDayBagCheck).filter(
        PackingListDayBagCheck.day_id == day.id, PackingListDayBagCheck.bag_id == bag_id
    ).delete(synchronize_session=False)
    db.commit()
    return _day_response(db, day)


# --- Template schedules -------------------------------------------------------------


def _get_schedule(db: Session, schedule_id: int) -> PackingListTemplateSchedule:
    schedule = (
        db.query(PackingListTemplateSchedule)
        .filter(PackingListTemplateSchedule.id == schedule_id)
        .first()
    )
    if not schedule:
        raise HTTPException(status_code=404, detail="Schedule not found")
    return schedule


def _require_template(db: Session, template_id: int) -> PackingListTemplate:
    template = db.query(PackingListTemplate).filter(PackingListTemplate.id == template_id).first()
    if not template:
        raise HTTPException(status_code=422, detail="Unknown packing_list_template_id")
    return template


def _schedule_clash(db: Session, template_id: int, *, exclude_id: int | None = None) -> None:
    """A template repeats on one schedule or none: ``409`` naming the one it has."""
    query = db.query(PackingListTemplateSchedule).filter(
        PackingListTemplateSchedule.packing_list_template_id == template_id
    )
    if exclude_id is not None:
        query = query.filter(PackingListTemplateSchedule.id != exclude_id)
    clash = query.first()
    if clash:
        raise HTTPException(
            status_code=409,
            detail={"message": "This packing list template already repeats", "id": clash.id},
        )


def _schedule_response(
    schedule: PackingListTemplateSchedule, template_name: str
) -> PackingListTemplateScheduleResponse:
    return PackingListTemplateScheduleResponse(
        id=schedule.id,
        packing_list_template_id=schedule.packing_list_template_id,
        template_name=template_name,
        recurrence_type=schedule.recurrence_type,
        recurrence_day=schedule.recurrence_day,
        custom_rule=schedule.custom_rule,
        start_date=schedule.start_date,
        end_date=schedule.end_date,
        label=schedule.label,
        active=schedule.active,
        last_generated_date=schedule.last_generated_date,
    )


@template_schedules_router.get("", response_model=list[PackingListTemplateScheduleResponse])
def list_schedules(db: Session = Depends(get_db)):
    """Every schedule, paused ones included, by template name."""
    rows = (
        db.query(PackingListTemplateSchedule, PackingListTemplate.name)
        .join(
            PackingListTemplate,
            PackingListTemplate.id == PackingListTemplateSchedule.packing_list_template_id,
        )
        .order_by(func.lower(PackingListTemplate.name), PackingListTemplateSchedule.id)
        .all()
    )
    return [_schedule_response(schedule, name) for schedule, name in rows]


@template_schedules_router.post(
    "", response_model=PackingListTemplateScheduleResponse, status_code=201
)
def create_schedule(payload: PackingListTemplateScheduleCreate, db: Session = Depends(get_db)):
    """Start putting a template on days by a rule. ``422`` for an unknown template.

    Nothing is put on a day here; the next listing of days does it.
    """
    template = _require_template(db, payload.packing_list_template_id)
    _schedule_clash(db, template.id)
    schedule = PackingListTemplateSchedule(**payload.model_dump())
    db.add(schedule)
    db.commit()
    db.refresh(schedule)
    return _schedule_response(schedule, template.name)


@template_schedules_router.get("/{schedule_id}", response_model=PackingListTemplateScheduleResponse)
def get_schedule(schedule_id: int, db: Session = Depends(get_db)):
    schedule = _get_schedule(db, schedule_id)
    return _schedule_response(
        schedule, _require_template(db, schedule.packing_list_template_id).name
    )


@template_schedules_router.put("/{schedule_id}", response_model=PackingListTemplateScheduleResponse)
def update_schedule(
    schedule_id: int, payload: PackingListTemplateScheduleUpdate, db: Session = Depends(get_db)
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
        for field in PackingListTemplateScheduleUpdate.model_fields
        if (value := getattr(payload, field)) is not UNSET
        and not (
            value is None and field in ("packing_list_template_id", "recurrence_type", "active")
        )
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
    template = _require_template(
        db, changes.get("packing_list_template_id", schedule.packing_list_template_id)
    )
    _schedule_clash(db, template.id, exclude_id=schedule.id)
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
    return _schedule_response(schedule, template.name)


@template_schedules_router.delete("/{schedule_id}", status_code=204)
def delete_schedule(schedule_id: int, db: Session = Depends(get_db)):
    """Delete a schedule. The days it made stay, as days added by hand."""
    schedule = _get_schedule(db, schedule_id)
    db.query(PackingListDay).filter(PackingListDay.schedule_id == schedule.id).update(
        {PackingListDay.schedule_id: None}, synchronize_session=False
    )
    db.delete(schedule)
    db.commit()
    return Response(status_code=204)
