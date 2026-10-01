"""Checklists: reusable packing lists, put on a day and checked off there.

A ``Checklist`` is the master. A ``ChecklistDay`` puts it on a date. The day
holds no items of its own, only ``ChecklistDayCheck`` rows saying which of the
checklist's items are checked *on that day*. Two rules follow from that shape
with no code to enforce them:

* An edit to the checklist is on every day it is on. There is no copy to sync.
* Checking an item off on one day touches nothing else. There is no column on
  the checklist, or on any other day, for it to write.

This module holds what the router and the daily summary share: display order,
progress counts, pack dates, and the summary's text.
"""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from rally.models import (
    Checklist,
    ChecklistDay,
    ChecklistDayCheck,
    ChecklistGroup,
    ChecklistItem,
    ShoppingItem,
)

# What the catch-all group (``group_id IS NULL``) is called wherever it is named.
GENERAL = "General"

# How far ahead the daily summary looks. A week is enough notice to restock or
# order something before a trip, and short enough that the section stays small.
SUMMARY_LOOKAHEAD_DAYS = 7


def pack_date(day: str, timing: str) -> str:
    """The date a day's checklist should be packed, as ``YYYY-MM-DD``."""
    if timing == "day_before":
        return (date.fromisoformat(day) - timedelta(days=1)).isoformat()
    return day


def ordered_groups(db: Session, checklist_id: int) -> list[ChecklistGroup]:
    """A checklist's groups in display order: creation order, ties by id."""
    return (
        db.query(ChecklistGroup)
        .filter(ChecklistGroup.checklist_id == checklist_id)
        .order_by(ChecklistGroup.sort_order.asc(), ChecklistGroup.id.asc())
        .all()
    )


def ordered_items(
    db: Session, checklist_id: int, groups: list[ChecklistGroup] | None = None
) -> list[ChecklistItem]:
    """A checklist's items in the order they read: group by group, General last.

    An item whose ``group_id`` names a group that no longer exists is treated
    as General rather than dropped. Deleting a group moves its items first, so
    this only matters for a row something else left behind — and an item that
    vanished from every day's checklist would be the worse failure.
    """
    if groups is None:
        groups = ordered_groups(db, checklist_id)
    rank = {group.id: index for index, group in enumerate(groups)}
    general = len(groups)
    items = db.query(ChecklistItem).filter(ChecklistItem.checklist_id == checklist_id).all()
    return sorted(items, key=lambda i: (rank.get(i.group_id, general), i.sort_order, i.id))


def effective_group_id(item: ChecklistItem, groups: list[ChecklistGroup]) -> int | None:
    """The item's group as the page should file it: a stray id reads as General."""
    return item.group_id if any(g.id == item.group_id for g in groups) else None


def checked_item_ids(db: Session, day_id: int) -> set[int]:
    return {
        row.item_id
        for row in db.query(ChecklistDayCheck.item_id).filter(ChecklistDayCheck.day_id == day_id)
    }


def item_counts(db: Session, checklist_ids: list[int]) -> dict[int, int]:
    """Items per checklist, for every id asked about (zero included)."""
    counts = dict.fromkeys(checklist_ids, 0)
    if checklist_ids:
        rows = (
            db.query(ChecklistItem.checklist_id, func.count(ChecklistItem.id))
            .filter(ChecklistItem.checklist_id.in_(checklist_ids))
            .group_by(ChecklistItem.checklist_id)
        )
        counts.update(dict(rows.all()))
    return counts


def checked_counts(db: Session, day_ids: list[int]) -> dict[int, int]:
    """Checked items per day, counting only checks on items that still exist.

    Deleting an item deletes its checks, so the join is a guard rather than the
    mechanism: a stray check must never report "15 of 14 packed".
    """
    counts = dict.fromkeys(day_ids, 0)
    if day_ids:
        rows = (
            db.query(ChecklistDayCheck.day_id, func.count(ChecklistDayCheck.id))
            .join(ChecklistItem, ChecklistItem.id == ChecklistDayCheck.item_id)
            .join(ChecklistDay, ChecklistDay.id == ChecklistDayCheck.day_id)
            .filter(
                ChecklistDayCheck.day_id.in_(day_ids),
                ChecklistItem.checklist_id == ChecklistDay.checklist_id,
            )
            .group_by(ChecklistDayCheck.day_id)
        )
        counts.update(dict(rows.all()))
    return counts


