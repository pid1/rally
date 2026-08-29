"""Shopping list router for Rally.

Three tables back this feature, and the split between them is deliberate:

* ``shopping_items`` is the live list. Completed items stay visible until local
  midnight (identical to Tasks) and are deleted outright once they are more than
  ``PURCHASED_RETENTION_DAYS`` old, so ``/shopping/purchased`` never grows
  unbounded.
* ``shopping_item_history`` is a permanent, deduplicated vocabulary with a use
  counter. It powers autocomplete and deliberately outlives the purge — which is
  precisely what makes deleting purchased rows safe.
* ``shopping_stores`` groups items; the catch-all is ``store_id IS NULL``.

Items are hand-orderable within their store via ``sort_order`` (see the column's
note in ``models``), which is what ``POST /items/reorder`` writes.
"""

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import case, func, nullslast, or_
from sqlalchemy.orm import Session

from rally import shopping_notifications
from rally.database import get_db
from rally.models import Setting, ShoppingItem, ShoppingItemHistory, ShoppingStore
from rally.schemas import (
    UNSET,
    ShoppingItemCreate,
    ShoppingItemResponse,
    ShoppingItemUpdate,
    ShoppingReorder,
    ShoppingStoreCreate,
    ShoppingStoreResponse,
    ShoppingStoreUpdate,
    ShoppingSuggestion,
)
from rally.utils.settings import local_timezone_name, today_start_utc
from rally.utils.timezone import now_utc, today_local

router = APIRouter(prefix="/api/shopping", tags=["shopping"])

# Completed items are deleted from the database once they are older than this.
# Open items are never purged at any age — an item nobody has bought in two
# years is still something the family wants.
PURCHASED_RETENTION_DAYS = 30

# Settings row (local YYYY-MM-DD) gating the purge to once per local day.
PURGE_DATE_SETTING = "shopping_last_purge_date"

# LIKE escape character for user-supplied autocomplete terms.
_LIKE_ESCAPE = "\\"

MAX_SUGGESTIONS = 25


# --- Helpers -------------------------------------------------------------------


def _clean_name(raw: str, label: str) -> str:
    """Trim a user-supplied name, rejecting empty/whitespace-only values."""
    name = (raw or "").strip()
    if not name:
        raise HTTPException(status_code=422, detail=f"{label} cannot be empty")
    return name


def _history_key(name: str) -> str:
    """The dedupe key for item history: trimmed and casefolded."""
    return name.strip().casefold()


def _find_store_by_name(db: Session, name: str) -> ShoppingStore | None:
    return (
        db.query(ShoppingStore)
        .filter(func.lower(ShoppingStore.name) == name.strip().lower())
        .first()
    )


def _require_store(db: Session, store_id: int | None) -> None:
    """Validate a bare integer store reference (no DB-level foreign keys here)."""
    if store_id is None:
        return
    if not db.query(ShoppingStore).filter(ShoppingStore.id == store_id).first():
        raise HTTPException(status_code=422, detail="Unknown store_id")


def _escape_like(term: str) -> str:
    """Escape LIKE wildcards so a typed ``%`` or ``_`` matches literally.

    Without this, typing ``%`` matches the entire history table and ``_``
    matches any character — a small bug that presents as "autocomplete is broken
    and random".
    """
    return (
        term.replace(_LIKE_ESCAPE, _LIKE_ESCAPE * 2)
        .replace("%", f"{_LIKE_ESCAPE}%")
        .replace("_", f"{_LIKE_ESCAPE}_")
    )


def _list_ordering():
    """The one order the list reads in: open items first, hand-arranged.

    ``sort_order`` is deliberately neutralised for completed rows. They sink to
    the bottom of their group and are ordered newest-first among themselves, so
    letting a stale position from before they were ticked off shuffle them would
    be noise. Collapsing them to a single tie value hands that tier to
    ``created_at DESC`` — exactly how the list read before ordering existed.
    """
    open_position = case((ShoppingItem.completed, 0), else_=ShoppingItem.sort_order)
    return (
        ShoppingItem.completed.asc(),
        open_position.asc(),
        ShoppingItem.created_at.desc(),
    )


