"""Calendar events API.

Two shapes travel through here and they are deliberately different:

- an **event** is the stored rule (`GET /api/events/{id}`), which is what the
  edit form reads;
- an **occurrence** is one dated instance of it (`GET /api/events`), which is
  what every view renders.

Collapsing the two is how an edit gets applied to the wrong thing, so nothing
in this module returns one where the other is meant.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from rally.calendars import (
    Occurrence,
    RecurrenceError,
)
from rally.calendars import cache as calendar_cache
from rally.calendars import (
    collect_occurrences,
    dates_covered,
    expand_event,
    series_end_date,
    validate_rrule,
)
from rally.calendars.inputs import (
    EventTimeError,
    local_form_values,
    resolve_event_times,
)
from rally.database import get_db
from rally.models import (
    Calendar,
    Event,
    EventAttendee,
    EventNotification,
    EventOverride,
    FamilyMember,
)
from rally.notifications import (
    KIND_CREATED,
    KIND_DELETED,
    KIND_MANUAL,
    KIND_UPDATED,
    notify_event_change,
    notify_occurrence,
    plan_change_notice,
    run_due_reminders_once_per_minute,
    send_change_notice,
)
from rally.schemas import (
    UNSET,
    EventCreate,
    EventNotifyRequest,
    EventNotifyResponse,
    EventOverrideResponse,
    EventResponse,
    EventUpdate,
    OccurrencePage,
    OccurrenceResponse,
)
from rally.utils.settings import local_timezone_name
from rally.utils.timezone import now_utc

router = APIRouter(prefix="/api/events", tags=["events"])

# A year at a time is plenty for a month grid or an agenda, and it bounds the
# work an unbounded recurrence can be asked to do in one request.
MAX_WINDOW_DAYS = 366

SCOPE_THIS = "this"
SCOPE_FOLLOWING = "following"
SCOPE_ALL = "all"
SCOPES = (SCOPE_THIS, SCOPE_FOLLOWING, SCOPE_ALL)


# --- Helpers ---------------------------------------------------------------


def _tz(db: Session) -> ZoneInfo:
    return ZoneInfo(local_timezone_name(db))


def _default_native_calendar(db: Session) -> Calendar:
    """The calendar a new event lands on when the client did not pick one.

    Creating one on demand keeps the API usable on a fresh install: the
    migration seeds a native calendar per existing family member, but a family
    that has not added anybody yet still needs somewhere to put an event.
    """
    calendar = (
        db.query(Calendar)
        .filter(Calendar.cal_type == "native")
        .order_by(Calendar.id.asc())
        .first()
    )
    if calendar:
        return calendar

    member = db.query(FamilyMember).order_by(FamilyMember.id.asc()).first()
    calendar = Calendar(
        label=f"{member.name}'s Calendar" if member else "Family Calendar",
        url="",
        family_member_id=member.id if member else 0,
        cal_type="native",
    )
    db.add(calendar)
    db.commit()
    db.refresh(calendar)
    return calendar


def _attendee_ids(db: Session, event_id: int) -> list[int]:
    return [
        row.family_member_id
        for row in db.query(EventAttendee)
        .filter(EventAttendee.event_id == event_id)
        .order_by(EventAttendee.id.asc())
        .all()
    ]


def _set_attendees(db: Session, event_id: int, member_ids: list[int]) -> None:
    """Replace an event's attendees, ignoring ids that are not family members.

    SQLite does not enforce foreign keys here, so a bad id would otherwise
    become a permanently invisible attendee.
    """
    valid = {
        member.id
        for member in db.query(FamilyMember)
        .filter(FamilyMember.id.in_(member_ids or []))
        .all()
    }
    db.query(EventAttendee).filter(EventAttendee.event_id == event_id).delete(
        synchronize_session=False
    )
    for member_id in member_ids or []:
        if member_id in valid:
            db.add(EventAttendee(event_id=event_id, family_member_id=member_id))


def _overrides(db: Session, event_id: int) -> list[EventOverride]:
    return (
        db.query(EventOverride)
        .filter(EventOverride.event_id == event_id)
        .order_by(EventOverride.occurrence_date.asc())
        .all()
    )


def _event_response(db: Session, event: Event) -> EventResponse:
    tz = ZoneInfo(event.tzid) if event.tzid else _tz(db)
    start, end = local_form_values(event, tz)
    return EventResponse(
        id=event.id,
        calendar_id=event.calendar_id,
        uid=event.uid,
        title=event.title,
        description=event.description,
        location=event.location,
        all_day=bool(event.all_day),
        start=start,
        end=end,
        start_date=event.start_date,
        end_date=event.end_date,
        tzid=event.tzid,
        rrule=event.rrule,
        series_end_date=event.series_end_date,
        notify_minutes_before=event.notify_minutes_before,
        attendee_ids=_attendee_ids(db, event.id),
        overrides=[
            EventOverrideResponse(
                occurrence_date=override.occurrence_date,
                cancelled=bool(override.cancelled),
                title=override.title,
                start_date=override.start_date,
                end_date=override.end_date,
            )
            for override in _overrides(db, event.id)
        ],
        created_at=event.created_at,
        updated_at=event.updated_at,
    )


def _occurrence_response(occurrence: Occurrence, tz: ZoneInfo) -> OccurrenceResponse:
    return OccurrenceResponse(
        uid=occurrence.uid,
        source=occurrence.source,
        title=occurrence.title,
        description=occurrence.description,
        location=occurrence.location,
        all_day=occurrence.all_day,
        start=occurrence.start,
        end=occurrence.end,
        start_date=occurrence.start_local_date,
        end_date=occurrence.end_local_date,
        time_label=occurrence.time_label(tz),
        end_time_label=(
            ""
            if occurrence.all_day
            else occurrence.local_end(tz).strftime("%I:%M %p").lstrip("0")
        ),
        dates=dates_covered(occurrence),
        calendar_id=occurrence.calendar_id,
        calendar_label=occurrence.calendar_label,
        member=occurrence.member,
        member_color=occurrence.member_color,
        attendees=list(occurrence.attendees),
        event_id=occurrence.event_id,
        occurrence_date=occurrence.occurrence_date,
        recurring=occurrence.recurring,
        editable=occurrence.editable,
        notify_minutes_before=occurrence.notify_minutes_before,
    )


def _load_event(db: Session, event_id: int) -> Event:
    event = db.query(Event).filter(Event.id == event_id).first()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    return event


def _apply_times(
    payload: dict, *, start: str, end: str | None, all_day: bool, tzid: str
) -> None:
    try:
        payload.update(
            resolve_event_times(start=start, end=end, all_day=all_day, tzid=tzid)
        )
    except EventTimeError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _validated_rrule(rrule: str | None) -> str | None:
    try:
        return validate_rrule(rrule)
    except RecurrenceError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _until_value(event: Event, last_day: date) -> str:
    """An RRULE ``UNTIL`` that stops a series after ``last_day``.

    UNTIL is compared against the occurrence's own DTSTART, so for a timed
    series it must carry a time late enough to include that day's occurrence
    and early enough to exclude the next one — end of day in UTC does both.
    """
    if event.all_day:
        return last_day.strftime("%Y%m%d")
    boundary = datetime.combine(last_day, datetime.max.time()).replace(tzinfo=UTC)
    return boundary.strftime("%Y%m%dT%H%M%SZ")


def _truncate_series(event: Event, before: date) -> None:
    """End ``event``'s recurrence the day before ``before``."""
    last_day = before - timedelta(days=1)
    parts = [
        p
        for p in (event.rrule or "").split(";")
        if p and not p.upper().startswith("UNTIL=")
    ]
    parts = [p for p in parts if not p.upper().startswith("COUNT=")]
    parts.append(f"UNTIL={_until_value(event, last_day)}")
    event.rrule = ";".join(parts)
    event.series_end_date = last_day.isoformat()


