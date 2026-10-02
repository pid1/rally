"""Packing Lists: reusable packing lists, put on a day and checked off there.

A ``PackingList`` is the template. A ``PackingListDay`` puts it on a date. The day
holds no copy of the template's items, only ``PackingListDayCheck`` rows saying
which are checked *on that day*. Two rules follow from that shape with no code
to enforce them:

* An edit to the template is on every day it is on. There is no copy to sync.
* Checking an item off on one day touches nothing else. There is no column on
  the template, or on any other day, for it to write.

A ``PackingListSchedule`` puts a template on days by a repeating rule. It does
so by creating ordinary ``PackingListDay`` rows ahead of time
(``process_schedules``), so everything above holds for a scheduled day too.

A day also keeps its own changes on top of the template (``PackingListDayItem``):
items added to that day only, and that day's edits or removals of a template
item. ``resolve_day`` is the one place that lays the overlay over the template,
so the page, the counts and the summary all read a day the same way.

Every item has an optional owner (a family member) and an optional bag. The
page groups a packing list by either — who owns what, what goes in each bag — over
one order (``sort_order``); the summary groups by owner and names the bag.

This module holds what the router and the daily summary share: display order,
progress counts, pack dates, the day overlay, bags, item history, schedule
processing, and the summary's text.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

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
    ShoppingItem,
)
from rally.recurrence import get_first_recurrence_date, get_next_recurrence_date
from rally.utils.timezone import now_utc

# What an item with no owner, and one in no bag, are grouped under.
EVERYONE = "Everyone"
NO_BAG = "No bag"

# How far ahead the daily summary looks. A week is enough notice to restock or
# order something before a trip, and short enough that the section stays small.
SUMMARY_LOOKAHEAD_DAYS = 7


def pack_days_before(day: PackingListDay, packing_list: PackingList) -> int:
    """How many days ahead this day's packing list is packed: the day's own number
    when it has one, otherwise its packing list template's."""
    return (
        day.pack_days_before if day.pack_days_before is not None else packing_list.pack_days_before
    )


def pack_date(day: str, days_before: int) -> str:
    """The date a day's packing list should be packed, as ``YYYY-MM-DD``."""
    return (date.fromisoformat(day) - timedelta(days=days_before)).isoformat()


def ordered_items(db: Session, packing_list_id: int) -> list[PackingListItem]:
    """A template's items in their one order. Each view groups this list by
    owner or by bag and keeps the order within each group."""
    return (
        db.query(PackingListItem)
        .filter(PackingListItem.packing_list_id == packing_list_id)
        .order_by(PackingListItem.sort_order.asc(), PackingListItem.id.asc())
        .all()
    )


def bottom_position(db: Session, packing_list_id: int) -> int:
    """The position after every item on a template: where a new one goes."""
    highest = (
        db.query(func.max(PackingListItem.sort_order))
        .filter(PackingListItem.packing_list_id == packing_list_id)
        .scalar()
    )
    return 0 if highest is None else highest + 1


def checked_item_ids(db: Session, day_id: int) -> set[int]:
    return {
        row.item_id
        for row in db.query(PackingListDayCheck.item_id).filter(
            PackingListDayCheck.day_id == day_id
        )
    }


@dataclass(frozen=True)
class DayItem:
    """One item as it reads on one day, after the day's own changes."""

    id: int  # The template item's id, or the day item's id when source is "day"
    source: str  # "packing list" or "day"
    owner_id: int | None
    bag_id: int | None
    name: str
    note: str | None
    sort_order: int
    checked: bool
    changed: bool  # A template item this day has edited


