"""Preparedness router: stock, locations, the go list, and the refresh digest.

Three tables back this feature:

* ``prep_locations`` groups items; the catch-all is ``location_id IS NULL``.
* ``prep_items`` is the stock itself, each optionally carrying a refresh
  schedule.
* ``prep_refresh_notices`` records what has already been announced, which is
  what stops the family being told about the same canned food every morning.
"""

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from rally import golist, prep_review, preparedness
from rally.database import get_db
from rally.models import PrepItem, PrepLocation, PrepRefreshNotice, PrepReview
from rally.schemas import (
    UNSET,
    GoListGroup,
    GoListResponse,
    PrepDigestItem,
    PrepDigestResponse,
    PrepItemCreate,
    PrepItemRefresh,
    PrepItemResponse,
    PrepItemUpdate,
    PrepLocationCreate,
    PrepLocationResponse,
    PrepLocationUpdate,
    PrepNoticeResponse,
    PrepReviewResponse,
    validate_prep_schedule,
)

router = APIRouter(prefix="/api/preparedness", tags=["preparedness"])

EXPORT_FORMATS = {
    "md": ("text/markdown; charset=utf-8", "md"),
    "csv": ("text/csv; charset=utf-8", "csv"),
    "pdf": ("application/pdf", "pdf"),
}


# --- Helpers -------------------------------------------------------------------


def _clean_name(raw: str, label: str) -> str:
    name = (raw or "").strip()
    if not name:
        raise HTTPException(status_code=422, detail=f"{label} cannot be empty")
    return name


def _find_location_by_name(db: Session, name: str) -> PrepLocation | None:
    return (
        db.query(PrepLocation).filter(func.lower(PrepLocation.name) == name.strip().lower()).first()
    )


def _require_location(db: Session, location_id: int | None) -> None:
    """Validate a bare integer location reference (no DB-level foreign keys)."""
    if location_id is None:
        return
    if not db.query(PrepLocation).filter(PrepLocation.id == location_id).first():
        raise HTTPException(status_code=422, detail="Unknown location_id")


def _parse_iso(value: str | None, label: str) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"{label} must be YYYY-MM-DD") from exc


def _location_names(db: Session) -> dict[int, str]:
    return {loc.id: loc.name for loc in db.query(PrepLocation).all()}


def _to_response(
    item: PrepItem, on_date: date, names: dict[int, str], default_lead: int
) -> PrepItemResponse:
    """Attach the fields the UI needs but the database never stores."""
    return PrepItemResponse(
        id=item.id,
        name=item.name,
        quantity=item.quantity,
        location_id=item.location_id,
        notes=item.notes,
        refresh_mode=item.refresh_mode,
        refresh_interval_months=item.refresh_interval_months,
        next_refresh_date=item.next_refresh_date,
        remind_days_before=item.remind_days_before,
        last_refreshed_on=item.last_refreshed_on,
        created_at=item.created_at,
        updated_at=item.updated_at,
        status=preparedness.status_of(item, on_date, default_lead),
        days_until=preparedness.days_until(item, on_date),
        location_name=names.get(item.location_id, "Unassigned"),
    )


# --- Locations -----------------------------------------------------------------


@router.get("/locations", response_model=list[PrepLocationResponse])
def list_locations(db: Session = Depends(get_db)):
    """Physical walking order, ties broken alphabetically."""
    return (
        db.query(PrepLocation)
        .order_by(PrepLocation.sort_order.asc(), PrepLocation.name.asc())
        .all()
    )


@router.post("/locations", response_model=PrepLocationResponse, status_code=201)
def create_location(location: PrepLocationCreate, db: Session = Depends(get_db)):
    """Create a location. Names are unique case-insensitively."""
    name = _clean_name(location.name, "Location name")
    if _find_location_by_name(db, name):
        raise HTTPException(status_code=409, detail="A location with that name already exists")

    db_location = PrepLocation(name=name, sort_order=location.sort_order)
    db.add(db_location)
    db.commit()
    db.refresh(db_location)
    return db_location


