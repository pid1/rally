"""Busy-only tools: free/busy and pending bookings for an agent that is not trusted.

These three routes are what an agent on a connection given these tools uses
to answer "is the family free Tuesday at 2?" and to request an appointment.
Any connection can be given them, OAuth or token. A voice assistant is the
usual case: anyone it talks to can try to prompt-inject it. They are the security
boundary, so they are written to be safe with the agent fully manipulated:

- **Nothing private comes back.** Availability is merged busy blocks rounded
  outward to 15 minutes (``rally.calendars.busy``). No title, description,
  location, attendee, uid, calendar name or event count, in a result *or* an
  error: every error message here is a fixed string.
- **Bounded questions.** A window is at most ``MAX_WINDOW_DAYS`` long, starts
  no earlier than a day ago and ends within ``HORIZON_DAYS``. Times are
  ISO 8601 with a UTC offset (a naive time is refused, never guessed at);
  answers are in the family's timezone, with its offset.
- **Rate limited per connection** (``READ_LIMIT``, ``WRITE_LIMIT``).
- **Bookings are pending.** A booking is a native event tagged with the
  connection that made it, marked pending until someone in the family
  confirms it on the calendar, and refused over busy time. The agent gets
  back only an opaque confirmation id (not the event id or uid) to read to
  the person it is talking to. It cannot change, cancel or confirm anything:
  only the family can.
- **Audited** like every route, plus one ``busy.<tool>`` event per call
  carrying the result size.

Only MCP connections may call them: a request without an MCP identity is
refused, because a booking has to be tagged with the connection that made it.
"""

from __future__ import annotations

import hashlib
import re
import secrets
import threading
import time as monotonic_time
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from rally.audit import context as audit_context
from rally.audit import recorder
from rally.calendars import busy as busy_calc
from rally.database import get_db
from rally.models import Event, EventAttendee, FamilyMember
from rally.utils.settings import local_timezone_name
from rally.utils.timezone import now_utc

router = APIRouter(prefix="/api/busy", tags=["busy"])

MAX_WINDOW_DAYS = 60
# How far ahead a question or a booking may reach. Inside the external
# calendar cache's own horizon (``cache.CACHE_DAYS_FORWARD``), so a far-future
# window can never read as free just because nothing was synced that far.
HORIZON_DAYS = 180
MAX_PAST = timedelta(days=1)
MAX_MEMBERS = 10
MAX_SLOTS = 20
MIN_DURATION_MINUTES = 15
MAX_DURATION_MINUTES = 8 * 60
MAX_BUFFER_MINUTES = 120

TITLE_MAX = 100
NOTES_MAX = 1000
CONTACT_NAME_MAX = 100
CONTACT_PHONE_MAX = 32
PHONE_PATTERN = re.compile(r"^[0-9+()\-. ]{3,32}$")

# Confirmation ids are read aloud, so they avoid look-alikes (0/O, 1/I/L):
# 8 characters of a 31-letter alphabet, about 40 bits, shown as XXXX-XXXX.
# Guessing one also needs the booking connection, and is rate limited.
CONFIRMATION_ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"
CONFIRMATION_LENGTH = 8

TIME_FORMAT_ERROR = (
    "Times must be ISO 8601 with a UTC offset, for example 2026-11-05T14:00:00-06:00"
)
UNKNOWN_MEMBER = "Unknown family member"
UNAVAILABLE = "Availability is temporarily unavailable. Try again later."
NOT_AVAILABLE = "That time is not available"


# --- Who is asking -------------------------------------------------------------


def _connection_id() -> str | None:
    """The MCP connection making this request, or None outside MCP.

    Phase 1's per-connection identity: rally-mcp names the connection and the
    audit middleware has already checked it is enabled, allowed from this IP and
    holding this tool, so bookings are tagged with it.
    """
    ctx = audit_context.current()
    if ctx.source != audit_context.SOURCE_MCP or ctx.actor_type != audit_context.ACTOR_MCP:
        return None
    return ctx.connection_id


def _require_connection() -> str:
    connection = _connection_id()
    if not connection:
        raise HTTPException(status_code=403, detail="Busy-only tools are for MCP connections")
    return connection


# --- Rate limits -----------------------------------------------------------------