def resolve_day(db: Session, day: PackingListDay, items: list[PackingListItem]) -> list[DayItem]:
    """A day's items in reading order: the template's, as this day has them,
    then the day's own.

    ``items`` are the template's, in order, passed in so a template on several
    days is read once. A day's reading of a template item carries its own owner
    and bag along with its name and note, and keeps its place in the order.
    """
    overlay = db.query(PackingListDayItem).filter(PackingListDayItem.day_id == day.id).all()
    changes = {row.item_id: row for row in overlay if row.item_id is not None}
    checked = checked_item_ids(db, day.id)

    resolved: list[DayItem] = []
    for item in items:
        change = changes.get(item.id)
        if change and change.removed:
            continue
        source = change or item
        resolved.append(
            DayItem(
                id=item.id,
                source="packing_list",
                owner_id=source.owner_id,
                bag_id=source.bag_id,
                name=source.name,
                note=source.note,
                sort_order=item.sort_order,
                checked=item.id in checked,
                changed=change is not None,
            )
        )
    own = sorted(
        (row for row in overlay if row.item_id is None), key=lambda r: (r.sort_order, r.id)
    )
    for row in own:
        resolved.append(
            DayItem(
                id=row.id,
                source="day",
                owner_id=row.owner_id,
                bag_id=row.bag_id,
                name=row.name,
                note=row.note,
                sort_order=row.sort_order,
                checked=row.checked,
                changed=False,
            )
        )
    return resolved


def change_count(db: Session, day: PackingListDay, items: list[PackingListItem]) -> int:
    """How many items this day differs from its template on: items it edited,
    removed, or added for itself. An edit to an item since deleted from the
    template no longer counts — deleting the item deletes the edit too, and
    this is the guard if something left one behind."""
    present = {item.id for item in items}
    rows = db.query(PackingListDayItem.item_id).filter(PackingListDayItem.day_id == day.id)
    return sum(1 for (item_id,) in rows if item_id is None or item_id in present)


def item_counts(db: Session, packing_list_ids: list[int]) -> dict[int, int]:
    """Items per packing list, for every id asked about (zero included)."""
    counts = dict.fromkeys(packing_list_ids, 0)
    if packing_list_ids:
        rows = (
            db.query(PackingListItem.packing_list_id, func.count(PackingListItem.id))
            .filter(PackingListItem.packing_list_id.in_(packing_list_ids))
            .group_by(PackingListItem.packing_list_id)
        )
        counts.update(dict(rows.all()))
    return counts


def delete_packing_list(db: Session, packing_list: PackingList) -> None:
    """Delete a packing list and everything hanging off it. Does not commit.

    SQLite does not enforce the references, so the cascade is by hand — the
    same reason deleting an event removes its attendees and overrides itself.
    Bags and item history are the household's, not the packing list's, and stay.
    """
    day_ids = [
        row.id
        for row in db.query(PackingListDay.id).filter(
            PackingListDay.packing_list_id == packing_list.id
        )
    ]
    if day_ids:
        for model in (PackingListDayCheck, PackingListDayItem):
            db.query(model).filter(model.day_id.in_(day_ids)).delete(synchronize_session=False)
    for model in (PackingListDay, PackingListItem, PackingListSchedule):
        db.query(model).filter(model.packing_list_id == packing_list.id).delete(
            synchronize_session=False
        )
    db.delete(packing_list)


# --- Owners and bags -------------------------------------------------------------


def ordered_members(db: Session) -> list[FamilyMember]:
    """Family members in the order Rally lists them everywhere: by name."""
    return db.query(FamilyMember).order_by(FamilyMember.name.asc()).all()


def ordered_bags(db: Session) -> list[PackingListBag]:
    """The household's bags, A to Z."""
    return db.query(PackingListBag).order_by(func.lower(PackingListBag.name).asc()).all()


def bag_named(db: Session, name: str | None, *, create: bool = True) -> PackingListBag | None:
    """The bag with this name, ignoring case, made if it is new.

    Bags are typed on the item, so a new name is how a bag comes to exist; a
    name that matches one already there, in any case, is that bag. A blank
    name is no bag.
    """
    name = (name or "").strip()
    if not name:
        return None
    bag = db.query(PackingListBag).filter(func.lower(PackingListBag.name) == name.lower()).first()
    if bag is None and create:
        bag = PackingListBag(name=name)
        db.add(bag)
        db.flush()
    return bag