@router.put("/locations/{location_id}", response_model=PrepLocationResponse)
def update_location(location_id: int, location: PrepLocationUpdate, db: Session = Depends(get_db)):
    db_location = db.query(PrepLocation).filter(PrepLocation.id == location_id).first()
    if not db_location:
        raise HTTPException(status_code=404, detail="Location not found")

    if location.name is not None:
        name = _clean_name(location.name, "Location name")
        conflict = _find_location_by_name(db, name)
        if conflict and conflict.id != location_id:
            raise HTTPException(status_code=409, detail="A location with that name already exists")
        db_location.name = name

    if location.sort_order is not None:
        db_location.sort_order = location.sort_order

    db.commit()
    db.refresh(db_location)
    return db_location


@router.delete("/locations/{location_id}", status_code=204)
def delete_location(location_id: int, db: Session = Depends(get_db)):
    """Delete a location, moving its items to the catch-all first.

    That reassignment is load-bearing: SQLite foreign keys are not enforced
    here, so an orphaned ``location_id`` would make those items silently vanish
    from every rendered group — including the go list, which is the failure
    that actually matters. Same reasoning as ``delete_store`` on shopping.
    """
    db_location = db.query(PrepLocation).filter(PrepLocation.id == location_id).first()
    if not db_location:
        raise HTTPException(status_code=404, detail="Location not found")

    db.query(PrepItem).filter(PrepItem.location_id == location_id).update(
        {"location_id": None}, synchronize_session=False
    )
    db.delete(db_location)
    db.commit()
    return None


# --- Items ---------------------------------------------------------------------


@router.get("/items", response_model=list[PrepItemResponse])
def list_items(
    location: list[str] = Query(default=[], description="Location id and/or 'unassigned'"),
    status: str | None = Query(None, description="ok | due | overdue"),
    search: str | None = Query(None, description="Case-insensitive, over name and notes"),
    sort: str = Query("location", description="location | name | refresh-soonest | newest"),
    db: Session = Depends(get_db),
):
    """List preparedness stock.

    Also runs the daily refresh digest opportunistically. The container's
    minute loop is the reliable path, but it only exists under Docker, so
    without this hook a ``dev``-served instance would never send one — the same
    arrangement event reminders use in ``list_events``.
    """
    preparedness.run_daily_digest(db)

    on_date = preparedness.today_for(db)
    default_lead = preparedness.default_lead_days(db)
    query = db.query(PrepItem)

    if location:
        ids = [int(v) for v in location if v.isdigit()]
        clauses = []
        if ids:
            clauses.append(PrepItem.location_id.in_(ids))
        if "unassigned" in location:
            clauses.append(PrepItem.location_id.is_(None))
        if clauses:
            query = query.filter(or_(*clauses))

    if search and search.strip():
        term = f"%{search.strip()}%"
        query = query.filter(or_(PrepItem.name.ilike(term), PrepItem.notes.ilike(term)))

    items = query.all()

    # Status is derived from today's date rather than stored, which is also why
    # it cannot be an index and is filtered here.
    if status in ("ok", "due", "overdue"):
        items = [i for i in items if preparedness.status_of(i, on_date, default_lead) == status]

    order = {loc.id: (loc.sort_order, loc.name.lower()) for loc in db.query(PrepLocation).all()}
    if sort == "name":
        items.sort(key=lambda i: i.name.lower())
    elif sort == "refresh-soonest":
        items.sort(key=lambda i: (i.next_refresh_date or "9999-12-31", i.name.lower()))
    elif sort == "newest":
        items.sort(key=lambda i: i.created_at, reverse=True)
    else:  # location — unassigned sorts last
        items.sort(key=lambda i: (order.get(i.location_id, (10**6, "zzz")), i.name.lower()))

    names = _location_names(db)
    return [_to_response(i, on_date, names, default_lead) for i in items]


@router.post("/items", response_model=PrepItemResponse, status_code=201)
def create_item(item: PrepItemCreate, db: Session = Depends(get_db)):
    """Create an item.

    On ``interval`` mode with no explicit date the first refresh is seeded as
    today + interval: a newly added item is assumed fresh today, which is more
    useful than rejecting the request.
    """
    name = _clean_name(item.name, "Item name")
    _require_location(db, item.location_id)
    on_date = preparedness.today_for(db)

    next_date = item.next_refresh_date
    if item.refresh_mode == "interval" and not next_date:
        next_date = preparedness.add_months(on_date, item.refresh_interval_months).isoformat()
    if next_date:
        _parse_iso(next_date, "next_refresh_date")

    db_item = PrepItem(
        name=name,
        quantity=item.quantity,
        location_id=item.location_id,
        notes=item.notes,
        refresh_mode=item.refresh_mode,
        refresh_interval_months=item.refresh_interval_months,
        next_refresh_date=next_date,
        remind_days_before=item.remind_days_before,
    )
    db.add(db_item)
    db.commit()
    db.refresh(db_item)
    return _to_response(db_item, on_date, _location_names(db), preparedness.default_lead_days(db))


