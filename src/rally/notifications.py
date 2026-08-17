"""Push notifications to specific event attendees, via Pushover.

Rally's only scheduled job is the 4:00 AM generator, which is exactly the wrong
instrument for "the dentist is in thirty minutes". This module is the other
one. Three things reach a phone through it: a reminder before an occurrence, an
explicit "notify attendees", and a notice that an event was just created or
edited.

Two things it deliberately does not do:

- **Notify the whole family.** The recipients of any notification are the
  event's attendees who have a Pushover key. Buzzing four phones for one
  child's appointment is how a notification feature gets muted for good.
- **Raise.** A push failure is recorded and logged. It cannot fail an API
  request and it cannot fail summary generation, the same discipline
  ``fetch_weather()`` follows.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import requests
from sqlalchemy.orm import Session

from rally.calendars.native import expand_event
from rally.models import (
    Calendar,
    Event,
    EventAttendee,
    EventNotification,
    EventOverride,
    FamilyMember,
    Setting,
)
from rally.utils.settings import local_timezone_name
from rally.utils.timezone import now_utc

PUSHOVER_URL = "https://api.pushover.net/1/messages.json"
PUSHOVER_TIMEOUT_SECONDS = 10

# How late a reminder may still be sent. A push at 4:05 for a 2:30 reminder is
# worse than silence — it is actively misleading about the time — so a window
# missed while Rally was down is dropped rather than replayed.
REMINDER_GRACE_MINUTES = 15

# The furthest ahead a reminder can be scheduled, and therefore how far the
# due-reminder scan has to expand recurrences.
MAX_LEAD_MINUTES = 60 * 24 * 7

KIND_REMINDER = "reminder"
KIND_MANUAL = "manual"
KIND_CREATED = "created"
KIND_UPDATED = "updated"

# How far a change notice will look for the occurrence to describe. Forward
# first — a new event is nearly always in the future — and only then backwards,
# so correcting the title of last Tuesday's appointment still says which one.
CHANGE_WINDOW_DAYS = 366

# Slack either side of a named occurrence date when hunting for it. An edit can
# move an occurrence off its own date, and the moved instance is the one worth
# describing.
CHANGE_DATE_SLACK_DAYS = 7

NOTIFICATION_RETENTION_DAYS = 30
_LAST_CHECK_KEY = "reminder_last_check_at"


class PushoverError(RuntimeError):
    """The provider rejected a message, or could not be reached."""


def app_token(db: Session) -> str:
    """The install's Pushover application token, or an empty string."""
    row = db.query(Setting).filter(Setting.key == "pushover_app_token").first()
    return (row.value or "").strip() if row else ""


def send_pushover(
    token: str,
    user_key: str,
    message: str,
    *,
    title: str = "Rally",
    device: str | None = None,
) -> None:
    """Deliver one message. Raises ``PushoverError`` on any failure.

    Pushover answers ``200`` with ``{"status": 1}`` on success and a ``4xx``
    with an ``errors`` array otherwise; a ``200`` that is not ``status: 1`` is
    still a failure, which is the case a naive ``raise_for_status`` misses.
    """
    payload = {"token": token, "user": user_key, "message": message, "title": title}
    if device:
        payload["device"] = device

    try:
        response = requests.post(PUSHOVER_URL, data=payload, timeout=PUSHOVER_TIMEOUT_SECONDS)
    except Exception as exc:
        raise PushoverError(str(exc)) from exc

    try:
        body = response.json()
    except Exception:
        body = {}

    if response.status_code != 200 or body.get("status") != 1:
        errors = body.get("errors")
        detail = "; ".join(errors) if errors else f"HTTP {response.status_code}"
        raise PushoverError(detail)