def clear_member(db: Session, member_id: int) -> None:
    """A family member is going: their items become Everyone's. Does not commit."""
    for model in (PackingListItem, PackingListDayItem, PackingListItemHistory):
        db.query(model).filter(model.owner_id == member_id).update(
            {model.owner_id: None}, synchronize_session=False
        )


# --- Item history ------------------------------------------------------------------


def history_key(name: str) -> str:
    return name.strip().casefold()


def record_item_history(db: Session, name: str, owner_id: int | None, bag_id: int | None) -> None:
    """Remember an item name for autocomplete. Does not commit.

    Called when an item is added, to a template or to one day. Renaming an
    item does not touch history — history records adds, the shopping list's
    rule — and the owner and bag kept are the last ones used, not the most
    common.
    """
    key = history_key(name)
    row = db.query(PackingListItemHistory).filter(PackingListItemHistory.name_key == key).first()
    if row:
        row.times_added += 1
        row.name = name
        row.owner_id = owner_id
        row.bag_id = bag_id
        row.last_added_at = now_utc()
    else:
        db.add(
            PackingListItemHistory(
                name=name,
                name_key=key,
                owner_id=owner_id,
                bag_id=bag_id,
                times_added=1,
                last_added_at=now_utc(),
            )
        )


# --- Schedules -------------------------------------------------------------------

# A runaway rule cannot loop forever: no schedule can reach more than a year of
# days in one pass, and the lookahead is a week.
_MAX_STEPS = 400


def _first_open_date(schedule: PackingListSchedule, today: date) -> date:
    """The first date a schedule may still put its packing list on.

    Never one at or before ``last_generated_date`` (that is what keeps a removed
    day from coming back), never one before today, and never one before the
    start date. A schedule that was paused, or not looked at for a while,
    carries on along its own cadence rather than restarting from today, so
    "every 2 weeks" stays on the weeks it was on.
    """
    floor = today
    if schedule.start_date:
        floor = max(floor, date.fromisoformat(schedule.start_date))
    if not schedule.last_generated_date:
        return get_first_recurrence_date(schedule, today)
    candidate = get_next_recurrence_date(schedule, date.fromisoformat(schedule.last_generated_date))
    for _ in range(_MAX_STEPS):
        if candidate >= floor:
            break
        candidate = get_next_recurrence_date(schedule, candidate)
    return candidate


def process_schedules(db: Session, today: date) -> int:
    """Put every active schedule's packing list on its days through the lookahead.

    The lookahead is ``SUMMARY_LOOKAHEAD_DAYS``, so every scheduled day the
    daily summary could mention already exists by the time it is written. A
    date the packing list is already on — added by hand — is left as it is and
    counted as generated. Commits, and returns how many days it created.
    """
    horizon = today + timedelta(days=SUMMARY_LOOKAHEAD_DAYS)
    schedules = (
        db.query(PackingListSchedule).filter(PackingListSchedule.active == True).all()  # noqa: E712
    )
    created = 0
    for schedule in schedules:
        end = date.fromisoformat(schedule.end_date) if schedule.end_date else None
        candidate = _first_open_date(schedule, today)
        for _ in range(_MAX_STEPS):
            if candidate > horizon or (end and candidate > end):
                break
            day = candidate.isoformat()
            taken = (
                db.query(PackingListDay.id)
                .filter(
                    PackingListDay.packing_list_id == schedule.packing_list_id,
                    PackingListDay.date == day,
                )
                .first()
            )
            if not taken:
                db.add(
                    PackingListDay(
                        packing_list_id=schedule.packing_list_id,
                        date=day,
                        label=schedule.label,
                        schedule_id=schedule.id,
                    )
                )
                created += 1
            schedule.last_generated_date = day
            candidate = get_next_recurrence_date(schedule, candidate)
    db.commit()
    return created


# --- The daily summary -----------------------------------------------------------