@router.get("/items/{item_id}", response_model=PrepItemResponse)
def get_item(item_id: int, db: Session = Depends(get_db)):
    db_item = db.query(PrepItem).filter(PrepItem.id == item_id).first()
    if not db_item:
        raise HTTPException(status_code=404, detail="Item not found")
    return _to_response(
        db_item, preparedness.today_for(db), _location_names(db), preparedness.default_lead_days(db)
    )


@router.put("/items/{item_id}", response_model=PrepItemResponse)
def update_item(item_id: int, item: PrepItemUpdate, db: Session = Depends(get_db)):
    """Partially update an item."""
    db_item = db.query(PrepItem).filter(PrepItem.id == item_id).first()
    if not db_item:
        raise HTTPException(status_code=404, detail="Item not found")

    if item.name is not None:
        db_item.name = _clean_name(item.name, "Item name")
    if item.quantity is not UNSET:
        db_item.quantity = item.quantity
    if item.notes is not UNSET:
        db_item.notes = item.notes
    if item.location_id is not UNSET:
        _require_location(db, item.location_id)
        db_item.location_id = item.location_id
    if item.remind_days_before is not UNSET:
        db_item.remind_days_before = item.remind_days_before
    if item.refresh_mode is not None:
        db_item.refresh_mode = item.refresh_mode
    if item.refresh_interval_months is not UNSET:
        db_item.refresh_interval_months = item.refresh_interval_months
    if item.next_refresh_date is not UNSET:
        _parse_iso(item.next_refresh_date, "next_refresh_date")
        db_item.next_refresh_date = item.next_refresh_date

    # Validate the merged state, not the patch: a request that only flips the
    # mode still has to leave a coherent item behind.
    try:
        validate_prep_schedule(
            db_item.refresh_mode, db_item.refresh_interval_months, db_item.next_refresh_date
        )
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    db.commit()
    db.refresh(db_item)
    return _to_response(
        db_item, preparedness.today_for(db), _location_names(db), preparedness.default_lead_days(db)
    )


@router.delete("/items/{item_id}", status_code=204)
def delete_item(item_id: int, db: Session = Depends(get_db)):
    """Delete an item. Its notices are left alone — history is permanent."""
    db_item = db.query(PrepItem).filter(PrepItem.id == item_id).first()
    if not db_item:
        raise HTTPException(status_code=404, detail="Item not found")

    db.delete(db_item)
    db.commit()
    return None


@router.post("/items/{item_id}/refresh", response_model=PrepItemResponse)
def refresh_item(item_id: int, body: PrepItemRefresh | None = None, db: Session = Depends(get_db)):
    """Mark an item refreshed and recompute its next refresh date."""
    db_item = db.query(PrepItem).filter(PrepItem.id == item_id).first()
    if not db_item:
        raise HTTPException(status_code=404, detail="Item not found")

    on = _parse_iso(body.on, "on") if body and body.on else preparedness.today_for(db)
    preparedness.mark_refreshed(db, db_item, on)
    return _to_response(
        db_item, preparedness.today_for(db), _location_names(db), preparedness.default_lead_days(db)
    )


# --- Go list -------------------------------------------------------------------


@router.get("/go-list", response_model=GoListResponse)
def get_go_list(
    location: list[str] = Query(default=[], description="Location id and/or 'unassigned'"),
    db: Session = Depends(get_db),
):
    on_date = preparedness.today_for(db)
    default_lead = preparedness.default_lead_days(db)
    names = _location_names(db)
    groups = golist.build_groups(db, location or None)

    return GoListResponse(
        generated_on=on_date.isoformat(),
        total_items=sum(len(items) for _lid, _n, items in groups),
        groups=[
            GoListGroup(
                location_id=lid,
                location_name=name,
                items=[_to_response(i, on_date, names, default_lead) for i in items],
            )
            for lid, name, items in groups
        ],
    )