# --- Occurrences -----------------------------------------------------------


@router.get("", response_model=OccurrencePage)
def list_occurrences(
    start: str | None = Query(None, description="First local date, YYYY-MM-DD"),
    end: str | None = Query(None, description="Exclusive last local date, YYYY-MM-DD"),
    member: list[str] = Query(default=[]),
    source: str = Query("all", pattern="^(all|native|external)$"),
    db: Session = Depends(get_db),
):
    """Expanded occurrences across a window, from every configured source.

    This is also where due reminders get a chance to fire. The 4 AM job lives
    in ``entrypoint.sh`` and only runs under Docker, so without an
    opportunistic hook a `dev`-served instance would never send one — the same
    reasoning, and the same once-per-period gate, as the shopping purge.
    """
    tz = _tz(db)
    today = now_utc().astimezone(tz).date()

    try:
        start_day = date.fromisoformat(start) if start else today
        end_day = date.fromisoformat(end) if end else start_day + timedelta(days=30)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Dates must be YYYY-MM-DD") from exc

    if end_day <= start_day:
        end_day = start_day + timedelta(days=1)
    if (end_day - start_day).days > MAX_WINDOW_DAYS:
        raise HTTPException(
            status_code=422, detail=f"Window may not exceed {MAX_WINDOW_DAYS} days"
        )

    run_due_reminders_once_per_minute(db)

    # Keep the cache warm from the read path as well as the minute loop. The
    # container loop only exists under Docker, so without this a `dev` instance
    # would serve an ever-staler calendar — the same reasoning as reminders.
    # This never blocks on the network unless the interval has actually
    # elapsed, and never on a cache that is merely warm.
    try:
        calendar_cache.sync_if_stale(db, tz)
    except Exception as exc:  # pragma: no cover - a sync must not fail a read
        print(f"Calendar sync failed: {exc}")

    sources = None if source == "all" else {source}
    result = collect_occurrences(
        db,
        start_day=start_day,
        end_day_exclusive=end_day,
        local_tz=tz,
        sources=sources,
        use_cache=True,
    )

    occurrences = result.occurrences
    if member:
        # Match on attendees alone. ``attendees`` already falls back to the
        # calendar's owner when an occurrence has no explicit list, so ORing the
        # owner in as well would make every event on a shared native calendar
        # match its owner — including the ones explicitly assigned to somebody
        # else.
        wanted = {name.strip().lower() for name in member if name.strip()}
        occurrences = [
            occurrence
            for occurrence in occurrences
            if {name.lower() for name in occurrence.attendees} & wanted
        ]

    return OccurrencePage(
        occurrences=[
            _occurrence_response(occurrence, tz) for occurrence in occurrences
        ],
        failures=result.failures,
    )