def _next_sort_order(db: Session, store_id: int | None) -> int:
    """The position that puts a new item at the top of its store group.

    Adding used to surface at the top by virtue of ``created_at DESC``, and that
    is worth keeping: you add milk because you just thought of it, so you want to
    see that it landed. One below the current minimum does that without
    renumbering the group.
    """
    store_clause = (
        ShoppingItem.store_id.is_(None)
        if store_id is None
        else ShoppingItem.store_id == store_id
    )
    lowest = db.query(func.min(ShoppingItem.sort_order)).filter(store_clause).scalar()
    return 0 if lowest is None else lowest - 1


def purge_old_purchased_items(db: Session) -> None:
    """Delete items completed more than ``PURCHASED_RETENTION_DAYS`` ago.

    Runs opportunistically from the items listing (the ``process_recurring_todos``
    precedent in ``list_todos``) rather than from the 4 AM container job, which
    lives in ``entrypoint.sh`` and would skip a ``dev``-served instance entirely.

    Gated on a settings row so it executes at most once per local day: SQLite
    takes a write lock, and a read path shouldn't pay for that on every request.
    """
    today = today_local(local_timezone_name(db)).strftime("%Y-%m-%d")
    marker = db.query(Setting).filter(Setting.key == PURGE_DATE_SETTING).first()
    if marker and marker.value == today:
        return

    cutoff = now_utc() - timedelta(days=PURCHASED_RETENTION_DAYS)
    db.query(ShoppingItem).filter(
        ShoppingItem.completed == True,  # noqa: E712
        ShoppingItem.completed_at.isnot(None),
        ShoppingItem.completed_at < cutoff,
    ).delete(synchronize_session=False)

    if marker:
        marker.value = today
    else:
        db.add(Setting(key=PURGE_DATE_SETTING, value=today))
    db.commit()


def record_item_history(db: Session, name: str, store_id: int | None) -> None:
    """Upsert the permanent history row backing autocomplete.

    Called only on a real create. A dedupe hit doesn't increment (nothing was
    added), and renaming an item via PUT doesn't touch history either — history
    records adds, and a typo correction shouldn't rewrite a counter.
    """
    key = _history_key(name)
    row = (
        db.query(ShoppingItemHistory)
        .filter(ShoppingItemHistory.name_key == key)
        .first()
    )
    if row:
        row.times_added += 1
        row.name = name  # Follow the newest display casing
        row.store_id = store_id  # Last store used, not the most common one
        row.last_added_at = now_utc()
    else:
        db.add(
            ShoppingItemHistory(
                name_key=key,
                name=name,
                store_id=store_id,
                times_added=1,
                last_added_at=now_utc(),
            )
        )
    db.commit()


# --- Stores --------------------------------------------------------------------


@router.get("/stores", response_model=list[ShoppingStoreResponse])
def list_stores(db: Session = Depends(get_db)):
    """List all stores, alphabetically."""
    return db.query(ShoppingStore).order_by(ShoppingStore.name.asc()).all()


@router.post("/stores", response_model=ShoppingStoreResponse, status_code=201)
def create_store(store: ShoppingStoreCreate, db: Session = Depends(get_db)):
    """Create a store. Names are unique case-insensitively."""
    name = _clean_name(store.name, "Store name")
    if _find_store_by_name(db, name):
        raise HTTPException(
            status_code=409, detail="A store with that name already exists"
        )

    db_store = ShoppingStore(name=name)
    db.add(db_store)
    db.commit()
    db.refresh(db_store)
    return db_store


