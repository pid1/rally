"""Packing Lists: reusable packing lists, put on a day and checked off there.

A ``PackingListTemplate`` is the template. A ``PackingListDay`` puts it on a
date. The day holds no copy of the template's items, only
``PackingListDayCheck`` rows saying which are checked *on that day*. Two rules
follow from that shape with no code to enforce them:

* An edit to the template is on every day it is on. There is no copy to sync.
* Checking an item off on one day touches nothing else. There is no column on
  the template, or on any other day, for it to write.

A ``PackingListTemplateSchedule`` puts a template on days by a repeating rule.
It does so by creating ordinary ``PackingListDay`` rows ahead of time
(``process_schedules``), so everything above holds for a scheduled day too.

A day also keeps its own changes on top of the template (``PackingListDayItem``):
items added to that day only, and that day's edits or removals of a template
item. ``resolve_day`` is the one place that lays the overlay over the template,
and the day's hand-arranged order over both, so the page, the counts and the
summary all read a day the same way.

A day can also have no template at all: a **templateless** day, which has its
own name, description and lead time, and only its own items. Deleting a
template turns each of its days into one (``delete_template``), so a template
going never takes its history with it.

Every item has an optional owner (a family member) and an optional bag. The
page groups a packing list by either — who owns what, what goes in each bag —
over one order; the summary groups by owner and names the bag.

Item history (``PackingListItemHistory``) is what autocomplete suggests. Typing
a name creates its row; ``count_packed_days`` counts how many past days each
name was on a list.

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
    PackingListBag,
    PackingListDay,
    PackingListDayCheck,
    PackingListDayItem,
    PackingListItemHistory,
    PackingListTemplate,
    PackingListTemplateItem,
    PackingListTemplateSchedule,
    Setting,
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

# The two kinds of item on a day. They come from different tables, so an id
# alone does not say which: the API, ``item_order`` and the page all carry
# the source beside it.
TEMPLATE = "template"
DAY = "day"


def item_key(source: str, item_id: int) -> str:
    """An item's key in ``PackingListDay.item_order``: ``template:12``, ``day:4``."""
    return f"{source}:{item_id}"


def pack_days_before(day: PackingListDay, template: PackingListTemplate | None) -> int:
    """How many days ahead this day's packing list is packed: the day's own
    number when it has one, otherwise its template's. A templateless day
    always has its own."""
    if day.pack_days_before is not None or template is None:
        return day.pack_days_before or 0
    return template.pack_days_before


def pack_date(day: str, days_before: int) -> str:
    """The date a day's packing list should be packed, as ``YYYY-MM-DD``."""
    return (date.fromisoformat(day) - timedelta(days=days_before)).isoformat()


def day_name(day: PackingListDay, template: PackingListTemplate | None) -> str:
    """What a day's packing list is called: its template's name, or its own."""
    return template.name if template is not None else (day.name or "")


def day_description(day: PackingListDay, template: PackingListTemplate | None) -> str | None:
    return template.description if template is not None else day.description


def ordered_template_items(db: Session, template_id: int | None) -> list[PackingListTemplateItem]:
    """A template's items in their one order. Each view groups this list by
    owner or by bag and keeps the order within each group.

    ``None`` — a templateless day's template — has no items, said here rather
    than left to a query that happens to match nothing.
    """
    if template_id is None:
        return []
    return (
        db.query(PackingListTemplateItem)
        .filter(PackingListTemplateItem.packing_list_template_id == template_id)
        .order_by(PackingListTemplateItem.sort_order.asc(), PackingListTemplateItem.id.asc())
        .all()
    )


def template_bottom_position(db: Session, template_id: int) -> int:
    """The position after every item on a template: where a new one goes."""
    highest = (
        db.query(func.max(PackingListTemplateItem.sort_order))
        .filter(PackingListTemplateItem.packing_list_template_id == template_id)
        .scalar()
    )
    return 0 if highest is None else highest + 1


def checked_template_item_ids(db: Session, day_id: int) -> set[int]:
    return {
        row.template_item_id
        for row in db.query(PackingListDayCheck.template_item_id).filter(
            PackingListDayCheck.day_id == day_id
        )
    }