# --- Event CRUD ------------------------------------------------------------


@router.post("", response_model=EventResponse, status_code=201)
def create_event(payload: EventCreate, db: Session = Depends(get_db)):
    """Create a single event or a recurring series."""
    tz_name = payload.tzid or local_timezone_name(db)
    calendar = (
        db.query(Calendar)
        .filter(Calendar.id == payload.calendar_id, Calendar.cal_type == "native")
        .first()
        if payload.calendar_id
        else None
    )
    if payload.calendar_id and not calendar:
        raise HTTPException(status_code=422, detail="Unknown native calendar")
    if calendar is None:
        calendar = _default_native_calendar(db)

    fields: dict = {}
    _apply_times(
        fields,
        start=payload.start,
        end=payload.end,
        all_day=payload.all_day,
        tzid=tz_name,
    )
    rrule = _validated_rrule(payload.rrule)

    event = Event(
        calendar_id=calendar.id,
        uid=f"rally-{uuid.uuid4().hex[:16]}@rally.local",
        title=payload.title.strip() or "Untitled Event",
        description=payload.description,
        location=payload.location,
        rrule=rrule,
        series_end_date=series_end_date(rrule),
        notify_minutes_before=payload.notify_minutes_before,
        **fields,
    )
    db.add(event)
    db.commit()
    db.refresh(event)

    _set_attendees(db, event.id, payload.attendee_ids)
    db.commit()
    db.refresh(event)

    # After the commit, never before: the event exists whether or not a phone
    # buzzes, and ``notify_event_change`` cannot raise.
    notify_event_change(db, event, kind=KIND_CREATED)
    return _event_response(db, event)


@router.get("/{event_id}", response_model=EventResponse)
def get_event(event_id: int, db: Session = Depends(get_db)):
    """The stored series row plus its overrides."""
    return _event_response(db, _load_event(db, event_id))


@router.get("/{event_id}/occurrences", response_model=list[OccurrenceResponse])
def list_event_occurrences(
    event_id: int,
    start: str | None = None,
    end: str | None = None,
    db: Session = Depends(get_db),
):
    """Occurrences of one series, so the UI can show what a change affects."""
    event = _load_event(db, event_id)
    tz = _tz(db)
    today = now_utc().astimezone(tz).date()

    try:
        start_day = date.fromisoformat(start) if start else today
        end_day = date.fromisoformat(end) if end else start_day + timedelta(days=90)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Dates must be YYYY-MM-DD") from exc

    from rally.calendars.sources import window_bounds

    window_start, window_end = window_bounds(start_day, end_day, tz)
    occurrences = expand_event(
        event,
        overrides=_overrides(db, event.id),
        window_start=window_start,
        window_end=window_end,
        local_tz=tz,
    )
    return [_occurrence_response(occurrence, tz) for occurrence in occurrences]