@router.put("/stores/{store_id}", response_model=ShoppingStoreResponse)
def update_store(
    store_id: int, store: ShoppingStoreUpdate, db: Session = Depends(get_db)
):
    """Rename a store."""
    db_store = db.query(ShoppingStore).filter(ShoppingStore.id == store_id).first()
    if not db_store:
        raise HTTPException(status_code=404, detail="Store not found")

    name = _clean_name(store.name, "Store name")
    conflict = _find_store_by_name(db, name)
    if conflict and conflict.id != store_id:
        raise HTTPException(
            status_code=409, detail="A store with that name already exists"
        )

    db_store.name = name
    db.commit()
    db.refresh(db_store)
    return db_store


@router.delete("/stores/{store_id}", status_code=204)
def delete_store(store_id: int, db: Session = Depends(get_db)):
    """Delete a store, moving its items to the "Anywhere" catch-all first.

    That reassignment is load-bearing: SQLite foreign keys aren't enforced here,
    so an orphaned ``store_id`` would make those items silently vanish from every
    rendered group. History rows keep their ``store_id`` — a suggestion whose
    store was deleted simply falls back to the catch-all when accepted.
    """
    db_store = db.query(ShoppingStore).filter(ShoppingStore.id == store_id).first()
    if not db_store:
        raise HTTPException(status_code=404, detail="Store not found")

    db.query(ShoppingItem).filter(ShoppingItem.store_id == store_id).update(
        {"store_id": None}, synchronize_session=False
    )
    db.delete(db_store)
    db.commit()
    return None


# --- Items ---------------------------------------------------------------------


@router.get("/items", response_model=list[ShoppingItemResponse])
def list_items(
    include_hidden: bool = Query(
        False, description="Include items completed before today (local time)"
    ),
    db: Session = Depends(get_db),
):
    """List shopping items, hiding items completed before today by default.

    ``include_hidden=true`` returns open and purchased items mixed in one list —
    a "show me everything" escape hatch for scripted clients, with no caller in
    the UI. The archive view is `GET /api/shopping/purchased`, which is the
    exact complement of the default listing rather than a superset of it.

    Bounded by the retention purge that runs (at most once per local day) here.
    """
    purge_old_purchased_items(db)

    query = db.query(ShoppingItem)
    if not include_hidden:
        cutoff = today_start_utc(db)
        query = query.filter(
            (ShoppingItem.completed == False)
            | (ShoppingItem.completed_at >= cutoff)  # noqa: E712
        )

    return query.order_by(*_list_ordering()).all()


@router.get("/purchased", response_model=list[ShoppingItemResponse])
def list_purchased_items(
    search: str | None = Query(
        None, description="Case-insensitive keyword matched against name and note."
    ),
    db: Session = Depends(get_db),
):
    """List items purchased before today (local time), most recent first.

    An item purchased *today* stays on the shopping list and appears here only
    once the local date rolls over — the same split `/api/todos/completed`
    makes. Read-only: nothing here can be edited, restored or deleted.

    No sort, limit or offset: ``PURCHASED_RETENTION_DAYS`` bounds the window, so
    the whole archive fits in one response. Pagination is the right first
    addition if that retention ever lengthens.
    """
    purge_old_purchased_items(db)

    cutoff = today_start_utc(db)
    query = db.query(ShoppingItem).filter(
        ShoppingItem.completed == True,  # noqa: E712
        # A purchased item with no completed_at is already hidden from the
        # shopping list, so it belongs here rather than falling through the gap
        # between the two views — the same guarantee `list_completed_todos`
        # makes. The complement has to actually be the complement.
        (ShoppingItem.completed_at < cutoff) | (ShoppingItem.completed_at.is_(None)),
    )

    if search and search.strip():
        term = f"%{search.strip()}%"
        query = query.filter(
            or_(ShoppingItem.name.ilike(term), ShoppingItem.note.ilike(term))
        )

    return query.order_by(
        nullslast(ShoppingItem.completed_at.desc()), ShoppingItem.id.desc()
    ).all()