class TokenBucket:
    """An in-process token bucket per key: ``capacity`` calls, refilled over ``period``."""

    def __init__(self, capacity: int, period_seconds: float) -> None:
        self.capacity = capacity
        self.rate = capacity / period_seconds
        self._buckets: dict[str, tuple[float, float]] = {}
        self._lock = threading.Lock()

    def take(self, key: str) -> bool:
        now = monotonic_time.monotonic()
        with self._lock:
            tokens, updated = self._buckets.get(key, (float(self.capacity), now))
            tokens = min(self.capacity, tokens + (now - updated) * self.rate)
            if tokens < 1:
                self._buckets[key] = (tokens, now)
                return False
            self._buckets[key] = (tokens - 1, now)
            return True

    def reset(self) -> None:
        with self._lock:
            self._buckets.clear()


# Availability and slot questions: 30 at once, 30 a minute after that.
READ_LIMIT = TokenBucket(30, 60)
# Bookings: 5 at once, 5 an hour after that.
WRITE_LIMIT = TokenBucket(5, 3600)


def _limited(bucket: TokenBucket):
    def dependency(connection: str = Depends(_require_connection)) -> str:
        if not bucket.take(connection):
            raise HTTPException(status_code=429, detail="Too many requests. Try again later.")
        return connection

    return dependency


read_limited = _limited(READ_LIMIT)
write_limited = _limited(WRITE_LIMIT)


# --- Input -------------------------------------------------------------------------