def _require_occurrence_date(
    event: Event, scope: str, occurrence_date: str | None
) -> date:
    if not event.rrule:
        raise HTTPException(
            status_code=422,
            detail=f"scope={scope} only applies to a recurring event",
        )
    if not occurrence_date:
        raise HTTPException(
            status_code=422, detail=f"occurrence_date is required for scope={scope}"
        )
    try:
        return date.fromisoformat(occurrence_date)
    except ValueError as exc:
        raise HTTPException(
            status_code=422, detail="occurrence_date must be YYYY-MM-DD"
        ) from exc


@router.put("/{event_id}", response_model=EventResponse)
def update_event(
    event_id: int,
    payload: EventUpdate,
    scope: str = Query(SCOPE_ALL, pattern="^(this|following|all)$"),
    occurrence_date: str | None = None,
    db: Session = Depends(get_db),
):
    """Edit an event at one of three scopes.

    ``all`` updates the series and **keeps existing overrides** — a soccer
    practice the family already moved to Thursday must not snap back to Tuesday
    because somebody corrected the title.

    ``following`` splits the series: the original is truncated with ``UNTIL``
    and a new event carries the edited values forward, taking the overrides at
    or after the split with it. Splitting on a *date* rather than an occurrence
    index matters — an index shifts the moment an earlier occurrence is
    cancelled.

    Every scope announces itself to the attendees afterwards. Which occurrence
    the notice names differs by scope, and that is the point: a change to one
    Tuesday says that Tuesday, while a change to the series says the next one
    people will actually turn up to.
    """
    event = _load_event(db, event_id)
    tz_name = payload.tzid or event.tzid or local_timezone_name(db)

    if scope == SCOPE_THIS:
        split_day = _require_occurrence_date(event, scope, occurrence_date)
        response = _update_single_occurrence(db, event, payload, split_day, tz_name)
        notify_event_change(
            db, event, kind=KIND_UPDATED, occurrence_date=split_day.isoformat()
        )
        return response

    if scope == SCOPE_FOLLOWING:
        split_day = _require_occurrence_date(event, scope, occurrence_date)
        response = _split_series(db, event, payload, split_day, tz_name)
        # The edit now lives on the tail series, so that is what gets described.
        tail = db.query(Event).filter(Event.id == response.id).first()
        if tail is not None:
            notify_event_change(db, tail, kind=KIND_UPDATED)
        return response

    _apply_event_fields(db, event, payload, tz_name)
    db.commit()
    db.refresh(event)
    notify_event_change(db, event, kind=KIND_UPDATED)
    return _event_response(db, event)


def _apply_event_fields(
    db: Session, event: Event, payload: EventUpdate, tz_name: str
) -> None:
    """Fold an update payload onto a series row."""
    if payload.title is not None:
        event.title = payload.title.strip() or event.title
    if payload.description is not UNSET:
        event.description = payload.description
    if payload.location is not UNSET:
        event.location = payload.location
    if payload.notify_minutes_before is not UNSET:
        event.notify_minutes_before = payload.notify_minutes_before
    if payload.rrule is not UNSET:
        event.rrule = _validated_rrule(payload.rrule)
        event.series_end_date = series_end_date(event.rrule)
    if payload.calendar_id is not None:
        event.calendar_id = payload.calendar_id

    if payload.start is not None:
        all_day = event.all_day if payload.all_day is None else payload.all_day
        end = payload.end if payload.end is not UNSET else None
        fields: dict = {}
        _apply_times(
            fields, start=payload.start, end=end, all_day=all_day, tzid=tz_name
        )
        for key, value in fields.items():
            setattr(event, key, value)

    if payload.attendee_ids is not None:
        _set_attendees(db, event.id, payload.attendee_ids)