def recipients_for_event(db: Session, event_id: int) -> tuple[list[FamilyMember], list[str]]:
    """Split an event's attendees into the reachable and the unreachable.

    Returning both is the point: the UI has to be able to say *sent to Emma,
    Jon has no Pushover key* rather than reporting a bare success while one
    phone stayed silent. A member without a key is not an error — it is the
    default state, and the honest thing is to name it.
    """
    rows = db.query(EventAttendee).filter(EventAttendee.event_id == event_id).all()
    if not rows:
        return [], []

    members = (
        db.query(FamilyMember)
        .filter(FamilyMember.id.in_([row.family_member_id for row in rows]))
        .order_by(FamilyMember.name.asc())
        .all()
    )

    reachable = [m for m in members if (m.pushover_user_key or "").strip()]
    skipped = [m.name for m in members if not (m.pushover_user_key or "").strip()]
    return reachable, skipped


def format_message(event: Event, occurrence, tz: ZoneInfo) -> str:
    """The body of a push: when, and where if known."""
    if occurrence.all_day:
        when = "All day today" if occurrence.start_local_date else "All day"
    else:
        local = occurrence.start.astimezone(tz)
        when = local.strftime("%A %I:%M %p").replace(" 0", " ")
    parts = [when]
    if event.location:
        parts.append(f"at {event.location}")
    return " · ".join(parts)


def stamp_date(day: str) -> str:
    """A local ``YYYY-MM-DD`` date as the compact ``YYYYMMDD`` stamp."""
    return date.fromisoformat(day).strftime("%Y%m%d")


def when_label(occurrence, tz: ZoneInfo) -> str:
    """When an occurrence happens: ``YYYYMMDD`` plus the time on this server.

    The date is read from the occurrence's own local date rather than
    re-derived from the instant, which is what keeps an all-day event off the
    day before. The time is rendered in the install's configured local zone —
    the same clock the calendar screens use — and names that zone, because a
    bare "6:00 PM" in a message that also carries an unpunctuated date stamp is
    exactly the kind of ambiguity this format is meant to remove.
    """
    stamp = stamp_date(occurrence.start_local_date)

    if occurrence.spans_days():
        stamp = f"{stamp}–{stamp_date(occurrence.end_local_date)}"
    if occurrence.all_day:
        return f"{stamp} · All day"

    local = occurrence.start.astimezone(tz)
    clock = local.strftime("%I:%M %p").lstrip("0")
    zone = local.strftime("%Z") or getattr(tz, "key", "")
    return f"{stamp} · {clock} {zone}".strip()


def change_title(event: Event, kind: str) -> str:
    """The push title for a change notice, which leads with what happened."""
    prefix = "New event" if kind == KIND_CREATED else "Updated event"
    return f"{prefix}: {event.title}"


def format_change_message(
    event: Event, occurrence, tz: ZoneInfo, *, kind: str, member_name: str
) -> str:
    """One recipient's copy of "this event changed".

    Addressed by name on purpose. These go to the people an event actually
    concerns, and a message that opens with the reader's own name is one they
    can act on without first working out whether it is theirs.
    """
    opener = "is on the calendar" if kind == KIND_CREATED else "has been updated"
    lines = [f"{member_name}, {event.title} {opener}.", f"When: {when_label(occurrence, tz)}"]
    if event.location:
        lines.append(f"Where: {event.location}")
    if occurrence.recurring:
        lines.append("Repeats — this is the next one.")
    return "\n".join(lines)


def _record(
    db: Session,
    *,
    event_id: int,
    occurrence_date: str,
    member_id: int,
    kind: str,
    status: str,
    detail: str | None = None,
) -> None:
    """Write the send-once row, rewriting a previous failure in place.

    A failed attempt must not consume the dedupe slot, or a brief provider
    outage would permanently swallow the reminder it happened to collide with.
    """
    existing = (
        db.query(EventNotification)
        .filter(
            EventNotification.event_id == event_id,
            EventNotification.occurrence_date == occurrence_date,
            EventNotification.family_member_id == member_id,
            EventNotification.kind == kind,
        )
        .first()
    )
    if existing:
        existing.status = status
        existing.detail = detail
        existing.created_at = now_utc()
        return

    db.add(
        EventNotification(
            event_id=event_id,
            occurrence_date=occurrence_date,
            family_member_id=member_id,
            kind=kind,
            status=status,
            detail=detail,
        )
    )