@router.post("/items", response_model=ShoppingItemResponse, status_code=201)
def create_item(
    item: ShoppingItemCreate, response: Response, db: Session = Depends(get_db)
):
    """Add an item, deduplicating against the open list and recording history.

    A store may be named instead of referenced by id, for scripted and voice
    clients (Apple Shortcuts / Siri) that know names but not ids. An
    *unrecognized* name is not an error — the item lands in the catch-all rather
    than failing mid-dictation — and unknown names never auto-create a store.
    """
    # Announce the *previous* batch, if it has finished settling. Hung off a
    # write rather than a read — pushing from a GET is the mistake
    # ``todo_notifications`` explicitly avoids — and run before the insert
    # rather than after it, because the item being added is by definition
    # brand new: a pass taken afterwards would always find the batch still
    # warm and would never send anything. Gated to one pass a minute, like the
    # reminder check, so the container loop stays the reliable path while a
    # ``dev``-served instance is not silent.
    shopping_notifications.run_once_per_minute(db)

    name = _clean_name(item.name, "Item name")

    store_id = item.store_id
    if store_id is not None:
        _require_store(db, store_id)
    elif item.store is not None:
        match = _find_store_by_name(db, item.store)
        store_id = match.id if match else None

    # Add-dedupe: an open item with the same name in the same store is returned
    # as-is with 200. A merely *completed* match creates a new item — you bought
    # the milk, now you need more milk.
    store_clause = (
        ShoppingItem.store_id.is_(None)
        if store_id is None
        else ShoppingItem.store_id == store_id
    )
    existing = (
        db.query(ShoppingItem)
        .filter(
            ShoppingItem.completed == False,  # noqa: E712
            func.lower(ShoppingItem.name) == name.lower(),
            store_clause,
        )
        .first()
    )
    if existing:
        response.status_code = 200
        return existing

    db_item = ShoppingItem(
        name=name,
        note=item.note,
        store_id=store_id,
        completed=False,
        sort_order=_next_sort_order(db, store_id),
    )
    db.add(db_item)
    db.commit()
    db.refresh(db_item)

    record_item_history(db, name, store_id)
    return db_item


@router.put("/items/{item_id}", response_model=ShoppingItemResponse)
def update_item(item_id: int, item: ShoppingItemUpdate, db: Session = Depends(get_db)):
    """Partially update an item. Completion follows the todo pattern exactly."""
    db_item = db.query(ShoppingItem).filter(ShoppingItem.id == item_id).first()
    if not db_item:
        raise HTTPException(status_code=404, detail="Item not found")

    if item.name is not None:
        db_item.name = _clean_name(item.name, "Item name")
    if item.note is not UNSET:
        db_item.note = item.note
    if item.store_id is not UNSET:
        _require_store(db, item.store_id)
        if item.store_id != db_item.store_id:
            # A position is only meaningful inside the store it was arranged in,
            # so changing store through the edit form re-places the item at the
            # top of its new group rather than dropping it at whatever rank it
            # held in the old one. Dragging takes the other path (`reorder_items`),
            # where the user has said exactly where it goes.
            db_item.sort_order = _next_sort_order(db, item.store_id)
        db_item.store_id = item.store_id
    if item.completed is not None:
        if item.completed and not db_item.completed:
            db_item.completed_at = now_utc()
        elif not item.completed:
            db_item.completed_at = None
        db_item.completed = item.completed

    db.commit()
    db.refresh(db_item)
    return db_item