def delete_checklist(db: Session, checklist: Checklist) -> None:
    """Delete a checklist and everything hanging off it. Does not commit.

    SQLite does not enforce the references, so the cascade is by hand — the
    same reason deleting an event removes its attendees and overrides itself.
    """
    day_ids = [
        row.id
        for row in db.query(ChecklistDay.id).filter(ChecklistDay.checklist_id == checklist.id)
    ]
    if day_ids:
        db.query(ChecklistDayCheck).filter(ChecklistDayCheck.day_id.in_(day_ids)).delete(
            synchronize_session=False
        )
    for model in (ChecklistDay, ChecklistItem, ChecklistGroup):
        db.query(model).filter(model.checklist_id == checklist.id).delete(synchronize_session=False)
    db.delete(checklist)


# --- The daily summary -----------------------------------------------------------


def _status(day: str, pack_on: str, today: str) -> str:
    if day == today:
        return "TODAY"
    if pack_on == today:
        return "PACK TODAY"
    days = (date.fromisoformat(day) - date.fromisoformat(today)).days
    return f"UPCOMING (in {days} day{'s' if days != 1 else ''})"


def _open_shopping_names(db: Session) -> set[str]:
    return {
        row.name.strip().casefold()
        for row in db.query(ShoppingItem.name).filter(ShoppingItem.completed == False)  # noqa: E712
    }


def summary_text(db: Session, today: date) -> str:
    """The CHECKLISTS section of the daily summary, or ``""`` when there is none.

    Every day's checklist from today through ``SUMMARY_LOOKAHEAD_DAYS`` ahead
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
        db.query(ChecklistDay)
        .filter(ChecklistDay.date >= start, ChecklistDay.date <= end)
        .order_by(ChecklistDay.date.asc(), ChecklistDay.id.asc())
        .all()
    )
    if not days:
        return ""

    checklists = {
        c.id: c
        for c in db.query(Checklist).filter(Checklist.id.in_({d.checklist_id for d in days}))
    }
    on_shopping_list = _open_shopping_names(db)

    blocks = []
    for day in days:
        checklist = checklists.get(day.checklist_id)
        if checklist is None:
            continue
        groups = ordered_groups(db, checklist.id)
        items = ordered_items(db, checklist.id, groups)
        checked = checked_item_ids(db, day.id)
        remaining = [item for item in items if item.id not in checked]
        if not remaining:
            continue

        pack_on = pack_date(day.date, checklist.pack_timing)
        heading = f'- "{checklist.name}"'
        if day.label:
            heading += f" ({day.label})"
        heading += f" for {_long_date(day.date)}"
        if pack_on != day.date:
            heading += f", packed the day before ({_long_date(pack_on)})"
        heading += f" — {_status(day.date, pack_on, start)}."
        heading += f" {len(items) - len(remaining)} of {len(items)} packed."

        names = {g.id: g.name for g in groups}
        lines = [heading]
        current_group: str | None = None
        for item in remaining:
            group = names.get(item.group_id, GENERAL)
            if groups and group != current_group:
                lines.append(f"  {group}:")
                current_group = group
            entry = item.name
            if item.note:
                entry += f" — {item.note}"
            if item.name.strip().casefold() in on_shopping_list:
                entry += " (already on the shopping list)"
            lines.append(f"{'    ' if groups else '  '}- {entry}")
        blocks.append("\n".join(lines))

    return "\n".join(blocks)


def _long_date(value: str) -> str:
    day = date.fromisoformat(value)
    return f"{day.strftime('%A')}, {day.strftime('%B')} {day.day}"