@dataclass(frozen=True)
class DayItem:
    """One item as it reads on one day, after the day's own changes."""

    id: int  # The template item's id, or the day item's id when source is "day"
    source: str  # TEMPLATE or DAY
    owner_id: int | None
    bag_id: int | None
    name: str
    note: str | None
    sort_order: int
    checked: bool
    changed: bool  # A template item this day has edited

    @property
    def key(self) -> str:
        return item_key(self.source, self.id)


def _apply_item_order(items: list[DayItem], order: list | None) -> list[DayItem]:
    """Items in a day's hand-arranged order.

    Nothing enforces what ``item_order`` holds, so it is read leniently: a key
    whose item is gone (deleted, or removed from this day) is skipped, a key
    listed twice counts where it first appears, and an item the list does not
    name — one added after the day was arranged — reads after everything it
    does name, in the default order.
    """
    if not order:
        return items
    by_key = {item.key: item for item in items}
    arranged: list[DayItem] = []
    seen: set[str] = set()
    for key in order:
        if isinstance(key, str) and key in by_key and key not in seen:
            arranged.append(by_key[key])
            seen.add(key)
    arranged.extend(item for item in items if item.key not in seen)
    return arranged


def resolve_day(
    db: Session, day: PackingListDay, items: list[PackingListTemplateItem]
) -> list[DayItem]:
    """A day's items in reading order: the template's, as this day has them,
    then the day's own — or the day's hand-arranged order, when it has one.

    ``items`` are the template's, in order, passed in so a template on several
    days is read once. A day's reading of a template item carries its own owner
    and bag along with its name and note, and keeps its place in the order.
    """
    overlay = db.query(PackingListDayItem).filter(PackingListDayItem.day_id == day.id).all()
    changes = {row.template_item_id: row for row in overlay if row.template_item_id is not None}
    checked = checked_template_item_ids(db, day.id)

    resolved: list[DayItem] = []
    for item in items:
        change = changes.get(item.id)
        if change and change.removed:
            continue
        source = change or item
        resolved.append(
            DayItem(
                id=item.id,
                source=TEMPLATE,
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
        (row for row in overlay if row.template_item_id is None),
        key=lambda r: (r.sort_order, r.id),
    )
    for row in own:
        resolved.append(
            DayItem(
                id=row.id,
                source=DAY,
                owner_id=row.owner_id,
                bag_id=row.bag_id,
                name=row.name,
                note=row.note,
                sort_order=row.sort_order,
                checked=row.checked,
                changed=False,
            )
        )
    return _apply_item_order(resolved, day.item_order)


def change_count(db: Session, day: PackingListDay, items: list[PackingListTemplateItem]) -> int:
    """How many items this day differs from its template on: items it edited,
    removed, or added for itself. A templateless day has no template to differ
    from, so it is always 0.

    An edit to an item since deleted from the template no longer counts —
    deleting the item deletes the edit too, and this is the guard if something
    left one behind."""
    if day.packing_list_template_id is None:
        return 0
    present = {item.id for item in items}
    rows = db.query(PackingListDayItem.template_item_id).filter(PackingListDayItem.day_id == day.id)
    return sum(1 for (item_id,) in rows if item_id is None or item_id in present)


def template_item_counts(db: Session, template_ids: list[int]) -> dict[int, int]:
    """Items per template, for every id asked about (zero included)."""
    counts = dict.fromkeys(template_ids, 0)
    if template_ids:
        rows = (
            db.query(
                PackingListTemplateItem.packing_list_template_id,
                func.count(PackingListTemplateItem.id),
            )
            .filter(PackingListTemplateItem.packing_list_template_id.in_(template_ids))
            .group_by(PackingListTemplateItem.packing_list_template_id)
        )
        counts.update(dict(rows.all()))
    return counts


def _make_templateless(
    db: Session, day: PackingListDay, template: PackingListTemplate, items: list
) -> None:
    """Copy a template onto one of its days, so the day reads the same without it.

    The day keeps exactly what ``resolve_day`` shows: each template item, as
    this day has it, becomes one of the day's own items with its check; items
    the day removed stay gone; its own items stay; the order it reads in is
    written into ``sort_order``. Then everything that pointed at the template's
    items — readings, checks, the arranged order — is cleared, since nothing is
    left for it to point at.
    """
    resolved = resolve_day(db, day, items)
    own_rows = {
        row.id: row
        for row in db.query(PackingListDayItem).filter(
            PackingListDayItem.day_id == day.id, PackingListDayItem.template_item_id.is_(None)
        )
    }
    for position, item in enumerate(resolved):
        if item.source == DAY:
            own_rows[item.id].sort_order = position
            continue
        db.add(
            PackingListDayItem(
                day_id=day.id,
                template_item_id=None,
                owner_id=item.owner_id,
                bag_id=item.bag_id,
                name=item.name,
                note=item.note,
                sort_order=position,
                checked=item.checked,
            )
        )
    db.query(PackingListDayItem).filter(
        PackingListDayItem.day_id == day.id, PackingListDayItem.template_item_id.isnot(None)
    ).delete(synchronize_session=False)
    db.query(PackingListDayCheck).filter(PackingListDayCheck.day_id == day.id).delete(
        synchronize_session=False
    )
    day.pack_days_before = pack_days_before(day, template)
    day.name = template.name
    day.description = template.description
    day.packing_list_template_id = None
    day.schedule_id = None
    day.item_order = None


def delete_template(db: Session, template: PackingListTemplate) -> None:
    """Delete a template, its items and its schedule. Does not commit.

    Every day it is on — past, today and upcoming — is kept, made templateless
    first (``_make_templateless``), so deleting a template never erases what
    was packed or what is coming up. Item history is untouched: a day counts
    once its date is over (``count_packed_days``), and converting it changes
    neither its date nor what is on it.

    SQLite does not enforce the references, so the cascade is by hand — the
    same reason deleting an event removes its attendees and overrides itself.
    Bags and item history are the household's, not the template's, and stay.
    """
    items = ordered_template_items(db, template.id)
    days = db.query(PackingListDay).filter(PackingListDay.packing_list_template_id == template.id)
    for day in days.all():
        _make_templateless(db, day, template, items)
    db.flush()
    for model in (PackingListTemplateItem, PackingListTemplateSchedule):
        db.query(model).filter(model.packing_list_template_id == template.id).delete(
            synchronize_session=False
        )
    db.delete(template)


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
    for model in (PackingListTemplateItem, PackingListDayItem, PackingListItemHistory):
        db.query(model).filter(model.owner_id == member_id).update(
            {model.owner_id: None}, synchronize_session=False
        )


# --- Item history ------------------------------------------------------------------

# The last date ``count_packed_days`` has counted, as local ``YYYY-MM-DD``.
# Internal bookkeeping, never shown — like ``shopping_last_purge_date``.
COUNTED_THROUGH_SETTING = "packing_history_counted_through"


def history_key(name: str) -> str:
    return name.strip().casefold()


def record_item_history(db: Session, name: str, owner_id: int | None, bag_id: int | None) -> None:
    """Remember a name somebody typed, for autocomplete. Does not commit.

    Called when an item is added (to a template or to one day) and when one
    is renamed. A new name starts at 0 — it is suggested at once, and
    ``count_packed_days`` counts it once a day it was on is over. An existing
    row takes the casing, owner and bag typed now; its count is left alone,
    because typing a name is not packing it.
    """
    key = history_key(name)
    row = db.query(PackingListItemHistory).filter(PackingListItemHistory.name_key == key).first()
    if row:
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
                times_added=0,
                last_added_at=now_utc(),
            )
        )