def _update_single_occurrence(
    db: Session, event: Event, payload: EventUpdate, split_day: date, tz_name: str
) -> EventResponse:
    """Write an override for one occurrence, leaving the series alone."""
    override = (
        db.query(EventOverride)
        .filter(
            EventOverride.event_id == event.id,
            EventOverride.occurrence_date == split_day.isoformat(),
        )
        .first()
    )
    if override is None:
        override = EventOverride(
            event_id=event.id, occurrence_date=split_day.isoformat()
        )
        db.add(override)

    override.cancelled = False
    if payload.title is not None:
        override.title = payload.title
    if payload.description is not UNSET:
        override.description = payload.description
    if payload.location is not UNSET:
        override.location = payload.location

    if payload.start is not None:
        all_day = event.all_day if payload.all_day is None else payload.all_day
        end = payload.end if payload.end is not UNSET else None
        fields: dict = {}
        _apply_times(
            fields, start=payload.start, end=end, all_day=all_day, tzid=tz_name
        )
        override.all_day = fields["all_day"]
        override.start_utc = fields["start_utc"]
        override.end_utc = fields["end_utc"]
        override.start_date = fields["start_date"]
        override.end_date = fields["end_date"]

    # Attendees and reminders belong to the series: a per-occurrence recipient
    # list is a second notification model, and nobody asked for one.
    if payload.attendee_ids is not None:
        _set_attendees(db, event.id, payload.attendee_ids)

    db.commit()
    db.refresh(event)
    return _event_response(db, event)


def _split_series(
    db: Session, event: Event, payload: EventUpdate, split_day: date, tz_name: str
) -> EventResponse:
    """Truncate the original series and carry the edit forward on a new one."""
    original_rrule = event.rrule
    tz = ZoneInfo(event.tzid or tz_name)

    # The new series starts at the split occurrence unless the edit moves it.
    if payload.start is not None:
        new_start, new_end = payload.start, (
            payload.end if payload.end is not UNSET else None
        )
    elif event.all_day:
        new_start, new_end = (
            split_day.isoformat(),
            (
                split_day
                + (
                    date.fromisoformat(event.end_date)
                    - date.fromisoformat(event.start_date)
                )
            ).isoformat(),
        )
    else:
        start_local = event.start_utc.replace(tzinfo=UTC).astimezone(tz)
        duration = event.end_utc - event.start_utc
        moved = datetime.combine(split_day, start_local.time())
        new_start = moved.strftime("%Y-%m-%dT%H:%M")
        new_end = (moved + duration).strftime("%Y-%m-%dT%H:%M")

    all_day = event.all_day if payload.all_day is None else payload.all_day
    fields: dict = {}
    _apply_times(fields, start=new_start, end=new_end, all_day=all_day, tzid=tz_name)

    rrule = (
        original_rrule if payload.rrule is UNSET else _validated_rrule(payload.rrule)
    )
    # A COUNT on the tail would restart the count, so the split drops it and
    # relies on the original UNTIL, if any.
    if rrule:
        rrule = ";".join(
            p for p in rrule.split(";") if not p.upper().startswith("COUNT=")
        )

    tail = Event(
        calendar_id=payload.calendar_id or event.calendar_id,
        uid=f"rally-{uuid.uuid4().hex[:16]}@rally.local",
        title=(payload.title if payload.title is not None else event.title),
        description=(
            event.description if payload.description is UNSET else payload.description
        ),
        location=(event.location if payload.location is UNSET else payload.location),
        rrule=rrule,
        series_end_date=series_end_date(rrule),
        notify_minutes_before=(
            event.notify_minutes_before
            if payload.notify_minutes_before is UNSET
            else payload.notify_minutes_before
        ),
        **fields,
    )
    db.add(tail)
    db.commit()
    db.refresh(tail)

    attendee_ids = (
        payload.attendee_ids
        if payload.attendee_ids is not None
        else _attendee_ids(db, event.id)
    )
    _set_attendees(db, tail.id, attendee_ids)

    # Overrides on or after the split describe occurrences that now belong to
    # the tail; earlier ones stay with the head.
    for override in _overrides(db, event.id):
        if override.occurrence_date >= split_day.isoformat():
            override.event_id = tail.id

    _truncate_series(event, split_day)
    db.commit()
    db.refresh(tail)
    return _event_response(db, tail)