@router.post("/items/reorder", response_model=list[ShoppingItemResponse])
def reorder_items(payload: ShoppingReorder, db: Session = Depends(get_db)):
    """Rewrite one store group's order, moving in any item that isn't there yet.

    The client sends the destination group as it should now read, top to bottom,
    and every listed item is assigned to ``store_id`` and numbered by its index.
    That makes the endpoint idempotent and makes a cross-store drag the same
    operation as a within-store one: the item simply appears in a payload for a
    store it did not previously belong to.

    The group the item *left* is deliberately not renumbered. Positions are only
    ever compared, so the gap left behind changes nothing, and rewriting a second
    group would double the rows this touches for no visible effect.

    Only the items named in the payload are returned, in their new order — the
    caller's own list is the thing it needs back. Completed items are usually
    absent from it (they are not draggable and sort below the arranged ones
    regardless), but naming one is allowed and moves it like any other row.
    """
    _require_store(db, payload.store_id)

    # A duplicate id would otherwise assign two positions to one row, and the
    # later one would silently win. Keep the first mention, drop the rest.
    ordered_ids: list[int] = []
    seen: set[int] = set()
    for item_id in payload.item_ids:
        if item_id not in seen:
            seen.add(item_id)
            ordered_ids.append(item_id)

    if not ordered_ids:
        return []

    rows = {
        row.id: row
        for row in db.query(ShoppingItem).filter(ShoppingItem.id.in_(ordered_ids)).all()
    }
    missing = [item_id for item_id in ordered_ids if item_id not in rows]
    if missing:
        # All or nothing: a partial reorder would leave the list in an order the
        # user never asked for, which is worse than refusing and refetching.
        raise HTTPException(status_code=404, detail=f"Unknown item ids: {missing}")

    for position, item_id in enumerate(ordered_ids):
        row = rows[item_id]
        row.store_id = payload.store_id
        row.sort_order = position

    db.commit()
    return [rows[item_id] for item_id in ordered_ids]


@router.delete("/items/{item_id}", status_code=204)
def delete_item(item_id: int, db: Session = Depends(get_db)):
    """Delete an item. History is untouched."""
    db_item = db.query(ShoppingItem).filter(ShoppingItem.id == item_id).first()
    if not db_item:
        raise HTTPException(status_code=404, detail="Item not found")

    db.delete(db_item)
    db.commit()
    return None


# --- Suggestions ---------------------------------------------------------------


@router.get("/suggestions", response_model=list[ShoppingSuggestion])
def list_suggestions(
    q: str | None = Query(
        None, description="Substring to match anywhere in the item name"
    ),
    limit: int = Query(8, ge=1),
    db: Session = Depends(get_db),
):
    """Autocomplete matches from the permanent item history.

    Matching is substring, not prefix, so ``milk`` finds "Almond milk". Ranking
    puts prefix matches first, then substring matches, each tier ordered by use
    count so the weekly staple beats the one-off. An empty ``q`` returns the
    usual suspects — the top entries by use count.
    """
    limit = min(limit, MAX_SUGGESTIONS)
    query = db.query(ShoppingItemHistory)

    term = (q or "").strip()
    ranking = (
        ShoppingItemHistory.times_added.desc(),
        ShoppingItemHistory.last_added_at.desc(),
        ShoppingItemHistory.name.asc(),
    )

    if term:
        escaped = _escape_like(term.lower())
        lowered = func.lower(ShoppingItemHistory.name)
        query = query.filter(lowered.like(f"%{escaped}%", escape=_LIKE_ESCAPE))
        prefix_first = case(
            (lowered.like(f"{escaped}%", escape=_LIKE_ESCAPE), 0), else_=1
        )
        query = query.order_by(prefix_first, *ranking)
    else:
        query = query.order_by(*ranking)

    return query.limit(limit).all()


@router.delete("/suggestions/{history_id}", status_code=204)
def delete_suggestion(history_id: int, db: Session = Depends(get_db)):
    """Forget a suggestion.

    History is permanent, so a typo'd add ("mikl") would otherwise haunt
    autocomplete forever. Shopping items are left alone.
    """
    row = (
        db.query(ShoppingItemHistory)
        .filter(ShoppingItemHistory.id == history_id)
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Suggestion not found")

    db.delete(row)
    db.commit()
    return None
