"""Preparedness stock: refresh schedules and the daily refresh digest.

Rally already knows how to reach a phone — ``rally.notifications`` holds the
Pushover transport, the install's app token, and each family member's key.
This module reuses all of it rather than growing a second notifier.

What it adds is a different *shape* of notification. An event reminder is
minute-resolution and points at one occurrence for its attendees. A refresh is
day-resolution, concerns the household rather than a person, and there may be a
dozen due at once — so it is one digest, sent once per item per refresh date.

Everything here is a plain function taking the date it should act on. The
scheduler supplies it; nothing in this module reads a clock of its own, which
is what makes the whole path testable without a background task.
"""

from __future__ import annotations

import calendar as cal_module
from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy.orm import Session

from rally.models import FamilyMember, PrepItem, PrepLocation, PrepRefreshNotice, Setting
from rally.utils.settings import local_timezone_name
from rally.utils.timezone import now_utc, today_local

# The sweep only considers items whose refresh date falls inside this window.
# It bounds the query regardless of table size; a reminder window longer than a
# year is not something anybody wants.
HORIZON_DAYS = 365

MODE_NONE = "none"
MODE_DATE = "date"
MODE_INTERVAL = "interval"
MODES = (MODE_NONE, MODE_DATE, MODE_INTERVAL)

# Settings keys. `prep_last_digest_date` is internal bookkeeping and is never
# surfaced in the UI, exactly like `shopping_last_purge_date`.
ENABLED_KEY = "prep_notify_enabled"
TIME_KEY = "prep_notify_time"
DEFAULT_LEAD_KEY = "prep_default_remind_days"
LAST_DIGEST_KEY = "prep_last_digest_date"

DEFAULT_NOTIFY_TIME = "08:00"
DEFAULT_LEAD_DAYS = 14

# Pushover's documented message ceiling. A household with eighty things due
# would otherwise get a 4xx instead of a notification.
MAX_MESSAGE_CHARS = 1024


# ── Date arithmetic ──────────────────────────────────────────────────────────


def _advance_months(year: int, month: int, interval: int) -> tuple[int, int]:
    """Advance year/month by interval months. Same helper as recurrence.py."""
    month += interval
    year += (month - 1) // 12
    month = ((month - 1) % 12) + 1
    return year, month


def add_months(anchor: date, months: int) -> date:
    """Add months to a date, clamping to the target month's length.

    Without the clamp, 31 August + 6 months raises ValueError. 2026-08-31 + 6
    becomes 2027-02-28. Lossy on purpose: the anchor is re-set from the real
    refresh date each cycle, so the drift never compounds past one boundary.
    """
    y, m = _advance_months(anchor.year, anchor.month, months)
    return date(y, m, min(anchor.day, cal_module.monthrange(y, m)[1]))


def parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def lead_days(item: PrepItem, default_lead: int) -> int:
    """This item's reminder lead time in days.

    A blank per-item value inherits the household default from settings; an
    explicit ``0`` means "announce it on the day" and is preserved. That
    distinction is why this is an ``is None`` check and not ``or`` — `or` reads
    a deliberate 0 as "unset" and quietly replaces it with 14.
    """
    return default_lead if item.remind_days_before is None else item.remind_days_before


def notify_on(item: PrepItem, default_lead: int) -> date | None:
    """The first date this item should be announced on.

    ``default_lead`` is required rather than defaulted. The setting behind it
    existed from the start, was shown in Settings as 14 days, and was applied
    nowhere: `notify_on` took the item alone and fell back to `or 0`. A
    parameter with a safe-looking default is what let that go unnoticed, so
    there isn't one — every caller has to say which lead it means.
    """
    target = parse_date(item.next_refresh_date)
    if target is None:
        return None
    return target - timedelta(days=lead_days(item, default_lead))


def is_due(item: PrepItem, on_date: date, default_lead: int) -> bool:
    """True once the item's reminder window has opened."""
    start = notify_on(item, default_lead)
    return start is not None and on_date >= start


def status_of(item: PrepItem, on_date: date, default_lead: int) -> str:
    """Derived display state — never stored. ``ok`` | ``due`` | ``overdue``."""
    target = parse_date(item.next_refresh_date)
    if target is None:
        return "ok"
    if on_date > target:
        return "overdue"
    if is_due(item, on_date, default_lead):
        return "due"
    return "ok"


def days_until(item: PrepItem, on_date: date) -> int | None:
    target = parse_date(item.next_refresh_date)
    if target is None:
        return None
    return (target - on_date).days


def today_for(db: Session) -> date:
    """Today in the family's configured timezone."""
    return today_local(local_timezone_name(db))


# ── Settings helpers ─────────────────────────────────────────────────────────


def _setting(db: Session, key: str, default: str) -> str:
    row = db.query(Setting).filter(Setting.key == key).first()
    value = (row.value or "").strip() if row else ""
    return value or default