@router.delete("/{event_id}", status_code=204)
def delete_event(
    event_id: int,
    scope: str = Query(SCOPE_ALL, pattern="^(this|following|all)$"),
    occurrence_date: str | None = None,
    db: Session = Depends(get_db),
):
    """Delete one occurrence, the tail of a series, or the whole event.

    Each scope announces the deletion to the attendees. The notice is *planned*
    before the delete and *sent* after it: a delete destroys the occurrence, the
    attendee list and sometimes the event row, so there is nothing left to build
    a message from afterwards — and announcing it beforehand would risk naming a
    deletion that then failed.
    """
    event = _load_event(db, event_id)

    if scope == SCOPE_THIS:
        split_day = _require_occurrence_date(event, scope, occurrence_date)
        notice = plan_change_notice(
            db, event, kind=KIND_DELETED, occurrence_date=split_day.isoformat()
        )
        override = (
            db.query(EventOverride)
            .filter(
                EventOverride.event_id == event.id,
                EventOverride.occurrence_date == split_day.isoformat(),
            )
            .first()
        )
        if override is None:
            override = EventOverride(
                event_id=event.id, occurrence_date=split_day.isoformat()
            )
            db.add(override)
        override.cancelled = True
        db.commit()
        send_change_notice(db, notice)
        return None

    if scope == SCOPE_FOLLOWING:
        split_day = _require_occurrence_date(event, scope, occurrence_date)
        # The first occurrence being removed is the one worth naming: everything
        # from here on is going, and that is the date people had in their heads.
        notice = plan_change_notice(
            db, event, kind=KIND_DELETED, occurrence_date=split_day.isoformat()
        )
        _truncate_series(event, split_day)
        db.query(EventOverride).filter(
            EventOverride.event_id == event.id,
            EventOverride.occurrence_date >= split_day.isoformat(),
        ).delete(synchronize_session=False)
        db.commit()
        send_change_notice(db, notice)
        return None

    # ``record=False``: the notification rows for this event are about to be
    # cascaded away, so writing another would leave an orphan behind.
    notice = plan_change_notice(db, event, kind=KIND_DELETED, record=False)

    # SQLite foreign keys are not enforced, so the cascade is explicit — an
    # orphaned attendee or notification row would otherwise linger invisibly.
    db.query(EventAttendee).filter(EventAttendee.event_id == event.id).delete(
        synchronize_session=False
    )
    db.query(EventOverride).filter(EventOverride.event_id == event.id).delete(
        synchronize_session=False
    )
    db.query(EventNotification).filter(EventNotification.event_id == event.id).delete(
        synchronize_session=False
    )
    db.delete(event)
    db.commit()
    send_change_notice(db, notice)
    return None


# --- Notifications ---------------------------------------------------------


@router.post("/{event_id}/notify", response_model=EventNotifyResponse)
def notify_event(
    event_id: int,
    payload: EventNotifyRequest | None = None,
    db: Session = Depends(get_db),
):
    """Push this event to its attendees now.

    Defaults to the next upcoming occurrence, because "notify about the
    dentist" almost always means the one that has not happened yet.
    """
    event = _load_event(db, event_id)
    payload = payload or EventNotifyRequest()
    tz = _tz(db)
    now = now_utc()

    from rally.calendars.sources import window_bounds

    if payload.occurrence_date:
        try:
            target = date.fromisoformat(payload.occurrence_date)
        except ValueError as exc:
            raise HTTPException(
                status_code=422, detail="occurrence_date must be YYYY-MM-DD"
            ) from exc
        window_start, window_end = window_bounds(target, target + timedelta(days=1), tz)
    else:
        today = now.astimezone(tz).date()
        window_start, window_end = window_bounds(
            today, today + timedelta(days=MAX_WINDOW_DAYS), tz
        )

    occurrences = expand_event(
        event,
        overrides=_overrides(db, event.id),
        window_start=window_start,
        window_end=window_end,
        local_tz=tz,
    )
    if not occurrences:
        raise HTTPException(
            status_code=404, detail="No occurrence found to notify about"
        )

    upcoming = [o for o in occurrences if o.end >= now]
    occurrence = upcoming[0] if upcoming else occurrences[-1]

    outcome = notify_occurrence(
        db, event, occurrence, kind=KIND_MANUAL, tz=tz, message=payload.message
    )
    return EventNotifyResponse(**outcome)


@router.get("/sync/status")
def calendar_sync_status(db: Session = Depends(get_db)):
    """How fresh the cached calendars are, and which are failing."""
    status = calendar_cache.cache_status(db)
    return {
        "cached": status["cached"],
        "expected": status["expected"],
        "oldest_fetched_at": status["oldest_fetched_at"],
        "failing": status["failing"],
        "interval_minutes": calendar_cache.sync_interval_minutes(db),
    }


@router.post("/sync")
def calendar_sync_now(db: Session = Depends(get_db)):
    """Force a refresh of every external calendar.

    The button behind this exists because the alternative to a cache is not
    "always fresh" but "always slow": somebody who has just added an event
    upstream needs a way to pull it in now without waiting out the interval.
    """
    tz = _tz(db)
    try:
        return calendar_cache.sync_calendars(db, tz)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Sync failed: {exc}") from exc