def _parse_instant(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat((value or "").strip())
    except ValueError:
        raise HTTPException(status_code=422, detail=TIME_FORMAT_ERROR) from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise HTTPException(status_code=422, detail=TIME_FORMAT_ERROR)
    return parsed.astimezone(UTC)


def _window(start: str, end: str) -> tuple[datetime, datetime]:
    lo, hi = _parse_instant(start), _parse_instant(end)
    now = now_utc()
    if hi <= lo:
        raise HTTPException(status_code=422, detail="The end must be after the start")
    if hi - lo > timedelta(days=MAX_WINDOW_DAYS):
        raise HTTPException(
            status_code=422, detail=f"The window may not exceed {MAX_WINDOW_DAYS} days"
        )
    if lo < now - MAX_PAST:
        raise HTTPException(status_code=422, detail="The window may not start in the past")
    if hi > now + timedelta(days=HORIZON_DAYS):
        raise HTTPException(
            status_code=422, detail=f"The window must end within {HORIZON_DAYS} days"
        )
    return lo, hi


def _time_of_day(value: str | None, default: time) -> time:
    if value is None or not value.strip():
        return default
    if not re.fullmatch(r"\d{2}:\d{2}", value.strip()):
        raise HTTPException(status_code=422, detail="Times of day are HH:MM, 24-hour")
    try:
        return time.fromisoformat(value.strip())
    except ValueError:
        raise HTTPException(status_code=422, detail="Times of day are HH:MM, 24-hour") from None


def _members(db: Session, names: list[str] | None) -> tuple[set[str] | None, list[int]]:
    """Lowercased names to filter on, and their member ids. ``None`` means everyone."""
    wanted = [n.strip() for n in names or [] if n and n.strip()]
    if not wanted:
        return None, []
    if len(wanted) > MAX_MEMBERS or any(len(n) > CONTACT_NAME_MAX for n in wanted):
        raise HTTPException(status_code=422, detail=UNKNOWN_MEMBER)
    by_name = {m.name.strip().lower(): m for m in db.query(FamilyMember).all() if m.name}
    found = []
    for name in wanted:
        member = by_name.get(name.lower())
        if member is None:
            # The name is not echoed, and the family's names are not listed:
            # an unknown caller is not told who lives here.
            raise HTTPException(status_code=422, detail=UNKNOWN_MEMBER)
        found.append(member)
    return {m.name.strip().lower() for m in found}, sorted({m.id for m in found})


def _clean(value: str | None, *, multiline: bool = False) -> str:
    """Drop control and format characters; collapse whitespace."""
    if not value:
        return ""
    kept = []
    for ch in unicodedata.normalize("NFC", value):
        if ch == "\n" and multiline:
            kept.append(ch)
        elif unicodedata.category(ch)[0] == "C":
            kept.append(" ")
        else:
            kept.append(ch)
    text = "".join(kept)
    if multiline:
        lines = [" ".join(line.split()) for line in text.split("\n")]
        return "\n".join(lines).strip()
    return " ".join(text.split())


def _tz(db: Session) -> ZoneInfo:
    return ZoneInfo(local_timezone_name(db))


def _busy(db: Session, *, start: datetime, end: datetime, tz: ZoneInfo, members):
    try:
        return busy_calc.busy_blocks(
            db, window_start=start, window_end=end, local_tz=tz, members=members
        )
    except busy_calc.AvailabilityUnavailable:
        raise HTTPException(status_code=503, detail=UNAVAILABLE) from None


# --- Output ------------------------------------------------------------------------


class TimeBlock(BaseModel):
    start: str  # ISO 8601, family's timezone, with offset
    end: str  # exclusive


class AvailabilityResponse(BaseModel):
    timezone: str
    start: str
    end: str
    busy: list[TimeBlock]
    free: list[TimeBlock]


class FreeSlotsResponse(BaseModel):
    timezone: str
    duration_minutes: int
    slots: list[TimeBlock]


class BookingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start: str = Field(max_length=40)
    end: str = Field(max_length=40)
    title: str = Field(min_length=1, max_length=TITLE_MAX)
    contact_name: str | None = Field(default=None, max_length=CONTACT_NAME_MAX)
    contact_phone: str | None = Field(default=None, max_length=CONTACT_PHONE_MAX)
    notes: str | None = Field(default=None, max_length=NOTES_MAX)
    members: list[str] | None = Field(default=None, max_length=MAX_MEMBERS)


class BookingResponse(BaseModel):
    confirmation_id: str


def _iso(value: datetime, tz: ZoneInfo) -> str:
    return value.astimezone(tz).isoformat(timespec="seconds")


def _blocks(blocks, tz: ZoneInfo) -> list[TimeBlock]:
    return [TimeBlock(start=_iso(b.start, tz), end=_iso(b.end, tz)) for b in blocks]


def _record(db: Session, action: str, *, commit: bool = True, **after) -> None:
    recorder.record(db, action=action, after=after, commit=commit)


# --- Routes ------------------------------------------------------------------------


@router.get("/availability", response_model=AvailabilityResponse)
def check_availability(
    start: str = Query(..., max_length=40, description="ISO 8601 with offset"),
    end: str = Query(..., max_length=40, description="ISO 8601 with offset, exclusive"),
    members: list[str] = Query(default=[], description="Family member names; omit for everyone"),
    connection: str = Depends(read_limited),
    db: Session = Depends(get_db),
):
    """Busy and free blocks in a window, and nothing else."""
    lo, hi = _window(start, end)
    names, _ = _members(db, members)
    tz = _tz(db)
    busy = _busy(db, start=lo, end=hi, tz=tz, members=names)
    free = busy_calc.free_blocks(busy, window_start=lo, window_end=hi)
    _record(
        db,
        "busy.check_availability",
        result_size=len(busy) + len(free),
        busy_blocks=len(busy),
        free_blocks=len(free),
    )
    return AvailabilityResponse(
        timezone=str(tz),
        start=_iso(busy_calc.floor_slot(lo), tz),
        end=_iso(busy_calc.ceil_slot(hi), tz),
        busy=_blocks(busy, tz),
        free=_blocks(free, tz),
    )


@router.get("/free-slots", response_model=FreeSlotsResponse)
def find_free_slots(
    window_start: str = Query(..., max_length=40, description="ISO 8601 with offset"),
    window_end: str = Query(..., max_length=40, description="ISO 8601 with offset"),
    duration_minutes: int = Query(..., ge=MIN_DURATION_MINUTES, le=MAX_DURATION_MINUTES),
    members: list[str] = Query(default=[]),
    earliest_time: str | None = Query(None, max_length=5, description="HH:MM local, default 08:00"),
    latest_time: str | None = Query(None, max_length=5, description="HH:MM local, default 18:00"),
    weekdays_only: bool = Query(False),
    buffer_minutes: int = Query(0, ge=0, le=MAX_BUFFER_MINUTES),
    max_results: int = Query(5, ge=1, le=MAX_SLOTS),
    connection: str = Depends(read_limited),
    db: Session = Depends(get_db),
):
    """Candidate slots of a given length, earliest first, that fit the constraints."""
    lo, hi = _window(window_start, window_end)
    constraints = busy_calc.SlotConstraints(
        earliest=_time_of_day(earliest_time, time(8, 0)),
        latest=_time_of_day(latest_time, time(18, 0)),
        weekdays_only=weekdays_only,
        buffer_minutes=buffer_minutes,
    )
    if constraints.latest <= constraints.earliest:
        raise HTTPException(status_code=422, detail="latest_time must be after earliest_time")
    names, _ = _members(db, members)
    tz = _tz(db)
    pad = timedelta(minutes=buffer_minutes)
    busy = _busy(db, start=lo - pad, end=hi + pad, tz=tz, members=names)
    slots = busy_calc.free_slots(
        busy,
        window_start=lo,
        window_end=hi,
        duration=timedelta(minutes=duration_minutes),
        local_tz=tz,
        now=now_utc(),
        constraints=constraints,
        max_results=max_results,
    )
    _record(db, "busy.find_free_slots", result_size=len(slots))
    return FreeSlotsResponse(
        timezone=str(tz), duration_minutes=duration_minutes, slots=_blocks(slots, tz)
    )


# One booking at a time, so two calls cannot both see the same hour free.
_BOOKING_LOCK = threading.Lock()


def _confirmation_hash(confirmation_id: str) -> str:
    return hashlib.sha256(confirmation_id.encode()).hexdigest()


def _new_confirmation(db: Session) -> tuple[str, str]:
    while True:
        raw = "".join(secrets.choice(CONFIRMATION_ALPHABET) for _ in range(CONFIRMATION_LENGTH))
        digest = _confirmation_hash(raw)
        if not db.query(Event.id).filter(Event.confirmation_hash == digest).first():
            return f"{raw[:4]}-{raw[4:]}", digest


@dataclass(frozen=True)
class _Booking:
    start: datetime
    end: datetime
    title: str
    notes: str
    contact_name: str
    contact_phone: str


def _validated_booking(payload: BookingRequest) -> _Booking:
    start, end = _parse_instant(payload.start), _parse_instant(payload.end)
    now = now_utc()
    if end <= start:
        raise HTTPException(status_code=422, detail="The end must be after the start")
    if end - start < timedelta(minutes=5) or end - start > timedelta(minutes=MAX_DURATION_MINUTES):
        raise HTTPException(
            status_code=422,
            detail=f"An appointment is 5 minutes to {MAX_DURATION_MINUTES // 60} hours long",
        )
    if start <= now:
        raise HTTPException(status_code=422, detail="An appointment must start in the future")
    if end > now + timedelta(days=HORIZON_DAYS):
        raise HTTPException(
            status_code=422, detail=f"An appointment must be within {HORIZON_DAYS} days"
        )
    title = _clean(payload.title)
    if not title:
        raise HTTPException(status_code=422, detail="A title is required")
    phone = _clean(payload.contact_phone)
    if phone and not PHONE_PATTERN.fullmatch(phone):
        raise HTTPException(
            status_code=422,
            detail="contact_phone may contain only digits, spaces and + ( ) - .",
        )
    return _Booking(
        start=start,
        end=end,
        title=title,
        notes=_clean(payload.notes, multiline=True),
        contact_name=_clean(payload.contact_name),
        contact_phone=phone,
    )


@router.post("/appointments", response_model=BookingResponse, status_code=201)
def book_appointment(
    payload: BookingRequest,
    connection: str = Depends(write_limited),
    db: Session = Depends(get_db),
):
    """Book a pending appointment into free time; returns only a confirmation id."""
    from rally.routers.events import _default_native_calendar

    booking = _validated_booking(payload)
    names, member_ids = _members(db, payload.members)
    tz = _tz(db)
    with _BOOKING_LOCK:
        busy = _busy(db, start=booking.start, end=booking.end, tz=tz, members=names)
        if any(b.start < booking.end and b.end > booking.start for b in busy):
            raise HTTPException(status_code=409, detail=NOT_AVAILABLE)

        calendar = _default_native_calendar(db)
        confirmation_id, digest = _new_confirmation(db)
        local_start = booking.start.astimezone(tz)
        local_end = booking.end.astimezone(tz)
        if local_end.time() == time.min:
            # Ending at midnight belongs to the day it started, as in the form.
            local_end -= timedelta(seconds=1)
        event = Event(
            calendar_id=calendar.id,
            uid=f"rally-{uuid.uuid4().hex[:16]}@rally.local",
            title=booking.title,
            description=booking.notes or None,
            all_day=False,
            start_utc=booking.start.replace(tzinfo=None),
            end_utc=booking.end.replace(tzinfo=None),
            start_date=local_start.date().isoformat(),
            end_date=local_end.date().isoformat(),
            tzid=str(tz),
            source=connection,
            pending_confirmation=True,
            confirmation_hash=digest,
            contact_name=booking.contact_name or None,
            contact_phone=booking.contact_phone or None,
        )
        db.add(event)
        db.flush()
        for member_id in member_ids:
            db.add(EventAttendee(event_id=event.id, family_member_id=member_id))
        _record(db, "busy.book_appointment", commit=False, result_size=1, event_id=event.id)
        db.commit()
    return BookingResponse(confirmation_id=confirmation_id)