def _status(day: str, pack_on: str, today: str) -> str:
    """TODAY on the day itself; PACK TODAY from the packing day until then, so
    a list packed days ahead keeps coming up while something is still left on
    it; UPCOMING before the packing day."""
    if day == today:
        return "TODAY"
    if pack_on <= today:
        return "PACK TODAY"
    days = (date.fromisoformat(day) - date.fromisoformat(today)).days
    return f"UPCOMING (in {days} day{'s' if days != 1 else ''})"


def _open_shopping_names(db: Session) -> set[str]:
    return {
        row.name.strip().casefold()
        for row in db.query(ShoppingItem.name).filter(ShoppingItem.completed == False)  # noqa: E712
    }


def summary_text(db: Session, today: date) -> str:
    """The PACKING LISTS section of the daily summary, or ``""`` when there is none.

    Every day's packing list from today through ``SUMMARY_LOOKAHEAD_DAYS`` ahead
    that still has something unchecked, soonest first. Only the unchecked
    items are listed: the model's job is what is left to pack and what might
    be hard to get, and a checked item is neither.

    An item whose name is open on the shopping list is marked as such, so the
    model does not tell the family to buy what they have already planned to.
    The match is the item's whole name, trimmed and casefolded — the same key
    shopping history dedupes on — because a fuzzier match that told the family
    "sunscreen is handled" when it was not would be worse than no mark.
    """
    start = today.isoformat()
    end = (today + timedelta(days=SUMMARY_LOOKAHEAD_DAYS)).isoformat()
    days = (
        db.query(PackingListDay)
        .filter(PackingListDay.date >= start, PackingListDay.date <= end)
        .order_by(PackingListDay.date.asc(), PackingListDay.id.asc())
        .all()
    )
    if not days:
        return ""

    packing_lists = {
        c.id: c
        for c in db.query(PackingList).filter(PackingList.id.in_({d.packing_list_id for d in days}))
    }
    on_shopping_list = _open_shopping_names(db)
    member_names = {m.id: m.name for m in ordered_members(db)}
    bag_names = {b.id: b.name for b in ordered_bags(db)}

    blocks = []
    for day in days:
        packing_list = packing_lists.get(day.packing_list_id)
        if packing_list is None:
            continue
        items = resolve_day(db, day, ordered_items(db, packing_list.id))
        remaining = [item for item in items if not item.checked]
        if not remaining:
            continue

        lead = pack_days_before(day, packing_list)
        pack_on = pack_date(day.date, lead)
        heading = f'- "{packing_list.name}"'
        if day.label:
            heading += f" ({day.label})"
        heading += f" for {_long_date(day.date)}"
        if lead == 1:
            heading += f", packed the day before ({_long_date(pack_on)})"
        elif lead > 1:
            heading += f", packed {lead} days before ({_long_date(pack_on)})"
        heading += f" — {_status(day.date, pack_on, start)}."
        heading += f" {len(items) - len(remaining)} of {len(items)} packed."

        # Grouped by owner — who still has what to pack — in Rally's member
        # order with Everyone last, every group headed even when it is the
        # only one, as the page heads it. Each item names its bag.
        lines = [heading]
        sections = [
            (name, [i for i in remaining if i.owner_id == member_id])
            for member_id, name in member_names.items()
        ]
        sections.append((EVERYONE, [i for i in remaining if i.owner_id not in member_names]))
        for owner, section in sections:
            if not section:
                continue
            lines.append(f"  {owner}:")
            for item in section:
                entry = item.name
                if item.bag_id in bag_names:
                    entry += f" (in {bag_names[item.bag_id]})"
                if item.note:
                    entry += f" — {item.note}"
                if item.name.strip().casefold() in on_shopping_list:
                    entry += " (already on the shopping list)"
                lines.append(f"    - {entry}")
        blocks.append("\n".join(lines))

    return "\n".join(blocks)


def _long_date(value: str) -> str:
    day = date.fromisoformat(value)
    return f"{day.strftime('%A')}, {day.strftime('%B')} {day.day}"
