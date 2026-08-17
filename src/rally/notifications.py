"""Push notifications to specific event attendees, via Pushover.

Rally's only scheduled job is the 4:00 AM generator, which is exactly the wrong
instrument for "the dentist is in thirty minutes". This module is the other
one. Three things reach a phone through it: a reminder before an occurrence, an
explicit "notify attendees", and a notice that an event was just added, changed
or removed.

Two things it deliberately does not do:

- **Notify the whole family.** The recipients of any notification are the
  event's attendees who have a Pushover key. Buzzing four phones for one
  child's appointment is how a notification feature gets muted for good.
- **Raise.** A push failure is recorded and logged. It cannot fail an API
  request and it cannot fail summary generation, the same discipline
  ``fetch_weather()`` follows.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
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
KIND_DELETED = "deleted"

# What a change notice calls itself. The three read as a set on a lock screen —
# the word that differs is the first one that matters.
CHANGE_LABELS = {
    KIND_CREATED: "Calendar Addition",
    KIND_UPDATED: "Calendar Modification",
    KIND_DELETED: "Calendar Deletion",
}

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


def when_label(occurrence, tz: ZoneInfo) -> str:
    """When an occurrence happens: the local date, then the time on this server.

    The date is read from the occurrence's own local date rather than
    re-derived from the instant, which is what keeps an all-day event off the
    day before. The time is rendered in the install's configured local zone —
    the same clock the calendar screens use — and names that zone, because a
    bare "5:30 PM" is only unambiguous to somebody who already knows which zone
    the server keeps.
    """
    when = occurrence.start_local_date
    if occurrence.spans_days():
        when = f"{when} – {occurrence.end_local_date}"
    if occurrence.all_day:
        return f"{when} · All day"

    local = occurrence.start.astimezone(tz)
    clock = local.strftime("%I:%M %p").lstrip("0")
    zone = local.strftime("%Z") or getattr(tz, "key", "")
    return f"{when} · {clock} {zone}".strip()


def change_title(event_title: str, kind: str) -> str:
    """The push title for a change notice: what happened, then to what."""
    return f"{CHANGE_LABELS.get(kind, 'Calendar Update')}: {event_title}"


def attendee_names(db: Session, event_id: int) -> list[str]:
    """Every attendee's name, in the order they were put on the event.

    All of them, not only the reachable ones: the list describes the event, and
    somebody without a Pushover key is still going to the dentist.
    """
    rows = (
        db.query(EventAttendee)
        .filter(EventAttendee.event_id == event_id)
        .order_by(EventAttendee.id.asc())
        .all()
    )
    if not rows:
        return []

    names = {
        member.id: member.name
        for member in db.query(FamilyMember)
        .filter(FamilyMember.id.in_([row.family_member_id for row in rows]))
        .all()
    }
    return [names[row.family_member_id] for row in rows if row.family_member_id in names]


def format_change_message(
    event: Event, occurrence, tz: ZoneInfo, *, attendees: Sequence[str]
) -> str:
    """The body of a change notice: when, where, and who it is for.

    One body for everybody, deliberately. The recipients are the event's
    attendees, so a message addressed to no one in particular is still
    addressed to exactly the right people — and it is the same text the family
    can compare notes on rather than four slightly different ones.
    """
    lines = [f"When: {when_label(occurrence, tz)}"]
    if event.location:
        lines.append(f"Where: {event.location}")
    if attendees:
        lines.append(f"Attendees: {', '.join(attendees)}")
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


def _deliver(
    db: Session,
    members: Sequence[FamilyMember],
    *,
    body: str,
    title: str,
    kind: str,
    event_id: int,
    occurrence_date: str,
    skipped: Sequence[str] = (),
    record: bool = True,
) -> dict:
    """Send one body to several people, recording each outcome.

    The single place a push is attempted, so the reporting, the send-once row
    and the "a failure is data, not an exception" rule each exist once.

    ``record=False`` is for a notice about an event that no longer exists: its
    ``event_notifications`` rows were cascaded away with it, and writing a fresh
    one would leave an orphan pointing at a deleted id.
    """
    result: dict[str, list] = {"sent": [], "skipped": list(skipped), "failed": []}

    if not members:
        return result

    token = app_token(db)
    if not token:
        # Not an error worth failing a request over: the family simply has not
        # configured Pushover yet, and every attendee is unreachable for the
        # same reason.
        result["skipped"] = sorted(result["skipped"] + [m.name for m in members])
        result["error"] = "No Pushover application token configured"
        return result

    for member in members:
        try:
            send_pushover(
                token,
                member.pushover_user_key.strip(),
                body,
                title=title,
                device=(member.pushover_device or "").strip() or None,
            )
        except PushoverError as exc:
            print(f"  Pushover failed for {member.name}: {exc}")
            if record:
                _record(
                    db,
                    event_id=event_id,
                    occurrence_date=occurrence_date,
                    member_id=member.id,
                    kind=kind,
                    status="failed",
                    detail=str(exc)[:200],
                )
            result["failed"].append(member.name)
        else:
            if record:
                _record(
                    db,
                    event_id=event_id,
                    occurrence_date=occurrence_date,
                    member_id=member.id,
                    kind=kind,
                    status="sent",
                )
            result["sent"].append(member.name)

    db.commit()
    return result


def notify_occurrence(
    db: Session,
    event: Event,
    occurrence,
    *,
    kind: str,
    tz: ZoneInfo,
    message: str | None = None,
) -> dict:
    """Push one occurrence to its attendees, recording each outcome.

    Returns ``{"sent": [...], "skipped": [...], "failed": [...]}`` with names,
    never raising.
    """
    reachable, skipped = recipients_for_event(db, event.id)
    return _deliver(
        db,
        reachable,
        body=message or format_message(event, occurrence, tz),
        title=event.title,
        kind=kind,
        event_id=event.id,
        occurrence_date=occurrence.occurrence_date or occurrence.start_local_date,
        skipped=skipped,
    )


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


@dataclass
class ChangeNotice:
    """A change notice worked out in full before the change lands.

    Deletion is why this is a two-step. A delete destroys the very things the
    message is made of — the occurrence, the attendee list, sometimes the event
    row itself — so the text and the recipients have to be resolved *first* and
    delivered *after*, once the change is safely committed. Additions and edits
    do not need the split, but they take the same path rather than growing a
    second one.
    """

    title: str
    body: str
    kind: str
    event_id: int
    occurrence_date: str
    members: list[FamilyMember] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    record: bool = True


def plan_change_notice(
    db: Session,
    event: Event,
    *,
    kind: str,
    occurrence_date: str | None = None,
    now: datetime | None = None,
    record: bool = True,
) -> ChangeNotice | None:
    """Work out what to say about a change, and to whom, before making it.

    Returns ``None`` when there is nothing truthful to say — no occurrence to
    describe — rather than a notice with a hole in it. Never raises: this runs
    inside a write request, and the calendar edit is what the user asked for.
    """
    try:
        tz = ZoneInfo(local_timezone_name(db))
        occurrence = change_occurrence(db, event, tz=tz, occurrence_date=occurrence_date, now=now)
        if occurrence is None:
            return None

        reachable, skipped = recipients_for_event(db, event.id)
        return ChangeNotice(
            title=change_title(event.title, kind),
            body=format_change_message(
                event, occurrence, tz, attendees=attendee_names(db, event.id)
            ),
            kind=kind,
            event_id=event.id,
            occurrence_date=occurrence.occurrence_date or occurrence.start_local_date,
            members=list(reachable),
            skipped=list(skipped),
            record=record,
        )
    except Exception as exc:  # pragma: no cover - defensive
        print(f"Event {kind} notice could not be prepared: {exc}")
        return None


def send_change_notice(db: Session, notice: ChangeNotice | None) -> dict:
    """Deliver a planned notice. A no-op on ``None``, and never raises."""
    if notice is None:
        return {"sent": [], "skipped": [], "failed": []}

    try:
        return _deliver(
            db,
            notice.members,
            body=notice.body,
            title=notice.title,
            kind=notice.kind,
            event_id=notice.event_id,
            occurrence_date=notice.occurrence_date,
            skipped=notice.skipped,
            record=notice.record,
        )
    except Exception as exc:  # pragma: no cover - defensive
        print(f"Event {notice.kind} notification failed: {exc}")
        return {"sent": [], "skipped": [], "failed": [], "error": str(exc)}


def notify_event_change(
    db: Session,
    event: Event,
    *,
    kind: str,
    occurrence_date: str | None = None,
    now: datetime | None = None,
) -> dict:
    """Tell an event's attendees it was added or changed, in one call.

    For the paths where the event survives the write and nothing has to be
    captured ahead of it. A deletion plans and sends separately.
    """
    return send_change_notice(
        db, plan_change_notice(db, event, kind=kind, occurrence_date=occurrence_date, now=now)
    )


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