def record_rename(
    db: Session, old_name: str, new_name: str, owner_id: int | None, bag_id: int | None
) -> None:
    """A rename is typing a name: record the new one, unless only its case
    or spacing changed — that is the same name, and history keys ignore both."""
    if history_key(old_name) != history_key(new_name):
        record_item_history(db, new_name, owner_id, bag_id)


def count_packed_days(db: Session, today: date) -> int:
    """Count every day that is over and not yet counted. Commits; returns how
    many days it counted.

    Each item on such a day, as ``resolve_day`` reads it, adds 1 to its name's
    ``times_added``: the day's own additions count, items it removed do not,
    an item renamed on the day counts under that name, and checked or not
    makes no difference — the count is what was on the list. Only names with
    a history row count; nothing here creates one, so a suggestion somebody
    forgot stays forgotten.

    ``COUNTED_THROUGH_SETTING`` is the high-water mark: days after it and
    before today are counted, then it moves to yesterday in the same commit,
    so a day counts exactly once and a quiet week is caught up in one pass.
    Without a marker (a database newer than this feature) it starts at
    yesterday and counts nothing that came before.
    """
    yesterday = (today - timedelta(days=1)).isoformat()
    marker = db.query(Setting).filter(Setting.key == COUNTED_THROUGH_SETTING).first()
    if marker is None:
        db.add(Setting(key=COUNTED_THROUGH_SETTING, value=yesterday))
        db.commit()
        return 0
    if marker.value >= yesterday:
        return 0

    days = (
        db.query(PackingListDay)
        .filter(PackingListDay.date > marker.value, PackingListDay.date <= yesterday)
        .order_by(PackingListDay.date.asc(), PackingListDay.id.asc())
        .all()
    )
    counts: dict[str, int] = {}
    contents: dict[int | None, list[PackingListTemplateItem]] = {}
    for day in days:
        template_id = day.packing_list_template_id
        if template_id not in contents:
            contents[template_id] = ordered_template_items(db, template_id)
        for item in resolve_day(db, day, contents[template_id]):
            key = history_key(item.name)
            counts[key] = counts.get(key, 0) + 1
    if counts:
        rows = db.query(PackingListItemHistory).filter(
            PackingListItemHistory.name_key.in_(list(counts))
        )
        for row in rows:
            row.times_added += counts[row.name_key]
    marker.value = yesterday
    db.commit()
    return len(days)