def notifications_enabled(db: Session) -> bool:
    return _setting(db, ENABLED_KEY, "true").lower() == "true"


def default_lead_days(db: Session) -> int:
    try:
        return int(_setting(db, DEFAULT_LEAD_KEY, str(DEFAULT_LEAD_DAYS)))
    except ValueError:
        return DEFAULT_LEAD_DAYS


def notify_time(db: Session) -> tuple[int, int]:
    raw = _setting(db, TIME_KEY, DEFAULT_NOTIFY_TIME)
    try:
        hh, mm = (int(part) for part in raw.split(":", 1))
        return hh, mm
    except ValueError, TypeError:
        return 8, 0


# ── Marking an item refreshed ────────────────────────────────────────────────


def mark_refreshed(db: Session, item: PrepItem, on: date) -> PrepItem:
    """Record a refresh and recompute the next one.

    An ``interval`` item re-anchors on the *actual* refresh date: for physical
    stock the clock starts when you swap it, so a case rotated three weeks late
    expires three weeks later. This is the recurring-todo
    ``custom_rule["next_due_from"] == "completion_date"`` anchor, applied here
    as the only sensible default.

    A spent ``date`` item becomes unscheduled rather than inventing a new date.
    Rally has no idea when the *next* case expires — that number is stamped on
    the packaging and is not derivable — and guessing would put a confidently
    wrong date in the one feature whose whole job is being right about dates.

    Notices are deliberately untouched: the new date yields a new notice key,
    which is what re-arms the item.
    """
    item.last_refreshed_on = on.isoformat()

    if item.refresh_mode == MODE_INTERVAL and item.refresh_interval_months:
        item.next_refresh_date = add_months(on, item.refresh_interval_months).isoformat()
    elif item.refresh_mode == MODE_DATE:
        item.next_refresh_date = None
        item.refresh_mode = MODE_NONE

    db.commit()
    db.refresh(item)
    return item


# ── The sweep ────────────────────────────────────────────────────────────────


@dataclass
class DigestResult:
    """What a digest pass did, or would have done under ``dry_run``."""

    due_items: list[PrepItem] = field(default_factory=list)
    sent_to: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    dry_run: bool = False
    skipped_reason: str | None = None

    @property
    def count(self) -> int:
        return len(self.due_items)

    @property
    def sent(self) -> bool:
        return bool(self.sent_to)


def notice_key(item_id: int, refresh_date: str) -> str:
    return f"{item_id}:{refresh_date}"


def find_due_items(db: Session, on_date: date) -> list[PrepItem]:
    """Items whose reminder window has opened and that are not yet announced.

    Candidates come from an indexed range query; the lead-time predicate is
    applied in Python because ``remind_days_before`` is per-row and cannot go
    into the SQL cheaply — nor does it need to at this table size.
    """
    horizon = (on_date + timedelta(days=HORIZON_DAYS)).isoformat()
    candidates = (
        db.query(PrepItem)
        .filter(PrepItem.next_refresh_date.isnot(None), PrepItem.next_refresh_date <= horizon)
        .all()
    )

    # One settings read for the whole sweep rather than one per row.
    default_lead = default_lead_days(db)
    due = [item for item in candidates if is_due(item, on_date, default_lead)]
    if not due:
        return []

    keys = {notice_key(item.id, item.next_refresh_date) for item in due}
    announced = {
        row.notice_key
        for row in db.query(PrepRefreshNotice).filter(PrepRefreshNotice.notice_key.in_(keys)).all()
    }

    unannounced = [
        item for item in due if notice_key(item.id, item.next_refresh_date) not in announced
    ]
    # Overdue sorts first because next_refresh_date is ascending and an overdue
    # date is by definition the earliest.
    unannounced.sort(key=lambda i: (i.next_refresh_date or "", i.name.lower()))
    return unannounced


def _location_names(db: Session) -> dict[int, str]:
    return {loc.id: loc.name for loc in db.query(PrepLocation).all()}


def build_digest(db: Session, items: list[PrepItem], on_date: date) -> tuple[str, str]:
    """Render the digest title and body.

    The body carries real content rather than a teaser. Rally is only reachable
    over Tailscale, so the link is dead whenever the tunnel is down and the
    message has to stand alone — the same reasoning the event reminder follows.
    """
    names = _location_names(db)
    count = len(items)
    title = f"Rally — {count} item{'s' if count != 1 else ''} to refresh"

    lines = []
    for item in items:
        where = names.get(item.location_id, "Unassigned")
        target = parse_date(item.next_refresh_date)
        when = (
            f"OVERDUE since {item.next_refresh_date}"
            if target and on_date > target
            else f"due {item.next_refresh_date}"
        )
        parts = [item.name, where, when]
        if item.quantity:
            parts.append(item.quantity)
        lines.append(" · ".join(parts))

    body = "\n".join(lines)
    if len(body) > MAX_MESSAGE_CHARS:
        # Cut on a line boundary and say how many were dropped, rather than
        # letting Pushover reject the whole message.
        kept: list[str] = []
        used = 0
        for line in lines:
            if used + len(line) + 1 > MAX_MESSAGE_CHARS - 40:
                break
            kept.append(line)
            used += len(line) + 1
        remaining = count - len(kept)
        kept.append(f"…and {remaining} more — open Rally for the full list.")
        body = "\n".join(kept)

    return title, body