@router.get("/go-list/export")
def export_go_list(
    format: str = Query("md", description="md | csv | pdf"),
    location: list[str] = Query(default=[]),
    db: Session = Depends(get_db),
):
    """Download the go list as an attachment."""
    if format not in EXPORT_FORMATS:
        raise HTTPException(status_code=422, detail="format must be one of: md, csv, pdf")

    on_date = preparedness.today_for(db)
    groups = golist.build_groups(db, location or None)
    media_type, ext = EXPORT_FORMATS[format]

    if format == "md":
        payload: bytes = golist.render_markdown(groups, on_date).encode("utf-8")
    elif format == "csv":
        payload = golist.render_csv(groups, on_date).encode("utf-8")
    else:
        payload = golist.render_pdf(groups, on_date)

    filename = f"go-list-{on_date.isoformat()}.{ext}"
    return Response(
        content=payload,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# --- Digest --------------------------------------------------------------------


@router.post("/digest/run", response_model=PrepDigestResponse)
def run_digest(
    dry_run: bool = Query(True, description="Report what would be sent without sending"),
    db: Session = Depends(get_db),
):
    """Run the refresh digest now.

    Defaults to ``dry_run=true`` — the honest way to answer "is this working"
    without waiting until morning, and without burning the notice rows that
    suppress a real send.
    """
    on_date = preparedness.today_for(db)
    default_lead = preparedness.default_lead_days(db)
    result = preparedness.send_digest(db, on_date, dry_run=dry_run)
    names = _location_names(db)

    return PrepDigestResponse(
        ran_on=on_date.isoformat(),
        dry_run=dry_run,
        sent=result.sent,
        count=result.count,
        items=[
            PrepDigestItem(
                id=i.id,
                name=i.name,
                location_name=names.get(i.location_id, "Unassigned"),
                next_refresh_date=i.next_refresh_date,
                status=preparedness.status_of(i, on_date, default_lead),
            )
            for i in result.due_items
        ],
        sent_to=result.sent_to,
        skipped=result.skipped,
        muted=result.muted,
        failed=result.failed,
        skipped_reason=result.skipped_reason,
    )


@router.get("/digest/log", response_model=list[PrepNoticeResponse])
def digest_log(limit: int = Query(50, ge=1, le=200), db: Session = Depends(get_db)):
    """Recent announcements, newest first. Answers "did it actually send?"."""
    rows = (
        db.query(PrepRefreshNotice)
        .order_by(PrepRefreshNotice.created_at.desc(), PrepRefreshNotice.id.desc())
        .limit(limit)
        .all()
    )
    names = {i.id: i.name for i in db.query(PrepItem).all()}
    return [
        PrepNoticeResponse(
            id=r.id,
            item_id=r.item_id,
            item_name=names.get(r.item_id),
            refresh_date=r.refresh_date,
            sent_on=r.sent_on,
            recipients=r.recipients,
            created_at=r.created_at,
        )
        for r in rows
    ]


# --- LLM review ----------------------------------------------------------------


def _review_response(db: Session, row: PrepReview) -> PrepReviewResponse:
    """Attach the staleness signal the stored row cannot know on its own."""
    current = prep_review.current_item_count(db)
    return PrepReviewResponse(
        id=row.id,
        review=row.data,
        model=row.model,
        item_count=row.item_count,
        current_item_count=current,
        # Cheapest possible staleness signal, and the one that matters after a
        # restock: a review of 38 items says little about the 44 you have now.
        stale=current != row.item_count,
        created_at=row.created_at,
    )


@router.get("/review", response_model=PrepReviewResponse)
def get_review(db: Session = Depends(get_db)):
    """The last stored review.

    Reading never triggers a call — a review costs real money and several
    seconds, so a page load must never spend either. `404` until one has been
    run, which the UI renders as "not reviewed yet" rather than an error.
    """
    row = prep_review.latest_review(db)
    if not row:
        raise HTTPException(status_code=404, detail="No review yet")
    return _review_response(db, row)


@router.post("/review", response_model=PrepReviewResponse)
def run_review(db: Session = Depends(get_db)):
    """Run a fresh review and store it.

    Every failure path returns a message written for the person who pressed the
    button, not a stack trace: the feature is off, there is nothing to review,
    no LLM is configured, or the model did not answer usefully.
    """
    try:
        row = prep_review.run_review(db)
    except prep_review.PrepReviewError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _review_response(db, row)