# --- Schedules -------------------------------------------------------------------

# A runaway rule cannot loop forever: no schedule can reach more than a year of
# days in one pass, and the lookahead is a week.
_MAX_STEPS = 400


def _first_open_date(schedule: PackingListTemplateSchedule, today: date) -> date:
    """The first date a schedule may still put its template on.

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
    """Put every active schedule's template on its days through the lookahead.

    The lookahead is ``SUMMARY_LOOKAHEAD_DAYS``, so every scheduled day the
    daily summary could mention already exists by the time it is written. A
    date the template is already on — added by hand — is left as it is and
    counted as generated. Commits, and returns how many days it created.
    """
    horizon = today + timedelta(days=SUMMARY_LOOKAHEAD_DAYS)
    schedules = (
        db.query(PackingListTemplateSchedule)
        .filter(PackingListTemplateSchedule.active == True)  # noqa: E712
        .all()
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
                    PackingListDay.packing_list_template_id == schedule.packing_list_template_id,
                    PackingListDay.date == day,
                )
                .first()
            )
            if not taken:
                db.add(
                    PackingListDay(
                        packing_list_template_id=schedule.packing_list_template_id,
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
    that still has something unchecked, soonest first — templated or not. Only
    the unchecked items are listed, in the day's reading order: the model's job
    is what is left to pack and what might be hard to get, and a checked item
    is neither.

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

    templates = {
        t.id: t
        for t in db.query(PackingListTemplate).filter(
            PackingListTemplate.id.in_({d.packing_list_template_id for d in days})
        )
    }
    on_shopping_list = _open_shopping_names(db)
    member_names = {m.id: m.name for m in ordered_members(db)}
    bag_names = {b.id: b.name for b in ordered_bags(db)}

    blocks = []
    for day in days:
        template = templates.get(day.packing_list_template_id)
        if day.packing_list_template_id is not None and template is None:
            # A day pointing at a template that is gone. Deleting a template
            # makes its days templateless first, so this is a guard, not a path.
            continue
        items = resolve_day(db, day, ordered_template_items(db, day.packing_list_template_id))
        remaining = [item for item in items if not item.checked]
        if not remaining:
            continue

        lead = pack_days_before(day, template)
        pack_on = pack_date(day.date, lead)
        heading = f'- "{day_name(day, template)}"'
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