def notify_occurrence(
    db: Session,
    event: Event,
    occurrence,
    *,
    kind: str,
    tz: ZoneInfo,
    message: str | None = None,
    personalize: Callable[[FamilyMember], str] | None = None,
    title: str | None = None,
) -> dict:
    """Push one occurrence to its attendees, recording each outcome.

    Returns ``{"sent": [...], "skipped": [...], "failed": [...]}`` with names,
    never raising.

    ``personalize`` builds the body per recipient, for messages that address
    somebody by name; without it every attendee gets the same text.
    """
    result: dict[str, list] = {"sent": [], "skipped": [], "failed": []}

    token = app_token(db)
    reachable, skipped = recipients_for_event(db, event.id)
    result["skipped"] = list(skipped)

    if not reachable:
        return result

    if not token:
        # Not an error worth failing a request over: the family simply has not
        # configured Pushover yet, and every attendee is unreachable for the
        # same reason.
        result["skipped"] = sorted(result["skipped"] + [m.name for m in reachable])
        result["error"] = "No Pushover application token configured"
        return result

    body = message or format_message(event, occurrence, tz)
    occurrence_date = occurrence.occurrence_date or occurrence.start_local_date

    for member in reachable:
        try:
            send_pushover(
                token,
                member.pushover_user_key.strip(),
                personalize(member) if personalize else body,
                title=title or event.title,
                device=(member.pushover_device or "").strip() or None,
            )
        except PushoverError as exc:
            print(f"  Pushover failed for {member.name}: {exc}")
            _record(
                db,
                event_id=event.id,
                occurrence_date=occurrence_date,
                member_id=member.id,
                kind=kind,
                status="failed",
                detail=str(exc)[:200],
            )
            result["failed"].append(member.name)
        else:
            _record(
                db,
                event_id=event.id,
                occurrence_date=occurrence_date,
                member_id=member.id,
                kind=kind,
                status="sent",
            )
            result["sent"].append(member.name)

    db.commit()
    return result


def _expand(db: Session, event: Event, window_start: datetime, window_end: datetime, tz: ZoneInfo):
    overrides = db.query(EventOverride).filter(EventOverride.event_id == event.id).all()
    return expand_event(
        event,
        overrides=overrides,
        window_start=window_start,
        window_end=window_end,
        local_tz=tz,
    )


def change_occurrence(
    db: Session,
    event: Event,
    *,
    tz: ZoneInfo,
    occurrence_date: str | None = None,
    now: datetime | None = None,
):
    """The occurrence a change notice should describe, or ``None``.

    An edit scoped to one occurrence is about *that* one, so a named date wins.
    Otherwise the next occurrence that has not finished yet is the one people
    need to know about — "the dentist moved" means the next dentist. A series
    whose occurrences are all in the past falls back to the most recent, which
    is better than a notice that names no date at all.
    """
    now = (now or now_utc()).astimezone(UTC)

    if occurrence_date:
        try:
            target = date.fromisoformat(occurrence_date)
        except ValueError:
            target = None
        if target is not None:
            slack = timedelta(days=CHANGE_DATE_SLACK_DAYS)
            # A wide window and a match on identity, rather than a one-day
            # window: an override can move an occurrence clean off its own date,
            # and the moved instance is the one being asked about.
            found = [
                occurrence
                for occurrence in _expand(
                    db,
                    event,
                    datetime.combine(target, datetime.min.time(), tzinfo=UTC) - slack,
                    datetime.combine(target, datetime.max.time(), tzinfo=UTC) + slack,
                    tz,
                )
                if occurrence.occurrence_date == occurrence_date
            ]
            if found:
                return found[0]

    window = timedelta(days=CHANGE_WINDOW_DAYS)
    upcoming = [o for o in _expand(db, event, now, now + window, tz) if o.end >= now]
    if upcoming:
        return upcoming[0]

    past = _expand(db, event, now - window, now, tz)
    return past[-1] if past else None


