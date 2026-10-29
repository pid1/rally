"""Packing Lists: reusable packing lists, put on a day and checked off there.

A ``PackingListTemplate`` is the template. A ``PackingListDay`` is a packing
list on a date: it has its own name and its own items, and includes any number
of templates (``PackingListDayTemplate``, #281), each read live. The list holds
no copy of a template's items, only ``PackingListDayCheck`` rows saying which
are checked *on that list*. Two rules follow from that shape with no code to
enforce them:

* An edit to a template is on every list that includes it. There is no copy to
  sync.
* Checking an item off on one list touches nothing else. There is no column on
  the template, or on any other list, for it to write.

A ``PackingListTemplateSchedule`` puts a template on days by a repeating rule.
It does so by creating ordinary lists ahead of time, each named after the
template and including it (``process_schedules``), so everything above holds
for a scheduled day too.

A list also keeps its own changes on top of its templates
(``PackingListDayItem``): items only it has, and its edits or removals of a
template item. ``resolve_day`` is the one place that lays the overlay over the
templates, and the list's hand-arranged order over both, so the page, the
counts and the summary all read a list the same way. Two templates that both
hold "Toothbrush" put two on the list: nothing is deduplicated.

A list with no template is a list of its own items. Deleting a template turns
its items into each including list's own (``delete_template``), so a template
going never takes its history with it.

Every item has an optional owner (a family member) and an optional bag. The
page groups a packing list by either — who owns what, what goes in each bag —
over one order; the summary groups by owner and names the bag.

A bag has an owner too, and may go in another bag. Both are the household's
on the bag, and a template or a list can read them differently
(``PackingListTemplateBag``, ``PackingListDayBag``) the way a list reads an
item differently. Which bags are on a list is never stored: it is every bag
something on the list is in, and every bag those go in
(``resolve_day_bags``, ``resolve_template_bags``). A list records which of
them were grabbed (``PackingListDayBagCheck``).

Item history (``PackingListItemHistory``) is what autocomplete suggests. Typing
a name creates its row; ``count_packed_days`` counts how many past days each
name was on a list.

This module holds what the router and the daily summary share: display order,
progress counts, pack dates, the overlay, bags, item history, schedule
processing, calendar events' lists, and the summary's text.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func
from sqlalchemy.orm import Session

from rally.calendars import dates_covered, expand_event, window_bounds
from rally.models import (
    Event,
    EventOverride,
    EventPackingList,
    FamilyMember,
    PackingListBag,
    PackingListDay,
    PackingListDayBag,
    PackingListDayBagCheck,
    PackingListDayCheck,
    PackingListDayEvent,
    PackingListDayItem,
    PackingListDayTemplate,
    PackingListItemHistory,
    PackingListTemplate,
    PackingListTemplateBag,
    PackingListTemplateItem,
    PackingListTemplateSchedule,
    Setting,
    ShoppingItem,
)
from rally.recurrence import get_first_recurrence_date, get_next_recurrence_date
from rally.utils.display import WEEKDAYS, display_date
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


def day_template_ids(db: Session, day_id: int) -> list[int]:
    """The templates a list includes, in the order it reads them."""
    return [
        row.packing_list_template_id
        for row in db.query(PackingListDayTemplate)
        .filter(PackingListDayTemplate.day_id == day_id)
        .order_by(PackingListDayTemplate.position.asc(), PackingListDayTemplate.id.asc())
    ]


def day_template_map(db: Session, day_ids: list[int]) -> dict[int, list[int]]:
    """``day_template_ids`` for many lists at once (every id asked about)."""
    result: dict[int, list[int]] = {day_id: [] for day_id in day_ids}
    if not day_ids:
        return result
    rows = (
        db.query(PackingListDayTemplate)
        .filter(PackingListDayTemplate.day_id.in_(day_ids))
        .order_by(PackingListDayTemplate.position.asc(), PackingListDayTemplate.id.asc())
    )
    for row in rows:
        result.setdefault(row.day_id, []).append(row.packing_list_template_id)
    return result


def templates_by_id(db: Session, template_ids) -> dict[int, PackingListTemplate]:
    ids = set(template_ids)
    if not ids:
        return {}
    return {t.id: t for t in db.query(PackingListTemplate).filter(PackingListTemplate.id.in_(ids))}


def include_template(db: Session, day: PackingListDay, template_id: int) -> bool:
    """Include a template on a list, after the ones it has. Does not commit.
    Returns whether it was added: a template is on a list once."""
    rows = db.query(PackingListDayTemplate).filter(PackingListDayTemplate.day_id == day.id).all()
    if any(row.packing_list_template_id == template_id for row in rows):
        return False
    position = max((row.position for row in rows), default=-1) + 1
    db.add(
        PackingListDayTemplate(
            day_id=day.id, packing_list_template_id=template_id, position=position
        )
    )
    db.flush()
    return True


def exclude_template(db: Session, day: PackingListDay, template_id: int) -> None:
    """Take a template off a list: its items leave the list with their checks
    and the list's changes to them. The template is untouched. Does not
    commit. A list that followed its templates' lead time keeps the one it had
    when it is left with no template to follow."""
    before = pack_days_before(day, list(templates_by_id(db, day_template_ids(db, day.id)).values()))
    item_ids = [i.id for i in ordered_template_items(db, template_id)]
    if item_ids:
        for model in (PackingListDayCheck, PackingListDayItem):
            db.query(model).filter(
                model.day_id == day.id, model.template_item_id.in_(item_ids)
            ).delete(synchronize_session=False)
    db.query(PackingListDayTemplate).filter(
        PackingListDayTemplate.day_id == day.id,
        PackingListDayTemplate.packing_list_template_id == template_id,
    ).delete(synchronize_session=False)
    db.flush()
    if day.pack_days_before is None and not day_template_ids(db, day.id):
        day.pack_days_before = before


def pack_days_before(day: PackingListDay, templates: list[PackingListTemplate]) -> int:
    """How many days ahead a list is packed: its own number when it has one,
    otherwise the longest lead time among the templates it includes (#281).
    A list with no template always has its own."""
    if day.pack_days_before is not None or not templates:
        return day.pack_days_before or 0
    return max(t.pack_days_before for t in templates)


def pack_date(day: str, days_before: int) -> str:
    """The date a day's packing list should be packed, as ``YYYY-MM-DD``."""
    return (date.fromisoformat(day) - timedelta(days=days_before)).isoformat()


def day_description(day: PackingListDay, templates: list[PackingListTemplate]) -> str | None:
    """A list's description: its own, or, when it has none and includes one
    template, that template's."""
    if day.description or len(templates) != 1:
        return day.description
    return templates[0].description


def ordered_template_items(db: Session, template_id: int | None) -> list[PackingListTemplateItem]:
    """A template's items in their one order. Each view groups this list by
    owner or by bag and keeps the order within each group."""
    if template_id is None:
        return []
    return (
        db.query(PackingListTemplateItem)
        .filter(PackingListTemplateItem.packing_list_template_id == template_id)
        .order_by(PackingListTemplateItem.sort_order.asc(), PackingListTemplateItem.id.asc())
        .all()
    )


def list_template_items(
    db: Session, template_ids: list[int], cache: dict | None = None
) -> list[PackingListTemplateItem]:
    """Every included template's items, one template after another in the
    order the list includes them. ``cache`` (by template id) lets a template
    on many lists be read once."""
    items: list[PackingListTemplateItem] = []
    for template_id in template_ids:
        if cache is not None:
            if template_id not in cache:
                cache[template_id] = ordered_template_items(db, template_id)
            items.extend(cache[template_id])
        else:
            items.extend(ordered_template_items(db, template_id))
    return items


def day_items(db: Session, day: PackingListDay) -> list[PackingListTemplateItem]:
    """The template items a list reads, from every template it includes."""
    return list_template_items(db, day_template_ids(db, day.id))


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
    changed: bool  # A template item this list has edited
    # The template a template item comes from; None for the list's own.
    template_id: int | None = None

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
    """A list's items in reading order: each included template's, as this list
    has them, then the list's own — or the list's hand-arranged order, when it
    has one.

    ``items`` are the included templates' (``list_template_items``), in
    order, passed in so a template on several lists is read once. A day's reading of a template item carries its own owner
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
                template_id=item.packing_list_template_id,
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


@dataclass(frozen=True)
class BagOnList:
    """One bag as it reads on one list (a template or a day)."""

    id: int
    name: str
    owner_id: int | None
    parent_bag_id: int | None  # The bag it goes in on this list, always one also on it
    checked: bool  # Grabbed, on a day; always False on a template
    changed: bool  # This list reads it differently from the level above


def _bag_reading_rows(
    db: Session, *, template_id: int | None, day_id: int | None
) -> tuple[dict, dict]:
    """This list's readings of bags, by bag id: its templates' and its own.

    A template is read by itself (``template_id``). A list reads the readings
    of every template it includes (``day_id``), the first included template
    that reads a bag winning, so a list with one template reads exactly as
    that template does."""
    template_ids = (
        [template_id]
        if template_id is not None
        else (day_template_ids(db, day_id) if day_id is not None else [])
    )
    template_rows = {}
    for tid in template_ids:
        for row in db.query(PackingListTemplateBag).filter(
            PackingListTemplateBag.packing_list_template_id == tid
        ):
            template_rows.setdefault(row.bag_id, row)
    day_rows = {}
    if day_id is not None:
        day_rows = {
            row.bag_id: row
            for row in db.query(PackingListDayBag).filter(PackingListDayBag.day_id == day_id)
        }
    return template_rows, day_rows


def bag_reading(
    db: Session, bag: PackingListBag, *, template_id: int | None, day_id: int | None = None
) -> tuple[int | None, int | None]:
    """``(owner_id, parent_bag_id)`` for one bag where it is being read: the
    list's reading, else its templates', else the household's."""
    template_rows, day_rows = _bag_reading_rows(db, template_id=template_id, day_id=day_id)
    row = day_rows.get(bag.id) or template_rows.get(bag.id) or bag
    return row.owner_id, row.parent_bag_id


def readings_at(
    db: Session, *, template_id: int | None, day_id: int | None
) -> tuple[dict[int, PackingListBag], dict[int, tuple[int | None, int | None]], set[int]]:
    """Every household bag, how each reads at this level, and which this level
    reads differently. A parent that is not a bag reads as none."""
    bags = {b.id: b for b in db.query(PackingListBag).all()}
    template_rows, day_rows = _bag_reading_rows(db, template_id=template_id, day_id=day_id)
    own = day_rows if day_id is not None else template_rows
    readings = {}
    for bag_id, bag in bags.items():
        row = day_rows.get(bag_id) or template_rows.get(bag_id) or bag
        parent = row.parent_bag_id if row.parent_bag_id in bags else None
        readings[bag_id] = (row.owner_id, parent)
    return bags, readings, set(own)


def readings_at_above(db: Session, day: PackingListDay):
    """How bags read one level above a list: through its templates, else the
    household (``readings_at`` without the list's own readings)."""
    bags = {b.id: b for b in db.query(PackingListBag).all()}
    template_rows, _ = _bag_reading_rows(db, template_id=None, day_id=day.id)
    readings = {}
    for bag_id, bag in bags.items():
        row = template_rows.get(bag_id) or bag
        parent = row.parent_bag_id if row.parent_bag_id in bags else None
        readings[bag_id] = (row.owner_id, parent)
    return bags, readings, set()


def _goes_in(bag_id: int, parent_of) -> list[int]:
    """The bags ``bag_id`` goes in, innermost first. A chain that comes back
    round to a bag already on it stops there: readings at different levels
    can combine into a loop that no single write made, and a loop must read
    as a list rather than hang."""
    chain: list[int] = []
    seen = {bag_id}
    current = parent_of(bag_id)
    while current is not None and current not in seen:
        chain.append(current)
        seen.add(current)
        current = parent_of(current)
    return chain


def _bags_on_list(
    db: Session,
    item_bag_ids,
    *,
    template_id: int | None,
    day_id: int | None,
    checked: set[int],
) -> list[BagOnList]:
    """The bags on a list, outermost first and then the bags inside each, A to Z
    among bags that go in the same one.

    A bag is on the list when an item on it is in that bag, or in a bag inside
    it. A bag that is in a loop goes in nothing here, so every bag on the list
    is drawn exactly once.
    """
    bags, readings, own = readings_at(db, template_id=template_id, day_id=day_id)

    def parent_of(bag_id):
        return readings[bag_id][1]

    on_list: set[int] = set()
    for bag_id in item_bag_ids:
        if bag_id in bags:
            on_list.add(bag_id)
            on_list.update(_goes_in(bag_id, parent_of))

    def tree_parent(bag_id):
        # A parent whose own chain leads back here is a loop: none, here.
        parent = parent_of(bag_id)
        if parent is None or parent == bag_id or bag_id in _goes_in(parent, parent_of):
            return None
        return parent

    children: dict[int | None, list[int]] = {}
    for bag_id in on_list:
        children.setdefault(tree_parent(bag_id), []).append(bag_id)

    ordered: list[BagOnList] = []

    def visit(parent: int | None) -> None:
        for bag_id in sorted(children.get(parent, []), key=lambda b: bags[b].name.lower()):
            owner_id, _ = readings[bag_id]
            ordered.append(
                BagOnList(
                    id=bag_id,
                    name=bags[bag_id].name,
                    owner_id=owner_id,
                    parent_bag_id=parent,
                    checked=bag_id in checked,
                    changed=bag_id in own,
                )
            )
            visit(bag_id)

    visit(None)
    return ordered


def resolve_template_bags(
    db: Session, template_id: int, items: list[PackingListTemplateItem]
) -> list[BagOnList]:
    """A template's bags as it reads them (see ``_bags_on_list``)."""
    return _bags_on_list(
        db, {i.bag_id for i in items}, template_id=template_id, day_id=None, checked=set()
    )


def checked_bag_ids(db: Session, day_id: int) -> set[int]:
    return {
        row.bag_id
        for row in db.query(PackingListDayBagCheck.bag_id).filter(
            PackingListDayBagCheck.day_id == day_id
        )
    }


def resolve_day_bags(db: Session, day: PackingListDay, resolved: list[DayItem]) -> list[BagOnList]:
    """A day's bags as it reads them, each with whether it was grabbed.

    ``resolved`` is the day's items as ``resolve_day`` reads them, so a bag an
    item was moved out of on this day only is not on this day.
    """
    return _bags_on_list(
        db,
        {i.bag_id for i in resolved},
        template_id=None,
        day_id=day.id,
        checked=checked_bag_ids(db, day.id),
    )


def prune_bag_checks(db: Session, day: PackingListDay, bags: list[BagOnList]) -> bool:
    """Delete the grab checks of bags no longer on a day, so a bag that comes
    back comes back unchecked. Returns whether anything was deleted. Does not
    commit."""
    on_day = {bag.id for bag in bags}
    stale = [b for b in checked_bag_ids(db, day.id) if b not in on_day]
    if stale:
        db.query(PackingListDayBagCheck).filter(
            PackingListDayBagCheck.day_id == day.id, PackingListDayBagCheck.bag_id.in_(stale)
        ).delete(synchronize_session=False)
    return bool(stale)


def nests_in_itself(
    db: Session,
    bag_id: int,
    parent_bag_id: int | None,
    *,
    template_id: int | None,
    day_id: int | None,
) -> bool:
    """Whether putting ``bag_id`` in ``parent_bag_id`` at this level would put
    it inside itself: the parent is the bag, or a bag already inside it."""
    if parent_bag_id is None:
        return False
    if parent_bag_id == bag_id:
        return True
    _, readings, _ = readings_at(db, template_id=template_id, day_id=day_id)
    return bag_id in _goes_in(parent_bag_id, lambda b: readings.get(b, (None, None))[1])


def change_count(db: Session, day: PackingListDay, items: list[PackingListTemplateItem]) -> int:
    """How many template items this list differs from its templates on: items
    it edited or removed. A list's own items are its own, not changes, so
    they never count, and a list with no template always reads 0.

    An edit to an item since deleted from its template no longer counts —
    deleting the item deletes the edit too, and this is the guard if something
    left one behind."""
    present = {item.id for item in items}
    if not present:
        return 0
    rows = db.query(PackingListDayItem.template_item_id).filter(
        PackingListDayItem.day_id == day.id, PackingListDayItem.template_item_id.isnot(None)
    )
    return sum(1 for (item_id,) in rows if item_id in present)


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


def _keep_template_items(db: Session, day: PackingListDay, template: PackingListTemplate) -> None:
    """Turn one template's items on a list into the list's own, so the list
    reads the same without the template (#281's "deleting a template keeps
    today's behavior").

    Each of the template's items, as this list has it, becomes one of its own
    items with its check, in the place it read in; items the list removed stay
    gone; its own items and the other templates' stay as they are. The
    readings and checks that pointed at the template's items are cleared, and
    the template's readings of bags become the list's where it has none, so its
    bags read as they did too. A list that followed its templates' lead time
    keeps the one it had.
    """
    template_ids = day_template_ids(db, day.id)
    templates = list(templates_by_id(db, template_ids).values())
    lead = pack_days_before(day, templates)
    resolved = resolve_day(db, day, list_template_items(db, template_ids))
    own_rows = {
        row.id: row
        for row in db.query(PackingListDayItem).filter(
            PackingListDayItem.day_id == day.id, PackingListDayItem.template_item_id.is_(None)
        )
    }
    new_rows: dict[int, PackingListDayItem] = {}
    for position, item in enumerate(resolved):
        if item.source == DAY:
            own_rows[item.id].sort_order = position
            continue
        if item.template_id != template.id:
            continue
        row = PackingListDayItem(
            day_id=day.id,
            template_item_id=None,
            owner_id=item.owner_id,
            bag_id=item.bag_id,
            name=item.name,
            note=item.note,
            sort_order=position,
            checked=item.checked,
        )
        db.add(row)
        new_rows[item.id] = row
    db.flush()
    item_ids = [i.id for i in ordered_template_items(db, template.id)]
    if item_ids:
        for model in (PackingListDayItem, PackingListDayCheck):
            db.query(model).filter(
                model.day_id == day.id, model.template_item_id.in_(item_ids)
            ).delete(synchronize_session=False)
    # The list's order now names the new rows where it named the template's.
    order = [
        item_key(DAY, new_rows[item.id].id)
        if item.source == TEMPLATE and item.id in new_rows
        else item.key
        for item in resolved
    ]
    # Kept whenever it matters: a hand-arranged list, or one whose other
    # templates still read first by default, where the converted items would
    # otherwise fall to the bottom with the list's own.
    day.item_order = order if (day.item_order or len(template_ids) > 1) else None
    day_bags = {
        row.bag_id
        for row in db.query(PackingListDayBag.bag_id).filter(PackingListDayBag.day_id == day.id)
    }
    for row in db.query(PackingListTemplateBag).filter(
        PackingListTemplateBag.packing_list_template_id == template.id
    ):
        if row.bag_id not in day_bags:
            db.add(
                PackingListDayBag(
                    day_id=day.id,
                    bag_id=row.bag_id,
                    owner_id=row.owner_id,
                    parent_bag_id=row.parent_bag_id,
                )
            )
    if day.pack_days_before is None and len(template_ids) == 1:
        day.pack_days_before = lead
    if not day.description and len(template_ids) == 1:
        day.description = template.description
    db.query(PackingListDayTemplate).filter(
        PackingListDayTemplate.day_id == day.id,
        PackingListDayTemplate.packing_list_template_id == template.id,
    ).delete(synchronize_session=False)
    if day.schedule_id is not None:
        schedule = (
            db.query(PackingListTemplateSchedule)
            .filter(PackingListTemplateSchedule.id == day.schedule_id)
            .first()
        )
        if schedule is None or schedule.packing_list_template_id == template.id:
            day.schedule_id = None
    db.flush()
    if day.pack_days_before is None and not day_template_ids(db, day.id):
        day.pack_days_before = lead


def template_day_ids(db: Session, template_id: int) -> list[int]:
    """Every list that includes a template, past and upcoming."""
    return [
        row.day_id
        for row in db.query(PackingListDayTemplate.day_id).filter(
            PackingListDayTemplate.packing_list_template_id == template_id
        )
    ]


def delete_template(db: Session, template: PackingListTemplate) -> None:
    """Delete a template, its items and its schedule. Does not commit.

    Every list that includes it — past, today and upcoming — keeps it as its
    own items first (``_keep_template_items``), so deleting a template never
    erases what was packed or what is coming up. Item history is untouched: a
    day counts once its date is over (``count_packed_days``), and converting
    it changes neither its date nor what is on it.

    SQLite does not enforce the references, so the cascade is by hand — the
    same reason deleting an event removes its attendees and overrides itself.
    Bags and item history are the household's, not the template's, and stay.
    """
    # No event brings it any more.
    unlink_template(db, template.id)
    for day_id in template_day_ids(db, template.id):
        day = db.query(PackingListDay).filter(PackingListDay.id == day_id).first()
        if day is not None:
            _keep_template_items(db, day, template)
    db.flush()
    for model in (PackingListTemplateItem, PackingListTemplateSchedule, PackingListTemplateBag):
        db.query(model).filter(model.packing_list_template_id == template.id).delete(
            synchronize_session=False
        )
    db.query(PackingListDayTemplate).filter(
        PackingListDayTemplate.packing_list_template_id == template.id
    ).delete(synchronize_session=False)
    db.delete(template)


# Every table that holds something about one list only, by ``day_id``: its
# checks, its own items and changes, its readings of bags and its grab checks.
# The templates it includes (``PackingListDayTemplate``) are what it is, not
# state, and are deleted with the list itself (``delete_day``).
DAY_STATE_MODELS = (
    PackingListDayCheck,
    PackingListDayItem,
    PackingListDayBag,
    PackingListDayBagCheck,
)


def clear_day_state(db: Session, day: PackingListDay) -> None:
    """Delete everything kept about one list only (``DAY_STATE_MODELS``). Does
    not commit."""
    for model in DAY_STATE_MODELS:
        db.query(model).filter(model.day_id == day.id).delete(synchronize_session=False)


def delete_day(db: Session, day: PackingListDay) -> None:
    """Delete a list: its state, the templates it includes, and the list. Does
    not commit; the templates are untouched."""
    clear_day_state(db, day)
    db.query(PackingListDayTemplate).filter(PackingListDayTemplate.day_id == day.id).delete(
        synchronize_session=False
    )
    db.delete(day)


def _schedule_label(db: Session, day: PackingListDay) -> str | None:
    """The label the schedule that made a day gives its days now, or ``None``
    for a day added by hand (or whose schedule was deleted)."""
    if day.schedule_id is None:
        return None
    schedule = (
        db.query(PackingListTemplateSchedule)
        .filter(PackingListTemplateSchedule.id == day.schedule_id)
        .first()
    )
    return schedule.label if schedule else None


def _restore_removed_bags(db: Session, day: PackingListDay) -> None:
    """Put each bag that taking a bag off the day un-nested back in that bag,
    once the bag is on the day again. Does not commit.

    Only a reading still carrying its ``removed_from_bag_id`` is touched: any
    hand edit since cleared it. Its owner stays, and a reading that then says
    what the level above says is deleted rather than kept as a copy. A bag
    whose removed bag is still off the day keeps its reading and its record,
    and so does one whose return would put a bag inside itself.
    """
    rows = (
        db.query(PackingListDayBag)
        .filter(
            PackingListDayBag.day_id == day.id,
            PackingListDayBag.removed_from_bag_id.isnot(None),
        )
        .all()
    )
    if not rows:
        return
    resolved = resolve_day(db, day, day_items(db, day))
    on_day = {bag.id for bag in resolve_day_bags(db, day, resolved)}
    _, above, _ = readings_at_above(db, day)
    for row in rows:
        parent = row.removed_from_bag_id
        if parent not in on_day or nests_in_itself(
            db, row.bag_id, parent, template_id=None, day_id=day.id
        ):
            continue
        row.parent_bag_id = parent
        row.removed_from_bag_id = None
        if above.get(row.bag_id) == (row.owner_id, parent):
            db.delete(row)
        db.flush()


def resync_day(db: Session, day: PackingListDay, scope: str) -> None:
    """Bring a list back in line with every template it includes. Does not
    commit.

    ``items`` deletes the list's changes to template items — its edits and
    removals — so each reads as its template has it, keeping its check (a
    check names the template item, not the reading). A bag the list took off
    comes back with its items, and the bags that removal un-nested go back in
    it (``_restore_removed_bags``). The list's own items stay: they are what
    the list is, not a change to a template.

    ``all`` also clears its checks, grab checks, bag readings and order, gives
    it its templates' lead time and its schedule's label (or none), and lets a
    schedule's relabel reach it again. Its own items stay, unchecked.

    The templates, every other list and item history are untouched: a resync
    types nothing, and only a day that is over is ever counted.
    """
    db.query(PackingListDayItem).filter(
        PackingListDayItem.day_id == day.id, PackingListDayItem.template_item_id.isnot(None)
    ).delete(synchronize_session=False)
    if scope == "all":
        for model in (PackingListDayCheck, PackingListDayBag, PackingListDayBagCheck):
            db.query(model).filter(model.day_id == day.id).delete(synchronize_session=False)
        db.query(PackingListDayItem).filter(PackingListDayItem.day_id == day.id).update(
            {PackingListDayItem.checked: False}, synchronize_session=False
        )
        day.item_order = None
        day.pack_days_before = None
        day.label = _schedule_label(db, day)
        day.label_edited = False
        return
    _restore_removed_bags(db, day)


# --- Owners and bags -------------------------------------------------------------


def ordered_members(db: Session) -> list[FamilyMember]:
    """Family members in the order Rally lists them everywhere: by name."""
    return db.query(FamilyMember).order_by(FamilyMember.name.asc()).all()


def ordered_bags(db: Session) -> list[PackingListBag]:
    """The household's bags, A to Z."""
    return db.query(PackingListBag).order_by(func.lower(PackingListBag.name).asc()).all()


def bag_named(
    db: Session, name: str | None, owner_id: int | None = None, *, create: bool = True
) -> PackingListBag | None:
    """The bag a name typed on an item means, made if there is none.

    A bag is unique by name and owner, so a name can mean several bags: it
    means the one the item's owner (``owner_id``) owns, failing that the one
    nobody owns, and failing both a new bag nobody owns — matched ignoring
    case each time. "Backpack" on Emma's item is Emma's Backpack; on an item
    for Everyone it is the ownerless one, never Emma's or Jake's. A blank name
    is no bag.
    """
    name = (name or "").strip()
    if not name:
        return None
    same_name = (
        db.query(PackingListBag).filter(func.lower(PackingListBag.name) == name.lower()).all()
    )
    by_owner = {bag.owner_id: bag for bag in same_name}
    bag = (by_owner.get(owner_id) if owner_id is not None else None) or by_owner.get(None)
    if bag is None and create:
        bag = PackingListBag(name=name)
        db.add(bag)
        db.flush()
    return bag


def clear_member(db: Session, member_id: int) -> None:
    """A family member is going: their items and bags become Everyone's. Does
    not commit."""
    for model in (
        PackingListTemplateItem,
        PackingListDayItem,
        PackingListItemHistory,
        PackingListBag,
        PackingListTemplateBag,
        PackingListDayBag,
    ):
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
    contents: dict[int, list[PackingListTemplateItem]] = {}
    included = day_template_map(db, [day.id for day in days])
    for day in days:
        items = list_template_items(db, included[day.id], contents)
        for item in resolve_day(db, day, items):
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


def _template_on(db: Session, template_id: int, day: str) -> bool:
    """Whether any list on ``day`` includes the template."""
    return (
        db.query(PackingListDay.id)
        .join(PackingListDayTemplate, PackingListDayTemplate.day_id == PackingListDay.id)
        .filter(
            PackingListDayTemplate.packing_list_template_id == template_id,
            PackingListDay.date == day,
        )
        .first()
        is not None
    )


def process_schedules(db: Session, today: date) -> int:
    """Put every active schedule's template on its days through the lookahead.

    Each day it makes is an ordinary list named after the template and
    including it (a schedule still puts a single template on a day, #281).
    The lookahead is ``SUMMARY_LOOKAHEAD_DAYS``, so every scheduled day the
    daily summary could mention already exists by the time it is written. A
    date where a list already includes the template — added by hand, or by an
    event — is left as it is and counted as generated. Commits, and returns
    how many days it created.
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
            if not _template_on(db, schedule.packing_list_template_id, day):
                template = (
                    db.query(PackingListTemplate)
                    .filter(PackingListTemplate.id == schedule.packing_list_template_id)
                    .first()
                )
                if template is not None:
                    made = PackingListDay(
                        name=template.name,
                        date=day,
                        label=schedule.label,
                        schedule_id=schedule.id,
                    )
                    db.add(made)
                    db.flush()
                    include_template(db, made, template.id)
                    created += 1
            schedule.last_generated_date = day
            candidate = get_next_recurrence_date(schedule, candidate)
    db.commit()
    return created


# --- Packing lists on calendar events ----------------------------------------------
#
# An event can bring packing lists with it (``EventPackingList``): each a name
# and the templates it includes (#281), "Dad Goes to South Carolina" with
# "Dad's Overnight Bag". Picking a template on its own brings a list named
# after it, #270's shape. The days it put them on, or adopted, are recorded in
# ``PackingListDayEvent``. ``sync_event_packing_lists`` is the one place those
# rows and the days behind them are written: every event write calls it, and
# so does the daily pass, so a repeating event's lists keep being filled in.
#
# A list goes on an occurrence's **first day** only: an event over several
# days is one ongoing event, not a trip each day.

# How far past the lookahead to look for a repeating event's next occurrence,
# so a monthly event always has its next list on the page. A year covers a
# yearly event; anything rarer simply waits until it is within reach.
NEXT_OCCURRENCE_DAYS = 400


@dataclass(frozen=True)
class ListSpec:
    """A packing list an event brings: its key on the event, the name a day
    it puts it on starts with, and the templates that day includes."""

    key: str
    name: str
    template_ids: tuple[int, ...]


def list_key(name: str) -> str:
    """A list's identity on an event: its name, trimmed and casefolded, the
    key item history uses, so "Beach day" and "beach day " are one list."""
    return history_key(name)


def _spec(row: EventPackingList) -> ListSpec:
    return ListSpec(
        key=row.list_key, name=row.name or row.list_key, template_ids=tuple(row.template_ids or ())
    )


class EventPackingListError(Exception):
    """A sync that cannot go ahead without the person who asked for it."""


class AdoptNeeded(EventPackingListError):
    """Moving would put a list on a date a list of that name is already on,
    dropping the moving day's checks and changes. ``adopts`` says what, for
    the confirmation the calendar asks before trying again with
    ``adopt=True``."""

    def __init__(self, adopts: list[dict]):
        super().__init__("adopt needed")
        self.adopts = adopts


class ReleaseNeeded(EventPackingListError):
    """A write or a delete would take a list off a day nobody else uses, so
    somebody has to say whether the list stays on the day or goes with it.
    ``releases`` says which lists and days, for the question the calendar asks
    before trying again with ``release`` set; ``adopts`` carries any join the
    same save would make, so one question covers both."""

    def __init__(self, releases: list[dict], adopts: list[dict] | None = None):
        super().__init__("release needed")
        self.releases = releases
        self.adopts = adopts or []


RELEASE_KEEP = "keep"
RELEASE_REMOVE = "remove"


class MovedIntoPast(EventPackingListError):
    """An occurrence with packing lists on a day from today on was moved to a
    day that has passed."""


MOVED_INTO_PAST_MESSAGE = (
    "This event has packing lists. Remove them before moving it to a day that has passed."
)

ADDED_TO_PAST_MESSAGE = "This event has already passed, so a packing list can't be added to it."


class AddedToPast(EventPackingListError):
    """A list added to an occurrence with no date from today on for it to go on
    (#290). It would be stored and never put on a day."""


def series_lists(db: Session, event_id: int) -> set[str]:
    """The keys of the lists the series itself brings, as stored now."""
    return {spec.key for spec in series_specs(_event_rows(db, event_id))}


def occurrence_lists(db: Session, event_id: int, occurrence_date: str) -> set[str]:
    """The keys of the lists one occurrence brings, as stored now."""
    return set(occurrence_specs(_event_rows(db, event_id), occurrence_date))


def refuse_lists_added_to_the_past(
    db: Session,
    event: Event,
    added: set[str],
    today: date,
    tz: ZoneInfo,
    occurrence_date: str | None = None,
) -> None:
    """Raise ``AddedToPast`` when lists are being added where they could never
    land: to ``occurrence_date`` (one occurrence) or, without it, to a series or
    one-time event with no occurrence left whose first day is today or later.

    "A date a list could go on" is the sync's own rule (``_wanted_dates``).
    Lists the event already had are not checked: keeping them is not adding
    them, and moving them is ``MovedIntoPast``'s.
    """
    if not added:
        return
    today_iso = today.isoformat()
    overrides = db.query(EventOverride).filter(EventOverride.event_id == event.id).all()
    if occurrence_date is not None:
        bounds = [date.fromisoformat(occurrence_date)]
        for override in overrides:
            if override.occurrence_date == occurrence_date and override.start_date:
                bounds.append(date.fromisoformat(override.start_date))
                bounds.append(date.fromisoformat(override.end_date or override.start_date))
        span = date.fromisoformat(event.end_date) - date.fromisoformat(event.start_date)
        found = [
            occurrence
            for occurrence in _expand(
                event, overrides, min(bounds) - timedelta(days=1), max(bounds) + span, tz
            )
            if occurrence.occurrence_date == occurrence_date
        ]
    else:
        # From a month back, so a long event already under way is seen, and
        # far enough ahead to reach the event however far off it is: a
        # one-time event through its own end, a series through a year and a
        # bit past its own start.
        start = today - timedelta(days=31)
        end = today + timedelta(days=NEXT_OCCURRENCE_DAYS)
        if event.rrule:
            end = max(
                end, date.fromisoformat(event.start_date) + timedelta(days=NEXT_OCCURRENCE_DAYS)
            )
        else:
            start = min(start, date.fromisoformat(event.start_date))
            end = max(end, date.fromisoformat(event.end_date))
        found = _expand(event, overrides, start, end, tz)
    if not any(_wanted_dates(occurrence, today_iso) for occurrence in found):
        raise AddedToPast(ADDED_TO_PAST_MESSAGE)


def _event_rows(db: Session, event_id: int) -> list[EventPackingList]:
    return (
        db.query(EventPackingList)
        .filter(EventPackingList.event_id == event_id)
        .order_by(EventPackingList.id.asc())
        .all()
    )


def series_specs(rows: list[EventPackingList]) -> list[ListSpec]:
    """The lists every occurrence of the series brings, in the order added."""
    return [_spec(row) for row in rows if row.occurrence_date is None]


def occurrence_specs(rows: list[EventPackingList], occurrence_date: str) -> dict[str, ListSpec]:
    """One occurrence's lists, by key: the series', plus what it adds, minus
    what it leaves out."""
    wanted = {spec.key: spec for spec in series_specs(rows)}
    for row in rows:
        if row.occurrence_date != occurrence_date:
            continue
        if row.removed:
            wanted.pop(row.list_key, None)
        else:
            wanted[row.list_key] = _spec(row)
    return wanted


def _write_spec(row: EventPackingList, spec: ListSpec) -> None:
    row.name = spec.name
    row.template_ids = list(spec.template_ids)


def set_series_lists(db: Session, event_id: int, specs: list[ListSpec]) -> None:
    """Make ``specs`` the series' own lists. Does not commit.

    Each occurrence's own rows stay, the way a series edit keeps overrides. An
    occurrence row the new series makes redundant goes: an addition the series
    now has, or a removal of a list the series no longer has.
    """
    wanted = {spec.key: spec for spec in specs}
    rows = _event_rows(db, event_id)
    for row in rows:
        if row.occurrence_date is not None:
            continue
        if row.list_key in wanted:
            _write_spec(row, wanted[row.list_key])
        else:
            db.delete(row)
    # Deletes first: SQLite reuses a freed id, and an insert in the same
    # flush would then collide with the deleted row in the identity map.
    db.flush()
    have = {row.list_key for row in rows if row.occurrence_date is None}
    for key, spec in wanted.items():
        if key not in have:
            row = EventPackingList(event_id=event_id, list_key=key)
            _write_spec(row, spec)
            db.add(row)
    for row in rows:
        if row.occurrence_date is None:
            continue
        in_series = row.list_key in wanted
        if in_series != bool(row.removed):
            db.delete(row)
    db.flush()


def set_occurrence_lists(
    db: Session, event_id: int, occurrence_date: str, specs: list[ListSpec]
) -> None:
    """Make one occurrence's lists ``specs``, as differences from the
    series': an addition for each list the series lacks, a removal for each
    series list it leaves out. Does not commit."""
    rows = _event_rows(db, event_id)
    series = {spec.key for spec in series_specs(rows)}
    wanted = {spec.key: spec for spec in specs}
    for row in rows:
        if row.occurrence_date == occurrence_date:
            db.delete(row)
    db.flush()
    for key in sorted(set(wanted) - series):
        row = EventPackingList(event_id=event_id, list_key=key, occurrence_date=occurrence_date)
        _write_spec(row, wanted[key])
        db.add(row)
    for key in sorted(series - set(wanted)):
        db.add(
            EventPackingList(
                event_id=event_id, list_key=key, occurrence_date=occurrence_date, removed=True
            )
        )
    db.flush()


def split_event_packing_lists(db: Session, head_id: int, tail_id: int, split: str) -> None:
    """Carry a series' packing lists onto the tail a split made. Does not commit.

    The tail starts with the head's series lists; occurrence rows and day links
    on or after the split describe occurrences that now belong to the tail, so
    they move to it — the way the split moves overrides.
    """
    for spec in series_specs(_event_rows(db, head_id)):
        row = EventPackingList(event_id=tail_id, list_key=spec.key)
        _write_spec(row, spec)
        db.add(row)
    db.query(EventPackingList).filter(
        EventPackingList.event_id == head_id,
        EventPackingList.occurrence_date.isnot(None),
        EventPackingList.occurrence_date >= split,
    ).update({EventPackingList.event_id: tail_id}, synchronize_session=False)
    db.query(PackingListDayEvent).filter(
        PackingListDayEvent.event_id == head_id,
        PackingListDayEvent.occurrence_date >= split,
    ).update({PackingListDayEvent.event_id: tail_id}, synchronize_session=False)
    db.flush()


def _named_day(db: Session, key: str, day: str) -> PackingListDay | None:
    """The list called ``key`` (ignoring case) on ``day``, if any: the list an
    event adopts rather than putting a second one beside it."""
    for candidate in (
        db.query(PackingListDay)
        .filter(PackingListDay.date == day)
        .order_by(PackingListDay.id.asc())
        .all()
    ):
        if list_key(candidate.name) == key:
            return candidate
    return None


def _links_on_day(db: Session, day_id: int) -> int:
    return db.query(PackingListDayEvent).filter(PackingListDayEvent.day_id == day_id).count()


def _release(db: Session, link: PackingListDayEvent, keep: bool = False) -> None:
    """Drop one link, and its day with it once no event is left linked to it.
    With ``keep`` the day stays, as an ordinary day nothing moves or removes."""
    day_id = link.day_id
    db.delete(link)
    db.flush()
    if keep or day_id is None or _links_on_day(db, day_id):
        return
    day = db.query(PackingListDay).filter(PackingListDay.id == day_id).first()
    if day is not None:
        delete_day(db, day)


def _new_day(db: Session, spec: ListSpec, day: str) -> PackingListDay:
    """A list on ``day`` as an event brings it: its name, and its templates
    included, with nothing checked. It has no lead time of its own unless it
    includes no template, when it packs the day of."""
    template_ids = [
        tid for tid in spec.template_ids if tid in templates_by_id(db, spec.template_ids)
    ]
    made = PackingListDay(name=spec.name, date=day, pack_days_before=None if template_ids else 0)
    db.add(made)
    db.flush()
    for template_id in template_ids:
        include_template(db, made, template_id)
    return made


def _adopt(db: Session, target: PackingListDay, spec: ListSpec) -> None:
    """An event joins a list already there: it includes every template the
    event's list does, after its own."""
    existing = templates_by_id(db, spec.template_ids)
    for template_id in spec.template_ids:
        if template_id in existing:
            include_template(db, target, template_id)


def _place(db: Session, event_id: int, occurrence_date: str, spec: ListSpec, day: str) -> None:
    """Link an occurrence's list on ``day``: adopt the list of that name
    already there, or put a new one on that day."""
    existing = _named_day(db, spec.key, day)
    if existing is None:
        existing = _new_day(db, spec, day)
    else:
        _adopt(db, existing, spec)
    db.add(
        PackingListDayEvent(
            day_id=existing.id,
            event_id=event_id,
            occurrence_date=occurrence_date,
            list_key=spec.key,
            date=day,
        )
    )
    db.flush()


def _expand(event: Event, overrides: list, start: date, end: date, tz: ZoneInfo) -> list:
    window_start, window_end = window_bounds(start, end + timedelta(days=1), tz)
    return expand_event(
        event, overrides=overrides, window_start=window_start, window_end=window_end, local_tz=tz
    )


def _wanted_dates(occurrence, today: str) -> list[str]:
    """The date an occurrence's lists go on, from today on: its first day,
    once (#281). An event under way whose first day has passed takes none."""
    first = dates_covered(occurrence)[0]
    return [first] if first >= today else []


def _occurrences_in_play(
    db: Session, event: Event, links: list[PackingListDayEvent], today: date, tz: ZoneInfo
) -> dict[str, object]:
    """The occurrences a sync looks at, by original date.

    Every occurrence through the lookahead; for a repeating event, the next one
    even when it is further out; for a one-time event, its one occurrence
    wherever it is; and any occurrence that already has a list on a day from
    today on, so one placed as the next occurrence is not let go once the
    lookahead has moved past it. The window reaches back to wherever an
    occurrence has been moved, so a move into the past is seen as one.
    """
    overrides = db.query(EventOverride).filter(EventOverride.event_id == event.id).all()
    horizon = today + timedelta(days=SUMMARY_LOOKAHEAD_DAYS)
    linked = {link.occurrence_date for link in links}

    start = today
    end = horizon
    if not event.rrule:
        start = min(start, date.fromisoformat(event.start_date))
        end = max(end, date.fromisoformat(event.end_date))
    for override in overrides:
        if override.occurrence_date in linked and override.start_date:
            start = min(start, date.fromisoformat(override.start_date))
            end = max(end, date.fromisoformat(override.end_date or override.start_date))
    for link in links:
        end = max(end, date.fromisoformat(link.date), date.fromisoformat(link.occurrence_date))

    found = {}
    for occurrence in _expand(event, overrides, start, end, tz):
        in_reach = occurrence.start_local_date <= horizon.isoformat()
        if not event.rrule or in_reach or occurrence.occurrence_date in linked:
            found[occurrence.occurrence_date] = occurrence

    if event.rrule:
        today_iso = today.isoformat()
        upcoming = [o for o in found.values() if o.start_local_date >= today_iso]
        if not upcoming:
            later = _expand(
                event, overrides, horizon, today + timedelta(days=NEXT_OCCURRENCE_DAYS), tz
            )
            later = [o for o in later if o.start_local_date >= today_iso]
            if later:
                first = min(later, key=lambda o: o.start_local_date)
                found[first.occurrence_date] = first
    return found


def sync_event_packing_lists(
    db: Session,
    event: Event,
    today: date,
    *,
    tz: ZoneInfo,
    adopt: bool = False,
    ask_before_placing: bool = True,
    asking: Callable[[PackingListDayEvent], bool] | None = None,
    release: str | None = None,
) -> None:
    """Put an event's packing lists on its occurrences' first days, and take
    them off the days its occurrences no longer have. Does not commit;
    idempotent.

    Nothing before today is ever written. A day an event adopted goes when its
    last link does, whoever put it there first. A moved occurrence moves its
    days rather than recreating them, so checks go with them, except:

    * a list of that name is already on the new date and the day is the moving
      occurrence's alone — the event adopts the list already there and the
      moving day goes, which raises ``AdoptNeeded`` unless ``adopt``;
    * the day is shared with another event — it stays with that event, and
      this occurrence gets a day of its own on the new date.

    A list newly added to the event that would land on a date a list of that
    name is already on asks too (``AdoptNeeded``, #291): joining somebody's
    list is a decision, even with nothing checked to lose. "Newly added" means
    the event has never had a link for that list, past or present, so a title
    edit never asks about an occurrence that has merely come into the
    lookahead. The daily sync passes ``ask_before_placing=False``: nobody is
    there to answer.

    Taking a list off a day nobody else uses asks too (``ReleaseNeeded``, #313),
    for the links ``asking`` picks out: what this write itself takes off (a list
    removed, an occurrence deleted). ``release`` answers it — ``keep`` leaves
    those days as ordinary days, ``remove`` deletes them. A link let go for any
    other reason, and every link in the daily sync, goes without asking.

    Raises ``MovedIntoPast`` when an occurrence that still wants a list on a day
    from today on has been moved entirely before today. Both are raised before
    anything is written.
    """
    today_iso = today.isoformat()
    rows = _event_rows(db, event.id)
    links = (
        db.query(PackingListDayEvent)
        .filter(PackingListDayEvent.event_id == event.id, PackingListDayEvent.date >= today_iso)
        .order_by(PackingListDayEvent.date.asc(), PackingListDayEvent.id.asc())
        .all()
    )
    if not rows and not links:
        return
    occurrences = _occurrences_in_play(db, event, links, today, tz)

    current: dict[tuple[str, str], list[PackingListDayEvent]] = {}
    for link in links:
        current.setdefault((link.occurrence_date, link.list_key), []).append(link)

    wanted: dict[tuple[str, str], list[str]] = {}
    specs: dict[tuple[str, str], ListSpec] = {}
    for occurrence_date, occurrence in occurrences.items():
        dates = _wanted_dates(occurrence, today_iso)
        for key, spec in occurrence_specs(rows, occurrence_date).items():
            wanted[(occurrence_date, key)] = dates
            specs[(occurrence_date, key)] = spec

    # Plan first, so a refusal leaves nothing half done.
    releases: list[PackingListDayEvent] = []
    moves: list[tuple[PackingListDayEvent, str, str, str]] = []
    places: dict[str, list[tuple[str, str]]] = {}
    leaving: dict[str, list[PackingListDayEvent]] = {}
    passed: set[str] = set()
    for pair in sorted(set(current) | set(wanted)):
        occurrence_date, key = pair
        links_now = current.get(pair, [])
        dates = wanted.get(pair)
        if dates is None:
            for link in links_now:
                (leaving.setdefault(key, []) if link.day_id else releases).append(link)
            continue
        if not dates:
            # Wanted, and the date it could go on has passed.
            passed.add(key)
        kept = {link.date for link in links_now if link.date in dates}
        going = [link for link in links_now if link.date not in dates]
        arriving = [value for value in dates if value not in kept]
        for link, value in zip(going, arriving, strict=False):
            moves.append((link, key, occurrence_date, value))
        for link in going[len(arriving) :]:
            (leaving.setdefault(key, []) if link.day_id else releases).append(link)
        for value in arriving[len(going) :]:
            places.setdefault(key, []).append((occurrence_date, value))

    # A day leaving one occurrence and a list arriving on another, for the same
    # list, is one move: a one-time event that moves gets a new original
    # date, and so does every occurrence of a series moved to another weekday.
    # Pairing them keeps the day, and its checks, rather than starting over.
    for key, going in leaving.items():
        going.sort(key=lambda link: link.date)
        arriving = sorted(places.get(key, []), key=lambda pair: pair[1])
        for link, (occurrence_date, value) in zip(going, arriving, strict=False):
            moves.append((link, key, occurrence_date, value))
        releases.extend(going[len(arriving) :])
        places[key] = arriving[len(going) :]
        if key in passed and len(going) > len(arriving):
            raise MovedIntoPast(MOVED_INTO_PAST_MESSAGE)

    adopts = []
    for link, key, _, value in moves:
        if link.day_id is not None and _links_on_day(db, link.day_id) == 1:
            target = _named_day(db, key, value)
            if target is not None and target.id != link.day_id:
                adopts.append(_adopt_detail(db, link, target))
    if ask_before_placing:
        # Every link the event has ever had, past ones included: a series
        # that brought this list last week still brings it once those days
        # have passed, so its next occurrence is not "newly added".
        linked = {
            key
            for (key,) in db.query(PackingListDayEvent.list_key)
            .filter(PackingListDayEvent.event_id == event.id)
            .distinct()
        }
        for key, arriving in sorted(places.items()):
            if key in linked:
                continue
            for _, value in sorted(arriving, key=lambda pair: pair[1]):
                target = _named_day(db, key, value)
                if target is not None:
                    adopts.append(_place_adopt_detail(target))
    asked = _asked_releases(db, releases, asking)
    if asked and release is None:
        raise ReleaseNeeded(release_details(db, asked), [] if adopt else adopts)
    if adopts and not adopt:
        raise AdoptNeeded(adopts)

    keeping = {link.id for link in asked} if release == RELEASE_KEEP else set()
    for link in releases:
        _release(db, link, keep=link.id in keeping)
    for link, key, occurrence_date, value in moves:
        link.occurrence_date = occurrence_date
        _move(db, link, specs.get((occurrence_date, key)), value)
    for key, arriving in places.items():
        for occurrence_date, value in arriving:
            _place(db, event.id, occurrence_date, specs[(occurrence_date, key)], value)
    db.flush()


def _move(db: Session, link: PackingListDayEvent, spec: ListSpec | None, value: str) -> None:
    """Move one link, and its day when the day is this link's alone."""
    if link.day_id is None:
        link.date = value
        return
    target = _named_day(db, link.list_key, value)
    alone = _links_on_day(db, link.day_id) == 1
    if alone and target is None:
        day = db.query(PackingListDay).filter(PackingListDay.id == link.day_id).first()
        if day is not None:
            day.date = value
        link.date = value
        db.flush()
        return
    old_day_id = link.day_id
    if spec is None:
        spec = ListSpec(key=link.list_key, name=link.list_key, template_ids=())
    if target is None:
        target = _new_day(db, spec, value)
    else:
        _adopt(db, target, spec)
    link.day_id = target.id
    link.date = value
    db.flush()
    if alone:
        old = db.query(PackingListDay).filter(PackingListDay.id == old_day_id).first()
        if old is not None:
            delete_day(db, old)


def _adopt_detail(db: Session, link: PackingListDayEvent, target: PackingListDay) -> dict:
    return {
        "name": target.name,
        "from_date": link.date,
        "to_date": target.date,
        "checked": _day_checked(db, link.day_id),
    }


def _day_checked(db: Session, day_id: int) -> bool:
    """Whether anything on a day is checked: an item, its own item or a bag."""
    checked = (
        db.query(PackingListDayCheck).filter(PackingListDayCheck.day_id == day_id).count()
        + db.query(PackingListDayItem)
        .filter(PackingListDayItem.day_id == day_id, PackingListDayItem.checked == True)  # noqa: E712
        .count()
        + db.query(PackingListDayBagCheck).filter(PackingListDayBagCheck.day_id == day_id).count()
    )
    return checked > 0


def _place_adopt_detail(target: PackingListDay) -> dict:
    """A newly added list joining a list already there: nothing comes from an
    earlier day, so nothing checked can be lost."""
    return {"name": target.name, "from_date": None, "to_date": target.date, "checked": False}


def adopt_message(adopts: list[dict]) -> str:
    """The calendar's confirmation before an event joins a list already there.
    A move uses that list *instead* of its own day; a newly added list (no
    ``from_date``) simply uses it."""
    return adopt_sentences(adopts) + " Continue?"


def adopt_sentences(adopts: list[dict]) -> str:
    """``adopt_message`` without its question, for a question that asks more."""
    sentences = []
    for adopt in adopts:
        sentence = (
            f'"{adopt["name"]}" is already on {display_date(adopt["to_date"])}. '
            "This event will use that list"
        )
        if adopt["from_date"] is not None:
            sentence += " instead"
        if adopt["checked"]:
            sentence += f", and what was checked on {display_date(adopt['from_date'])} will be lost"
        sentences.append(sentence + ".")
    return " ".join(sentences)


def _asked_releases(
    db: Session,
    releases: list[PackingListDayEvent],
    asking: Callable[[PackingListDayEvent], bool] | None,
) -> list[PackingListDayEvent]:
    """The releases that would delete a day, among those ``asking`` picks out.
    A link with no day, or one whose day another link keeps, loses nothing."""
    if asking is None:
        return []
    going = {link.id for link in releases}
    asked = []
    for link in releases:
        if link.day_id is None or not asking(link):
            continue
        others = (
            db.query(PackingListDayEvent.id).filter(PackingListDayEvent.day_id == link.day_id).all()
        )
        if all(other_id in going for (other_id,) in others):
            asked.append(link)
    return asked


def release_details(db: Session, links: list[PackingListDayEvent]) -> list[dict]:
    """What each day a release would delete is: its list's name, its date,
    whether anything on it is checked, and whether a schedule made it (which
    will not make it again). One entry per day, soonest first."""
    details = {}
    for link in links:
        if link.day_id in details:
            continue
        day = db.query(PackingListDay).filter(PackingListDay.id == link.day_id).first()
        if day is None:
            continue
        details[day.id] = {
            "day_id": day.id,
            "name": day.name or "",
            "date": day.date,
            "checked": _day_checked(db, day.id),
            "scheduled": day.schedule_id is not None,
        }
    return sorted(details.values(), key=lambda detail: (detail["date"], detail["name"].lower()))


def _join_words(words: list[str]) -> str:
    if len(words) <= 2:
        return " and ".join(words)
    return ", ".join(words[:-1]) + ", and " + words[-1]


def release_question(releases: list[dict], *, dated: bool = True, named: bool = False) -> dict:
    """The words of the Keep or Remove question (#313): ``message`` says which
    lists are on which days, and ``keep_label`` / ``remove_label`` are its two
    choices. ``dated=False`` speaks of a series' upcoming days without listing
    them; ``named`` names the lists in the choices rather than saying "it",
    for a question that is also about another list."""
    names: list[str] = []
    dates_by_name: dict[str, list[str]] = {}
    for release in releases:
        if release["name"] not in dates_by_name:
            names.append(release["name"])
        dates_by_name.setdefault(release["name"], []).append(release["date"])
    one = len(names) == 1
    if dated:
        # Lists on the same days share a sentence.
        groups: dict[tuple[str, ...], list[str]] = {}
        for name in names:
            groups.setdefault(tuple(sorted(set(dates_by_name[name]))), []).append(name)
        message = " ".join(
            f"{join_names(group)} {'is' if len(group) == 1 else 'are'} on "
            f"{_join_words([display_date(value) for value in dates])}."
            for dates, group in groups.items()
        )
    else:
        message = f"{join_names(names)} {'is' if one else 'are'} on its upcoming days."
    # A schedule's day does not come back once it is removed (its high-water
    # mark is past it), so the question says which lists one made.
    scheduled = [
        name for name in names if any(r["scheduled"] for r in releases if r["name"] == name)
    ]
    if one and scheduled:
        message += " Its schedule put it there, and won't put it back if it's removed."
    elif scheduled:
        message += (
            f" {join_names(scheduled)} was put there by its schedule."
            if len(scheduled) == 1
            else f" {join_names(scheduled)} were put there by their schedules."
        )
    all_dates = {release["date"] for release in releases}
    if dated and len(all_dates) == 1:
        place = f"on {WEEKDAYS[date.fromisoformat(all_dates.pop()).weekday()]}"
    else:
        place = "on its days" if one else "on their days"
    subject = join_names(names) if named else ("it" if one else "them")
    pronoun = "it" if one else "them"
    return {
        "message": message,
        "keep_label": f"Keep {subject} {place}",
        "remove_label": f"Remove {subject}, with what was checked on {pronoun}",
    }


def release_event_packing_lists(
    db: Session, event_id: int, today: date, release: str | None = None
) -> None:
    """An event is being deleted: take its lists off every day from today on
    (each day going once no event is left linked to it), then drop everything
    the event kept. Past days stay, as ordinary days. Does not commit.

    A day nobody else uses is a question (``ReleaseNeeded``) until ``release``
    answers it: ``keep`` leaves it as an ordinary day, ``remove`` deletes it."""
    today_iso = today.isoformat()
    links = (
        db.query(PackingListDayEvent)
        .filter(PackingListDayEvent.event_id == event_id, PackingListDayEvent.date >= today_iso)
        .all()
    )
    asked = _asked_releases(db, links, lambda link: True)
    if asked and release is None:
        raise ReleaseNeeded(release_details(db, asked))
    for link in links:
        _release(db, link, keep=release == RELEASE_KEEP)
    db.query(PackingListDayEvent).filter(PackingListDayEvent.event_id == event_id).delete(
        synchronize_session=False
    )
    db.query(EventPackingList).filter(EventPackingList.event_id == event_id).delete(
        synchronize_session=False
    )
    db.flush()


def unlink_day(db: Session, day: PackingListDay) -> None:
    """A list is being taken off on the Packing Lists page: unlink it from
    every event linked to it, so none of them puts it back. Does not commit.

    The occurrence leaves the list out: its own addition is dropped, or a
    removal is written; on a one-time event the event's own row for the list
    goes.
    """
    for link in db.query(PackingListDayEvent).filter(PackingListDayEvent.day_id == day.id).all():
        event = db.query(Event).filter(Event.id == link.event_id).first()
        if event is not None:
            _leave_out(db, event, link.occurrence_date, link.list_key)
        db.delete(link)
    db.flush()


def _leave_out(db: Session, event: Event, occurrence_date: str, key: str) -> None:
    rows = _event_rows(db, event.id)
    if not event.rrule:
        for row in rows:
            if row.list_key == key:
                db.delete(row)
        return
    own = [row for row in rows if row.occurrence_date == occurrence_date and row.list_key == key]
    for row in own:
        db.delete(row)
    if key in {spec.key for spec in series_specs(rows)}:
        db.add(
            EventPackingList(
                event_id=event.id, list_key=key, occurrence_date=occurrence_date, removed=True
            )
        )


def unlink_template(db: Session, template_id: int) -> None:
    """A template is being deleted: no event's list includes it any more. A
    list left with no template is no longer brought by any event, and the days
    it was on stop moving with the event (they keep the template's items as
    their own, ``_keep_template_items``). Does not commit."""
    rows = db.query(EventPackingList).filter(EventPackingList.removed == False).all()  # noqa: E712
    emptied: set[tuple[int, str]] = set()
    for row in rows:
        if template_id in (row.template_ids or []) and len(row.template_ids) == 1:
            emptied.add((row.event_id, row.list_key))
    for row in rows:
        if (row.event_id, row.list_key) in emptied:
            continue
        if template_id in (row.template_ids or []):
            row.template_ids = [tid for tid in row.template_ids if tid != template_id]
    db.flush()
    for event_id, key in emptied:
        for model in (EventPackingList, PackingListDayEvent):
            db.query(model).filter(model.event_id == event_id, model.list_key == key).delete(
                synchronize_session="fetch"
            )
    db.flush()


def rename_template_lists(db: Session, template: PackingListTemplate, old_name: str) -> None:
    """A template was renamed: every list named after it — still called its
    old name and including it — takes the new name, and so does every event
    list named after it, so a list made by picking a bare template keeps
    reading as that template, as it did before #281. A list somebody named
    for itself keeps its name. Does not commit."""
    old_key = list_key(old_name)
    if old_key == list_key(template.name) and old_name == template.name:
        return
    for day_id in template_day_ids(db, template.id):
        day = db.query(PackingListDay).filter(PackingListDay.id == day_id).first()
        if day is not None and list_key(day.name) == old_key:
            day.name = template.name
    for row in db.query(EventPackingList).filter(EventPackingList.name.isnot(None)).all():
        if template.id in (row.template_ids or []) and list_key(row.name) == old_key:
            row.name = template.name


def join_names(names: list[str]) -> str:
    """Names in a sentence, each in straight double quotes, with the Oxford
    comma: ``"A"``, ``"A" and "B"``, ``"A", "B", and "C"``. Quoting is what keeps
    a title with "and" in it from reading as two."""
    quoted = [f'"{name}"' for name in names]
    if len(quoted) <= 2:
        return " and ".join(quoted)
    return ", ".join(quoted[:-1]) + ", and " + quoted[-1]


def process_event_packing_lists(db: Session, today: date, tz: ZoneInfo) -> int:
    """The daily pass: sync every event that brings a packing list, so a
    repeating event's lists are filled in as the lookahead moves forward.
    Commits. A sync that would need somebody's answer is skipped and logged
    rather than failing a read; it cannot arise from time passing alone.
    Returns how many events were synced."""
    event_ids = sorted({row.event_id for row in db.query(EventPackingList.event_id).all()})
    synced = 0
    for event_id in event_ids:
        event = db.query(Event).filter(Event.id == event_id).first()
        if event is None:
            continue
        try:
            # A refusal is raised before anything is written, so there is
            # nothing to roll back. Nobody is here to answer, so a list
            # arriving on a day already there joins it without asking.
            sync_event_packing_lists(db, event, today, tz=tz, ask_before_placing=False)
            synced += 1
        except EventPackingListError as exc:
            print(f"Packing lists for event {event_id} not synced: {exc}")
    db.commit()
    return synced


def day_events(db: Session, day_ids: list[int]) -> dict[int, list[dict]]:
    """The events linked to each day, by occurrence: ``{event_id, title,
    occurrence_date}``, an occurrence's own title winning over the series'."""
    if not day_ids:
        return {}
    links = (
        db.query(PackingListDayEvent)
        .filter(PackingListDayEvent.day_id.in_(day_ids))
        .order_by(PackingListDayEvent.id.asc())
        .all()
    )
    if not links:
        return {}
    event_ids = {link.event_id for link in links}
    titles = {
        event.id: event.title for event in db.query(Event).filter(Event.id.in_(event_ids)).all()
    }
    retitled = {
        (override.event_id, override.occurrence_date): override.title
        for override in db.query(EventOverride)
        .filter(EventOverride.event_id.in_(event_ids), EventOverride.title.isnot(None))
        .all()
    }
    result: dict[int, list[dict]] = {}
    seen: set[tuple[int, int, str]] = set()
    for link in links:
        key = (link.day_id, link.event_id, link.occurrence_date)
        if key in seen or link.event_id not in titles:
            continue
        seen.add(key)
        title = retitled.get((link.event_id, link.occurrence_date)) or titles[link.event_id]
        result.setdefault(link.day_id, []).append(
            {"event_id": link.event_id, "title": title, "occurrence_date": link.occurrence_date}
        )
    return result


def occurrence_packing_lists(
    db: Session, pairs: list[tuple[int, str]]
) -> dict[tuple[int, str], list[dict]]:
    """Each native occurrence's packing lists, A–Z: ``{key, name,
    packing_list_template_ids, day_ids}``. An occurrence the sync has not
    reached yet still names its lists, with no days. The name is the list's
    on its day when there is one (it can be renamed there), else the event's."""
    event_ids = {event_id for event_id, _ in pairs}
    if not event_ids:
        return {}
    rows_by_event: dict[int, list[EventPackingList]] = {}
    for row in (
        db.query(EventPackingList)
        .filter(EventPackingList.event_id.in_(event_ids))
        .order_by(EventPackingList.id.asc())
        .all()
    ):
        rows_by_event.setdefault(row.event_id, []).append(row)
    if not rows_by_event:
        return {}
    days: dict[tuple[int, str, str], list[int]] = {}
    for link in db.query(PackingListDayEvent).filter(
        PackingListDayEvent.event_id.in_(rows_by_event), PackingListDayEvent.day_id.isnot(None)
    ):
        days.setdefault((link.event_id, link.occurrence_date, link.list_key), []).append(
            link.day_id
        )
    day_names = {
        day.id: day.name
        for day in db.query(PackingListDay).filter(
            PackingListDay.id.in_({day_id for ids in days.values() for day_id in ids})
        )
    }
    result = {}
    for event_id, occurrence_date in pairs:
        rows = rows_by_event.get(event_id)
        if not rows:
            continue
        lists = []
        for key, spec in occurrence_specs(rows, occurrence_date).items():
            day_ids = sorted(days.get((event_id, occurrence_date, key), []))
            name = next((day_names[d] for d in day_ids if d in day_names), spec.name)
            lists.append(
                {
                    "key": key,
                    "name": name,
                    "packing_list_template_ids": list(spec.template_ids),
                    "day_ids": day_ids,
                }
            )
        result[(event_id, occurrence_date)] = sorted(lists, key=lambda item: item["name"].lower())
    return result


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
    that still has something unchecked, soonest first — whether its items come
    from one template, several, or none (#281). Only
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

    included = day_template_map(db, [d.id for d in days])
    templates = templates_by_id(db, {tid for ids in included.values() for tid in ids})
    contents: dict[int, list[PackingListTemplateItem]] = {}
    on_shopping_list = _open_shopping_names(db)
    member_names = {m.id: m.name for m in ordered_members(db)}
    bag_names = {b.id: b.name for b in ordered_bags(db)}

    blocks = []
    for day in days:
        # A template id that is gone is skipped: deleting a template converts
        # its items first, so this is a guard, not a path.
        template_ids = [tid for tid in included[day.id] if tid in templates]
        items = resolve_day(db, day, list_template_items(db, template_ids, contents))
        remaining = [item for item in items if not item.checked]
        if not remaining:
            continue

        lead = pack_days_before(day, [templates[tid] for tid in template_ids])
        pack_on = pack_date(day.date, lead)
        heading = f'- "{day.name}"'
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