def digest_recipients(db: Session) -> tuple[list[FamilyMember], list[str]]:
    """Everyone with a Pushover key, plus the names of everyone without one.

    Event reminders go to an event's attendees, never the whole family, because
    buzzing four phones for one child's appointment is how a feature gets
    muted. A refresh digest is the opposite case: the water drums belong to the
    household, not to a person, so the household is the right audience.

    Returning the unreachable names too keeps the settings page honest — "sent
    to Emma, Jon has no Pushover key" is a different claim from "it worked".
    """
    members = db.query(FamilyMember).order_by(FamilyMember.name.asc()).all()
    reachable = [m for m in members if (m.pushover_user_key or "").strip()]
    skipped = [m.name for m in members if not (m.pushover_user_key or "").strip()]
    return reachable, skipped


def send_digest(db: Session, on_date: date, *, dry_run: bool = False) -> DigestResult:
    """Announce every due item in one push, and record what was announced.

    Notices are written **only for members that were actually reached**. A
    total failure records nothing, so the next pass retries rather than
    silently marking the digest delivered — the same discipline
    ``_record`` follows for event reminders, where a failure must not consume
    the dedupe slot.
    """
    from rally.notifications import PushoverError, app_token, send_pushover

    result = DigestResult(dry_run=dry_run)

    if not notifications_enabled(db):
        result.skipped_reason = "preparedness notifications are turned off"
        return result

    result.due_items = find_due_items(db, on_date)
    if not result.due_items:
        # Silence is the correct output for a quiet day.
        result.skipped_reason = "nothing due"
        return result

    if dry_run:
        return result

    token = app_token(db)
    if not token:
        result.skipped_reason = "no Pushover application token configured"
        return result

    reachable, skipped = digest_recipients(db)
    result.skipped = list(skipped)
    if not reachable:
        result.skipped_reason = "no family member has a Pushover key"
        return result

    title, message = build_digest(db, result.due_items, on_date)

    for member in reachable:
        try:
            send_pushover(
                token,
                member.pushover_user_key.strip(),
                message,
                title=title,
                device=(member.pushover_device or "").strip() or None,
            )
            result.sent_to.append(member.name)
        except PushoverError as exc:
            result.failed.append(f"{member.name}: {exc}")

    if not result.sent_to:
        # Nobody got it. Record nothing so the next pass tries again.
        return result

    stamp = on_date.isoformat()
    recipients = ", ".join(result.sent_to)
    for item in result.due_items:
        db.add(
            PrepRefreshNotice(
                notice_key=notice_key(item.id, item.next_refresh_date),
                item_id=item.id,
                refresh_date=item.next_refresh_date,
                sent_on=stamp,
                recipients=recipients,
            )
        )
    db.commit()
    return result


def run_daily_digest(db: Session, now=None) -> DigestResult:
    """Send today's digest if it is time and it has not gone out yet.

    Three gates, all cheap: the feature switch, the configured local time, and
    a once-per-local-day settings marker. The marker records *"we attempted
    today"*; the notice rows record *"this item was actually announced"*. They
    are deliberately separate, so a provider outage retries tomorrow instead of
    every sixty seconds, while a genuinely undelivered item stays unannounced
    and is picked up again.
    """
    tz_name = local_timezone_name(db)
    local_now = (now or now_utc()).astimezone(_zone(tz_name))
    on_date = local_now.date()

    result = DigestResult()

    if not notifications_enabled(db):
        result.skipped_reason = "preparedness notifications are turned off"
        return result

    hh, mm = notify_time(db)
    if (local_now.hour, local_now.minute) < (hh, mm):
        result.skipped_reason = "before the configured send time"
        return result

    stamp = on_date.isoformat()
    row = db.query(Setting).filter(Setting.key == LAST_DIGEST_KEY).first()
    if row and row.value == stamp:
        result.skipped_reason = "already ran today"
        return result

    if row:
        row.value = stamp
    else:
        db.add(Setting(key=LAST_DIGEST_KEY, value=stamp))
    db.commit()

    return send_digest(db, on_date)


def _zone(tz_name: str):
    from zoneinfo import ZoneInfo

    try:
        return ZoneInfo(tz_name)
    except Exception:
        from datetime import UTC

        return UTC