def notify_event_change(
    db: Session,
    event: Event,
    *,
    kind: str,
    occurrence_date: str | None = None,
    now: datetime | None = None,
) -> dict:
    """Tell an event's attendees, each by name, that it was created or edited.

    Never raises and never rolls anything back: saving the event is the thing
    the user asked for, and a Pushover outage must not fail it. The same
    discipline as every other send in this module, with more at stake — this
    one runs inside a write request rather than a background pass.
    """
    try:
        tz = ZoneInfo(local_timezone_name(db))
        occurrence = change_occurrence(db, event, tz=tz, occurrence_date=occurrence_date, now=now)
        if occurrence is None:
            # A series edited into having no occurrences at all. There is
            # nothing truthful to say about when it happens, so say nothing.
            return {"sent": [], "skipped": [], "failed": []}

        return notify_occurrence(
            db,
            event,
            occurrence,
            kind=kind,
            tz=tz,
            title=change_title(event, kind),
            personalize=lambda member: format_change_message(
                event, occurrence, tz, kind=kind, member_name=member.name
            ),
        )
    except Exception as exc:  # pragma: no cover - defensive
        print(f"Event {kind} notification failed: {exc}")
        return {"sent": [], "skipped": [], "failed": [], "error": str(exc)}


def _already_sent(db: Session, event_id: int, occurrence_date: str, member_ids: list[int]) -> bool:
    """Whether every reachable attendee already has a successful reminder."""
    if not member_ids:
        return True
    sent = {
        row.family_member_id
        for row in db.query(EventNotification)
        .filter(
            EventNotification.event_id == event_id,
            EventNotification.occurrence_date == occurrence_date,
            EventNotification.kind == KIND_REMINDER,
            EventNotification.status == "sent",
        )
        .all()
    }
    return all(member_id in sent for member_id in member_ids)


def check_due_reminders(db: Session, now: datetime | None = None) -> int:
    """Send every reminder whose moment has arrived. Returns the count sent.

    Pure over the database plus a clock, so the whole behaviour is testable
    without a scheduler: the caller decides what "now" is.
    """
    now = (now or now_utc()).astimezone(UTC)
    tz = ZoneInfo(local_timezone_name(db))

    events = db.query(Event).filter(Event.notify_minutes_before.isnot(None)).all()
    if not events:
        return 0

    calendars = {calendar.id: calendar for calendar in db.query(Calendar).all()}
    sent_count = 0

    # Reminders point backwards from a start, so the scan window runs from now
    # to the furthest lead time ahead, plus the grace period behind.
    window_start = now - timedelta(minutes=REMINDER_GRACE_MINUTES + 1)
    window_end = now + timedelta(minutes=MAX_LEAD_MINUTES)

    for event in events:
        overrides = db.query(EventOverride).filter(EventOverride.event_id == event.id).all()
        calendar = calendars.get(event.calendar_id)
        occurrences = expand_event(
            event,
            overrides=overrides,
            window_start=window_start,
            window_end=window_end,
            local_tz=tz,
            calendar_label=calendar.label if calendar else "Rally",
        )

        reachable, _ = recipients_for_event(db, event.id)
        if not reachable:
            continue
        member_ids = [member.id for member in reachable]

        for occurrence in occurrences:
            # Subtracted from the *resolved occurrence*, never from the series
            # start: a fixed offset from DTSTART is an hour wrong for half the
            # year on any weekly event.
            due_at = occurrence.start - timedelta(minutes=event.notify_minutes_before)
            if due_at > now:
                continue
            if due_at < now - timedelta(minutes=REMINDER_GRACE_MINUTES):
                continue

            occurrence_date = occurrence.occurrence_date or occurrence.start_local_date
            if _already_sent(db, event.id, occurrence_date, member_ids):
                continue

            outcome = notify_occurrence(db, event, occurrence, kind=KIND_REMINDER, tz=tz)
            sent_count += len(outcome["sent"])

    return sent_count


