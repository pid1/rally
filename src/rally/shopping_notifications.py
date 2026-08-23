"""Push a batch of shopping list additions to whoever asked for them.

Rally already knows how to reach a phone — ``rally.notifications`` holds the
Pushover transport, the install's application token and each member's key. This
module reuses all of it rather than growing a second notifier, the same way the
preparedness digest and the task hand-off do.

What it adds is a notification with **no audience rule of its own**. An event
belongs to its attendees and a task belongs to its assignee, so both already
know whose phone to buzz. A shopping list belongs to the household, which means
a naive "notify on add" buzzes every phone in the house — the exact failure
``notifications.py`` warns about in its own docstring. So the audience here is
the people who ticked *Shopping list additions*, and nobody else:
``notification_prefs.subscribers()`` is the whole recipient rule.

Two things it deliberately does not do:

- **Send one push per item.** Somebody walking the pantry adds nine things in
  four minutes; that is one push, not nine. Every pass waits for the adding to
  stop — the settle window — before it sends anything.
- **Announce the list getting shorter.** Purchases, deletions, edits and
  reordering are not news. Only additions are.

``shopping_notify_watermark`` *is* the send-once guarantee, which is why there
is no per-item notice table: the scan only ever looks at what was created after
it, and it only moves past items that have been dealt with. An item added and
purchased inside the settle window is never announced, which is correct — the
milk was already bought.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from rally.models import Setting, ShoppingItem, ShoppingStore
from rally.notification_prefs import SHOPPING_ADDED, subscribers, switch_enabled
from rally.utils.timezone import ensure_utc, now_utc

# Settings keys. ``shopping_notify_watermark`` is internal bookkeeping and is
# never surfaced in the UI, exactly like ``shopping_last_purge_date`` and
# ``prep_last_digest_date``.
ENABLED_KEY = "shopping_notify_enabled"  # absent means off; also in the KINDS catalogue
SETTLE_KEY = "shopping_notify_settle_minutes"
WATERMARK_KEY = "shopping_notify_watermark"
LAST_CHECK_KEY = "shopping_notify_last_check_at"

# How long the adding has to stop for before a batch is considered finished.
DEFAULT_SETTLE_MINUTES = 5

# Pushover's documented message ceiling, the same cap the refresh digest keeps.
MAX_MESSAGE_CHARS = 1024

# How many items are named before the message stops listing and starts
# counting. Three names plus "and 6 more" is a lock screen's worth.
NAMED_LIMIT = 3

TITLE = "Shopping list"


@dataclass
class ShoppingNotifyResult:
    """What one pass did, and why it did nothing when it did nothing."""

    items: list[ShoppingItem] = field(default_factory=list)
    sent_to: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    message: str | None = None
    watermark: datetime | None = None  # the value the pass advanced to, if any
    skipped_reason: str | None = None

    @property
    def count(self) -> int:
        return len(self.items)

    @property
    def sent(self) -> bool:
        return bool(self.sent_to)


def notifications_enabled(db: Session) -> bool:
    """Whether shopping additions are announced at all. **Default off.**

    The one kind that is opt-in at both levels: nothing about this feature is
    on after an upgrade until somebody turns it on.
    """
    return switch_enabled(db, SHOPPING_ADDED)


def settle_minutes(db: Session) -> int:
    """How long the adding has to stop for before a batch is sent."""
    row = db.query(Setting).filter(Setting.key == SETTLE_KEY).first()
    try:
        value = int((row.value or "").strip()) if row else DEFAULT_SETTLE_MINUTES
    except ValueError:
        return DEFAULT_SETTLE_MINUTES
    return value if value >= 0 else DEFAULT_SETTLE_MINUTES


def watermark(db: Session) -> datetime | None:
    """The newest ``created_at`` any pass has already covered, or ``None``."""
    row = db.query(Setting).filter(Setting.key == WATERMARK_KEY).first()
    if not row or not (row.value or "").strip():
        return None
    try:
        return ensure_utc(datetime.fromisoformat(row.value.strip()))
    except ValueError:
        return None


def set_watermark(db: Session, moment: datetime) -> None:
    """Move the watermark. Never backwards — the guarantee runs one way."""
    moment = ensure_utc(moment)
    row = db.query(Setting).filter(Setting.key == WATERMARK_KEY).first()
    if row:
        row.value = moment.isoformat()
    else:
        db.add(Setting(key=WATERMARK_KEY, value=moment.isoformat()))
    db.commit()


def _join_names(names: list[str]) -> str:
    if len(names) == 1:
        return names[0]
    return f"{', '.join(names[:-1])} and {names[-1]}"


def _shared_store(db: Session, items: list[ShoppingItem]) -> str | None:
    """The store every item in the batch belongs to, if they all share one.

    A mixed batch says nothing rather than picking a winner, and the catch-all
    (``store_id IS NULL``) is not a store — "at Anywhere" is not a place.
    """
    store_ids = {item.store_id for item in items}
    if len(store_ids) != 1:
        return None
    store_id = store_ids.pop()
    if store_id is None:
        return None
    store = db.get(ShoppingStore, store_id)
    return store.name if store else None


def build_message(db: Session, items: list[ShoppingItem]) -> str:
    """What the push says: what was added, and where, if everything shares one.

    The body carries real content rather than a teaser. Rally is only reachable
    over Tailscale, so a link is dead whenever the tunnel is down and the
    message has to stand alone — the same reasoning the event reminder and the
    refresh digest both follow.
    """
    count = len(items)
    store = _shared_store(db, items)
    suffix = f" · at {store}" if store else ""

    def render(named: int) -> str:
        names = [item.name for item in items[:named]]
        remaining = count - named
        if not names:
            body = f"{count} items added"
        elif remaining > 0:
            body = f"{', '.join(names)} and {remaining} more added"
        else:
            body = f"{_join_names(names)} added"
        body += suffix
        if remaining > 0:
            body += " — open Rally for the full list"
        return body

    # Name as many as the cap allows, then give names up rather than let
    # Pushover reject the whole message over one very long item.
    for named in range(min(NAMED_LIMIT, count), -1, -1):
        body = render(named)
        if len(body) <= MAX_MESSAGE_CHARS:
            return body
    return body[:MAX_MESSAGE_CHARS]


def _added_since(db: Session, mark: datetime) -> list[ShoppingItem]:
    """Every item created after the watermark, purchased ones included.

    The purchased ones are here because the watermark has to advance past them
    too: an item added and bought inside the settle window is dealt with, and
    leaving it behind the mark would make every later pass reconsider it.
    """
    return (
        db.query(ShoppingItem)
        .filter(ShoppingItem.created_at > mark)
        .order_by(ShoppingItem.created_at.asc(), ShoppingItem.id.asc())
        .all()
    )


def scan_once(db: Session, now: datetime | None = None) -> ShoppingNotifyResult:
    """Announce the batch of additions that has finished settling. Never raises.

    Pure over the database plus a clock, so the whole behaviour is testable
    without a scheduler: the caller decides what "now" is.

    The watermark advances when the batch has been *dealt with* — announced, or
    emptied by purchases, or addressed to nobody because nobody opted in. It
    stays put when Rally could not deliver: no application token, or every send
    failing. That is the same discipline ``notifications._record`` follows,
    where a five-minute provider outage must not silently consume a reminder.
    """
    from rally.notifications import PushoverError, app_token, send_pushover

    now = (now or now_utc()).astimezone(UTC)
    result = ShoppingNotifyResult()

    if not notifications_enabled(db):
        result.skipped_reason = "shopping notifications are turned off"
        return result

    mark = watermark(db)
    if mark is None:
        # First pass after the switch went on. Everything already on the list
        # predates the decision to be told about it, so the mark starts at now
        # and the family hears about what they add next.
        set_watermark(db, now)
        result.watermark = now
        result.skipped_reason = "starting from now"
        return result

    candidates = _added_since(db, mark)
    if not candidates:
        result.skipped_reason = "nothing added since the last pass"
        return result

    newest = max(ensure_utc(item.created_at) for item in candidates)
    if newest > now - timedelta(minutes=settle_minutes(db)):
        # Somebody is still standing in the kitchen. Come back next minute.
        result.skipped_reason = "the batch is still settling"
        return result

    open_items = [item for item in candidates if not item.completed]
    if not open_items:
        set_watermark(db, newest)
        result.watermark = newest
        result.skipped_reason = "everything added was already purchased"
        return result

    token = app_token(db)
    if not token:
        # Not an error worth failing anything over, and not a reason to give up
        # on the batch either: the mark stays put so it goes out once Pushover
        # is configured.
        result.skipped_reason = "no Pushover application token configured"
        return result

    recipients = subscribers(db, SHOPPING_ADDED)
    if not recipients:
        # Nobody asked. That is an answer, not a failure — advance, so the
        # first person to opt in hears about what happens next rather than
        # inheriting a month of backlog.
        set_watermark(db, newest)
        result.watermark = newest
        result.skipped_reason = "nobody has asked for shopping list pushes"
        return result

    result.items = open_items
    result.message = build_message(db, open_items)

    for member in recipients:
        try:
            send_pushover(
                token,
                member.pushover_user_key.strip(),
                result.message,
                title=TITLE,
                device=(member.pushover_device or "").strip() or None,
            )
        except PushoverError as exc:
            print(f"  Pushover failed for {member.name}: {exc}")
            result.failed.append(member.name)
        else:
            result.sent_to.append(member.name)

    if not result.sent_to:
        # Nobody got it. Leave the mark so the next pass tries again.
        return result

    set_watermark(db, newest)
    result.watermark = newest
    return result


def run_once_per_minute(db: Session, now: datetime | None = None) -> ShoppingNotifyResult:
    """Opportunistic hook for the API, gated to at most one pass a minute.

    The container's minute loop lives in ``entrypoint.sh`` and only runs under
    Docker, so a ``dev``-served instance would otherwise never send one of
    these at all. Same reasoning and same mechanism as
    ``run_due_reminders_once_per_minute``: a settings row as the gate.

    It hangs off the *write* path (``POST /api/shopping/items``) rather than a
    read. Pushing from a ``GET`` is the mistake ``todo_notifications``
    explicitly avoids for recurring instances, and adding an item is the exact
    moment there is something to say.
    """
    now = (now or now_utc()).astimezone(UTC)
    stamp = now.strftime("%Y-%m-%dT%H:%M")

    row = db.query(Setting).filter(Setting.key == LAST_CHECK_KEY).first()
    if row and row.value == stamp:
        return ShoppingNotifyResult(skipped_reason="already checked this minute")

    if row:
        row.value = stamp
    else:
        db.add(Setting(key=LAST_CHECK_KEY, value=stamp))
    db.commit()

    try:
        return scan_once(db, now)
    except Exception as exc:  # pragma: no cover - defensive
        print(f"Shopping list notification check failed: {exc}")
        return ShoppingNotifyResult(skipped_reason=str(exc))