def purge_old_notifications(db: Session, today_local: str) -> int:
    """Drop notification records older than the retention window."""
    cutoff = (
        (datetime.fromisoformat(today_local) - timedelta(days=NOTIFICATION_RETENTION_DAYS))
        .date()
        .isoformat()
    )
    deleted = (
        db.query(EventNotification)
        .filter(EventNotification.occurrence_date < cutoff)
        .delete(synchronize_session=False)
    )
    if deleted:
        db.commit()
    return deleted


def run_due_reminders_once_per_minute(db: Session, now: datetime | None = None) -> int:
    """Opportunistic hook for the API, gated to at most one pass a minute.

    The 4 AM container job lives in ``entrypoint.sh`` and only runs under
    Docker, so a ``dev``-served instance would otherwise never send a reminder
    at all. Same reasoning as the shopping list's retention purge, and the same
    mechanism: a settings row as the gate.
    """
    now = (now or now_utc()).astimezone(UTC)
    stamp = now.strftime("%Y-%m-%dT%H:%M")

    row = db.query(Setting).filter(Setting.key == _LAST_CHECK_KEY).first()
    if row and row.value == stamp:
        return 0

    if row:
        row.value = stamp
    else:
        db.add(Setting(key=_LAST_CHECK_KEY, value=stamp))
    db.commit()

    try:
        return check_due_reminders(db, now)
    except Exception as exc:  # pragma: no cover - defensive
        print(f"Reminder check failed: {exc}")
        return 0


def main() -> int:
    """Run one notification pass. Entry point for the container's minute loop.

    Two jobs share this loop rather than each growing a scheduler:

    - Event reminders, which need minute resolution.
    - The preparedness refresh digest, which needs day resolution but has to be
      checked often enough to catch its configured send time. It gates itself
      on a settings row, so calling it every minute costs one indexed read.

    A failure in either is logged and does not stop the other.
    """
    from rally.database import SessionLocal
    from rally.preparedness import run_daily_digest

    db = SessionLocal()
    sent = 0
    failed = False
    try:
        # Keep the calendar cache warm. This is the reliable path; the API also
        # syncs opportunistically so a dev instance is not left behind. Both
        # gate on the configured interval, so calling this every minute costs
        # one indexed read on all but one pass in fifteen.
        try:
            from zoneinfo import ZoneInfo

            from rally.calendars import cache as calendar_cache
            from rally.utils.settings import local_timezone_name

            summary = calendar_cache.sync_if_stale(db, ZoneInfo(local_timezone_name(db)))
            if summary and (summary["synced"] or summary["failed"]):
                print(
                    f"Calendar sync: {summary['synced']} updated, "
                    f"{summary['unchanged']} unchanged, {summary['failed']} failed"
                )
        except Exception as exc:  # pragma: no cover - the loop must not die
            print(f"Calendar sync failed: {exc}")

        try:
            sent = check_due_reminders(db)
        except Exception as exc:  # pragma: no cover - the loop must not die
            print(f"Reminder check failed: {exc}")
            failed = True

        try:
            digest = run_daily_digest(db)
            if digest.sent:
                print(f"Sent preparedness digest for {digest.count} item(s)")
        except Exception as exc:  # pragma: no cover - the loop must not die
            print(f"Preparedness digest failed: {exc}")
            failed = True
    finally:
        db.close()

    if sent:
        print(f"Sent {sent} reminder(s)")
    return 1 if failed else 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
