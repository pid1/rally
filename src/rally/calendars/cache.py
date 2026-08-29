"""Cached external calendars, refreshed by a background sync.

The read path must never touch the network. Before this, `/calendar` called
every remote feed synchronously on every request: measured at 11.5s across
three sources, serially, with an 8.9MB ICS feed accounting for 6.3s of it. The
page therefore spent most of its life showing "Loading calendar…".

Three things fix that, in order of how much they matter:

1. **Reads come from the database.** External occurrences are expanded once, at
   sync time, and stored. A read is a JSON decode.
2. **Syncs run concurrently.** A pass costs the slowest source (~6s), not the
   sum (~11s).
3. **Syncs are incremental where the server allows it.** A conditional request
   turns an unchanged feed into a 304 with no body; failing that, a content
   hash turns an unchanged body into a skipped expansion. Neither of this
   install's feeds sends a validator, so the hash is what actually earns its
   keep here — but Apple, Fastmail and Nextcloud all send ETags, and a 304 is
   strictly better.

Native events are deliberately *not* cached. They are a local query (0.13s
measured), they are the events most likely to have just been edited, and
serving them stale would make Rally feel broken in the one place it owns the
data.
"""

from __future__ import annotations

import concurrent.futures
import hashlib
from dataclasses import fields as dataclass_fields
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from rally.calendars.occurrence import Occurrence
from rally.models import Calendar, CalendarCache, FamilyMember, Setting
from rally.utils.timezone import ensure_utc, now_utc

# How far either side of today the cached window reaches. Requests may ask for
# up to MAX_WINDOW_DAYS (366); anything outside what is cached is simply absent
# rather than fetched live, because a read that can block on the network is the
# bug this module exists to remove.
CACHE_DAYS_BACK = 30
CACHE_DAYS_FORWARD = 210

SYNC_INTERVAL_KEY = "calendar_sync_interval_minutes"
DEFAULT_SYNC_INTERVAL_MINUTES = 5

# Statuses that mean "you are asking too often", as opposed to "this is
# broken". 429 is the explicit one; 503 is what several calendar hosts send
# instead, and treating it as an ordinary failure would have us retry it at
# full rate. Both get the backoff; everything else keeps the old behaviour of
# retrying next pass, because a 500 or a timeout is usually transient and
# costs the server nothing to re-ask.
RATE_LIMIT_STATUSES = (429, 503)

# Backoff doubles per consecutive rate-limited pass: 5, 10, 20, 40 ... capped.
# The cap is deliberately well under a day — a feed that has calmed down should
# come back on its own without anyone pressing Refresh.
BACKOFF_BASE_MINUTES = 5
BACKOFF_MAX_MINUTES = 240

# Concurrency for a sync pass. Small on purpose: these are a handful of feeds,
# and a wide pool would trade a real problem for a rate-limit one.
MAX_WORKERS = 4

FETCH_TIMEOUT_SECONDS = 30

_DATETIME_FIELDS = {"start", "end"}

# Properties that change on every fetch without the calendar having changed.
# Google stamps a fresh DTSTAMP on each response, so hashing the raw body
# reports "changed" every single time and the incremental path never fires.
_VOLATILE_ICS_PROPERTIES = ("DTSTAMP:", "DTSTAMP;")


def content_fingerprint(text: str) -> str:
    """A hash that changes only when the calendar's *content* does.

    Two things defeat a naive hash of the body, both observed against this
    install's Google feeds:

    - **DTSTAMP is rewritten on every response.** It records when the feed was
      generated, not when the event changed.
    - **VEVENTs come back in a different order each time.** The line count is
      identical and the events are the same; only the ordering moved.

    So: unfold the continuation lines first (otherwise sorting would tear a
    folded value away from its property), drop the volatile properties, sort,
    and hash. A real edit still changes some line, so this cannot produce a
    false "unchanged" — it only stops the false "changed" that made the
    incremental path dead code.
    """
    unfolded: list[str] = []
    for raw_line in text.splitlines():
        if raw_line[:1] in (" ", "\t") and unfolded:
            unfolded[-1] += raw_line[1:]
        else:
            unfolded.append(raw_line)

    kept = [line for line in unfolded if not line.startswith(_VOLATILE_ICS_PROPERTIES)]
    kept.sort()
    digest = hashlib.sha256()
    for line in kept:
        digest.update(line.encode("utf-8", "replace"))
        digest.update(b"\n")
    return digest.hexdigest()


# ── Rate limiting ────────────────────────────────────────────────────────────


def parse_retry_after(value: str | None) -> timedelta | None:
    """Interpret a ``Retry-After`` header, in either form RFC 9110 allows.

    It is either a count of seconds or an HTTP-date, and which one you get is
    per-server: Google sends seconds, some CalDAV hosts send a date. A date
    already in the past means "you may retry now", so it clamps to zero rather
    than going negative and reading as no backoff at all.
    """
    if not value:
        return None
    text = value.strip()
    if text.isdigit():
        return timedelta(seconds=int(text))
    try:
        from email.utils import parsedate_to_datetime

        when = parsedate_to_datetime(text)
    except TypeError, ValueError:
        return None
    if when is None:
        return None
    return max(ensure_utc(when) - now_utc(), timedelta(0))


def backoff_delay(failure_count: int) -> timedelta:
    """How long to wait after ``failure_count`` consecutive rate-limited passes.

    ``failure_count`` is 1 on the first refusal, so the first wait is the base
    interval rather than zero.
    """
    exponent = max(0, failure_count - 1)
    # Cap the exponent before shifting: a feed left failing for a month would
    # otherwise compute an astronomically large number just to min() it away.
    exponent = min(exponent, 16)
    minutes = min(BACKOFF_BASE_MINUTES * (2**exponent), BACKOFF_MAX_MINUTES)
    return timedelta(minutes=minutes)


# ── Serialization ────────────────────────────────────────────────────────────


def occurrence_to_dict(occ: Occurrence) -> dict:
    out = {}
    for f in dataclass_fields(Occurrence):
        value = getattr(occ, f.name)
        if f.name in _DATETIME_FIELDS:
            value = value.isoformat()
        elif isinstance(value, tuple):
            value = list(value)
        out[f.name] = value
    return out


def occurrence_from_dict(raw: dict) -> Occurrence:
    kwargs = {}
    for f in dataclass_fields(Occurrence):
        if f.name not in raw:
            continue
        value = raw[f.name]
        if f.name in _DATETIME_FIELDS and isinstance(value, str):
            value = datetime.fromisoformat(value)
        elif f.name == "attendees" and isinstance(value, list):
            value = tuple(value)
        kwargs[f.name] = value
    return Occurrence(**kwargs)


# ── Settings ─────────────────────────────────────────────────────────────────


def sync_interval_minutes(db: Session) -> int:
    row = db.query(Setting).filter(Setting.key == SYNC_INTERVAL_KEY).first()
    try:
        value = int((row.value or "").strip()) if row else DEFAULT_SYNC_INTERVAL_MINUTES
    except TypeError, ValueError:
        return DEFAULT_SYNC_INTERVAL_MINUTES
    return max(1, value)


def cache_window(local_tz: ZoneInfo) -> tuple[date, date]:
    today = now_utc().astimezone(local_tz).date()
    return today - timedelta(days=CACHE_DAYS_BACK), today + timedelta(days=CACHE_DAYS_FORWARD)


# ── Reading ──────────────────────────────────────────────────────────────────


def external_calendar_ids(db: Session) -> list[int]:
    return [cal.id for cal in db.query(Calendar).all() if (cal.cal_type or "ics") != "native"]


def read_cached(
    db: Session, *, window_start: datetime, window_end: datetime
) -> tuple[list[Occurrence], list[str], list[int]]:
    """Cached occurrences overlapping the window.

    Returns ``(occurrences, failing_labels, uncached_calendar_ids)``. A feed
    that failed its last sync still serves its previous occurrences — a stale
    calendar is far better than an empty one — and names itself so the page can
    say so.
    """
    rows = {c.calendar_id: c for c in db.query(CalendarCache).all()}
    labels = {cal.id: cal for cal in db.query(Calendar).all()}

    occurrences: list[Occurrence] = []
    failures: list[str] = []
    uncached: list[int] = []

    for cal_id in external_calendar_ids(db):
        row = rows.get(cal_id)
        if row is None:
            uncached.append(cal_id)
            continue
        if row.last_error:
            cal = labels.get(cal_id)
            failures.append(cal.label if cal else f"calendar {cal_id}")
        for raw in row.occurrences or []:
            occ = occurrence_from_dict(raw)
            # end is exclusive, so an occurrence touching the window edge counts.
            if occ.end > window_start and occ.start < window_end:
                occurrences.append(occ)

    return occurrences, failures, uncached


def cache_status(db: Session) -> dict:
    """Freshness summary for the UI.

    Every field is computed over the calendars that still exist. A row left
    behind by a deleted calendar is nobody's job to refresh, so counting one
    here reported the age of the deletion rather than the age of the data —
    which is how a cache refreshed two minutes ago came to describe itself as
    272 hours old.
    """
    external = set(external_calendar_ids(db))
    if not external:
        return {"cached": 0, "expected": 0, "oldest_fetched_at": None, "failing": []}

    rows = [r for r in db.query(CalendarCache).all() if r.calendar_id in external]
    labels = {cal.id: cal.label for cal in db.query(Calendar).all()}
    return {
        "cached": len(rows),
        "expected": len(external),
        "oldest_fetched_at": min(
            (ensure_utc(r.fetched_at) for r in rows if r.fetched_at), default=None
        ),
        "failing": [labels.get(r.calendar_id, str(r.calendar_id)) for r in rows if r.last_error],
    }


def prune_orphaned_cache(db: Session) -> int:
    """Delete cache rows whose calendar no longer exists. Returns how many.

    ``calendar_cache.calendar_id`` is a plain integer rather than a foreign
    key, so nothing at the database level cleans these up; the delete endpoint
    now does it directly, and this is the sweep that catches rows already
    orphaned before it did.
    """
    live = {cal.id for cal in db.query(Calendar).all()}
    orphans = [r for r in db.query(CalendarCache).all() if r.calendar_id not in live]
    for row in orphans:
        db.delete(row)
    if orphans:
        db.flush()
    return len(orphans)


# ── Syncing ──────────────────────────────────────────────────────────────────


def _fetch_one(
    calendar: Calendar,
    owner: FamilyMember | None,
    *,
    window_start: datetime,
    window_end: datetime,
    local_tz: ZoneInfo,
    etag: str | None,
    last_modified: str | None,
    sync_tokens: dict | None = None,
    has_cache: bool = False,
) -> dict:
    """Fetch one calendar. Returns a result dict; never raises.

    ``unchanged`` means the server said 304 or the body hashed identically, and
    the caller should keep the stored occurrences and only move ``fetched_at``.
    """
    cal_type = calendar.cal_type or "ics"
    member = owner.name if owner else None
    member_color = owner.color if owner else None
    label = f"{calendar.label} ({member})" if member else calendar.label

    try:
        if cal_type in ("caldav_google", "caldav_apple"):
            from rally.caldav_client import sync_probe

            # Ask what changed before downloading anything. iCloud answers in
            # ~0.1s where a full fetch and expansion costs ~2.1s. A server
            # without sync-collection returns None and we fetch as before.
            tokens = None
            if has_cache:
                try:
                    tokens, unchanged = sync_probe(calendar, sync_tokens)
                    if tokens is not None and unchanged:
                        return {"ok": True, "unchanged": True, "sync_tokens": tokens}
                except Exception as exc:
                    return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "label": label}

            if cal_type == "caldav_google":
                from rally.caldav_client import fetch_google_caldav as fetch
            else:
                from rally.caldav_client import fetch_apple_caldav as fetch
            occurrences = fetch(
                calendar,
                local_tz,
                window_start=window_start,
                window_end=window_end,
                label=label,
                member=member,
                member_color=member_color,
            )
            if tokens is None:
                # Capture a baseline so the next pass has something to compare
                # against, but never let this fail a successful fetch.
                try:
                    tokens, _ = sync_probe(calendar, None)
                except Exception:
                    tokens = None
            return {
                "ok": True,
                "unchanged": False,
                "occurrences": occurrences,
                "sync_tokens": tokens,
            }

        import requests

        from rally.calendars.ics import occurrences_from_ical_text

        headers = {}
        if etag:
            headers["If-None-Match"] = etag
        if last_modified:
            headers["If-Modified-Since"] = last_modified

        response = requests.get(calendar.url, timeout=FETCH_TIMEOUT_SECONDS, headers=headers)
        if response.status_code == 304:
            # The cheapest possible sync: no body, no parse.
            return {"ok": True, "unchanged": True, "etag": etag, "last_modified": last_modified}
        if response.status_code in RATE_LIMIT_STATUSES:
            # Read the status before raise_for_status: the exception carries no
            # response we can reach here, and the server's own Retry-After is
            # always a better number than one we invented.
            return {
                "ok": False,
                "rate_limited": True,
                "retry_after": parse_retry_after(response.headers.get("Retry-After")),
                "error": f"rate limited (HTTP {response.status_code})",
                "label": label,
            }
        response.raise_for_status()

        body = response.text
        digest = content_fingerprint(body)
        return {
            "ok": True,
            "unchanged": False,
            "body_hash": digest,
            "etag": response.headers.get("ETag"),
            "last_modified": response.headers.get("Last-Modified"),
            "expand": lambda: occurrences_from_ical_text(
                body,
                window_start=window_start,
                window_end=window_end,
                local_tz=local_tz,
                owner_email=calendar.owner_email,
                source="ics",
                calendar_id=calendar.id,
                calendar_label=label,
                member=member,
                member_color=member_color,
            ),
        }
    except Exception as exc:
        # CalDAV goes through its own client, and a raised HTTPError from any
        # path still carries the response. Catching the rate limit here too
        # means a throttled CalDAV feed backs off like an ICS one instead of
        # being retried at full rate as a generic failure.
        response = getattr(exc, "response", None)
        status = getattr(response, "status_code", None)
        if status in RATE_LIMIT_STATUSES:
            return {
                "ok": False,
                "rate_limited": True,
                "retry_after": parse_retry_after(
                    getattr(response, "headers", {}).get("Retry-After")
                ),
                "error": f"rate limited (HTTP {status})",
                "label": label,
            }
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "label": label}


def _restamp_member_color(row: CalendarCache, owner: FamilyMember | None) -> bool:
    """Rewrite the owner's current color onto already-stored occurrences.

    The unchanged paths skip expansion, which is the whole point of them — but
    the stored dicts carry whatever color the owner had when they were last
    expanded. A member who picks a new color would otherwise keep the old dot
    until the feed itself happened to change, which for a quiet calendar is
    never: the guard those paths test is the *feed's* content, and nothing
    about it moves when the change was on this side.

    Returns whether anything moved, so the overwhelmingly common case — nobody
    changed a color — stays a no-op rather than rewriting the whole blob on
    every sync pass.
    """
    color = owner.color if owner else None
    stored = row.occurrences or []
    if all(occurrence.get("member_color") == color for occurrence in stored):
        return False
    # `occurrences` is a plain JSON column with no mutation tracking, so
    # patching the dicts in place would never reach the database. The list has
    # to be reassigned.
    row.occurrences = [{**occurrence, "member_color": color} for occurrence in stored]
    return True


def sync_calendars(
    db: Session, local_tz: ZoneInfo, *, calendar_ids: list[int] | None = None
) -> dict:
    """Refresh cached occurrences for external calendars, concurrently.

    Returns a summary dict. Never raises: a sync pass runs from a background
    loop, and a loop that dies on one bad feed is worse than a stale calendar.
    """
    start_day, end_day = cache_window(local_tz)
    from rally.calendars.sources import window_bounds

    window_start, window_end = window_bounds(start_day, end_day, local_tz)

    # Before anything else, drop cache rows whose calendar is gone. Deleting a
    # calendar used to leave its row behind, and a row nothing syncs is frozen
    # at the moment the calendar was deleted — which is what made the whole
    # cache report itself as stale forever.
    pruned = prune_orphaned_cache(db)

    pairs = (
        db.query(Calendar, FamilyMember)
        .outerjoin(FamilyMember, Calendar.family_member_id == FamilyMember.id)
        .all()
    )
    targets = [
        (cal, owner)
        for cal, owner in pairs
        if (cal.cal_type or "ics") != "native" and (calendar_ids is None or cal.id in calendar_ids)
    ]
    if not targets:
        # The prune only flushed. Nothing below will reach the commit on this
        # path, and an uncommitted delete is no delete at all.
        if pruned:
            db.commit()
        return {"synced": 0, "unchanged": 0, "failed": 0, "rate_limited": 0, "calendars": 0}

    rows = {c.calendar_id: c for c in db.query(CalendarCache).all()}

    # The network is the slow part and it parallelises cleanly. Expansion
    # happens back on this thread, where the DB session lives.
    results: dict[int, dict] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {
            pool.submit(
                _fetch_one,
                cal,
                owner,
                window_start=window_start,
                window_end=window_end,
                local_tz=local_tz,
                etag=(rows.get(cal.id).etag if rows.get(cal.id) else None),
                last_modified=(rows.get(cal.id).last_modified if rows.get(cal.id) else None),
                sync_tokens=(rows.get(cal.id).sync_tokens if rows.get(cal.id) else None),
                has_cache=bool(rows.get(cal.id) and rows.get(cal.id).occurrences),
            ): cal.id
            for cal, owner in targets
        }
        for future in concurrent.futures.as_completed(futures):
            results[futures[future]] = future.result()

    synced = unchanged = failed = rate_limited = 0
    now = now_utc()

    for cal, owner in targets:
        outcome = results.get(cal.id) or {"ok": False, "error": "no result"}
        row = rows.get(cal.id)
        if row is None:
            row = CalendarCache(
                calendar_id=cal.id,
                occurrences=[],
                window_start=start_day.isoformat(),
                window_end=end_day.isoformat(),
            )
            db.add(row)
            rows[cal.id] = row

        if not outcome.get("ok"):
            # Keep the previous occurrences. A feed being down must shorten
            # nothing and blank nothing; it only adds a note.
            row.last_error = outcome.get("error", "unknown error")
            row.failure_count = (row.failure_count or 0) + 1
            if outcome.get("rate_limited"):
                # Honor the server's own number when it gave one, and fall back
                # to doubling from our failure count when it did not.
                delay = outcome.get("retry_after") or backoff_delay(row.failure_count)
                row.retry_after = now + delay
                rate_limited += 1
            failed += 1
            continue

        if outcome.get("unchanged"):
            _restamp_member_color(row, owner)
            row.fetched_at = now
            if outcome.get("sync_tokens") is not None:
                row.sync_tokens = outcome["sync_tokens"]
            row.last_error = None
            row.failure_count = 0
            row.retry_after = None
            unchanged += 1
            continue

        digest = outcome.get("body_hash")
        if digest and row.content_hash == digest and row.occurrences:
            # Body identical to last time: skip the expansion entirely. The
            # owner's color is still re-resolved — it is ours, not the feed's,
            # and this is the one path that would otherwise never revisit it.
            _restamp_member_color(row, owner)
            row.fetched_at = now
            row.last_error = None
            row.failure_count = 0
            row.retry_after = None
            unchanged += 1
            continue

        expand = outcome.get("expand")
        try:
            occurrences = expand() if expand else outcome.get("occurrences", [])
        except Exception as exc:
            row.last_error = f"expand failed: {type(exc).__name__}: {exc}"
            row.failure_count = (row.failure_count or 0) + 1
            failed += 1
            continue

        row.occurrences = [occurrence_to_dict(o) for o in occurrences]
        row.window_start = start_day.isoformat()
        row.window_end = end_day.isoformat()
        row.content_hash = digest
        row.etag = outcome.get("etag")
        row.last_modified = outcome.get("last_modified")
        if outcome.get("sync_tokens") is not None:
            row.sync_tokens = outcome["sync_tokens"]
        row.fetched_at = now
        row.changed_at = now
        row.last_error = None
        row.failure_count = 0
        row.retry_after = None
        synced += 1

    db.commit()
    return {
        "synced": synced,
        "unchanged": unchanged,
        "failed": failed,
        "rate_limited": rate_limited,
        "calendars": len(targets),
    }


def sync_if_stale(db: Session, local_tz: ZoneInfo) -> dict | None:
    """Sync the calendars whose cache has aged past the configured interval.

    Called from the minute loop and opportunistically from the API, the same
    arrangement event reminders use. Returns ``None`` when nothing was due.

    Two things narrow what counts as due. Only rows belonging to a calendar
    that still exists are considered: an orphan is refreshed by nothing, so it
    is stale forever, and this used to report work on every pass — harmlessly,
    since ``sync_calendars`` then found no target and touched no network, but
    it meant the return value said "synced" when nothing had been. And a
    calendar inside its rate-limit backoff is held back until ``retry_after``
    passes, which is the whole point of having recorded it.
    """
    external = external_calendar_ids(db)
    if not external:
        return None

    now = now_utc()
    live = set(external)
    rows = [r for r in db.query(CalendarCache).all() if r.calendar_id in live]
    cached_ids = {r.calendar_id for r in rows}
    missing = [cid for cid in external if cid not in cached_ids]

    # SQLite hands back naive datetimes, so a bare comparison against an aware
    # `now_utc()` raises — and this runs from a background loop, where that
    # would mean the cache silently never refreshed.
    cutoff = now - timedelta(minutes=sync_interval_minutes(db))

    def is_due(row: CalendarCache) -> bool:
        if row.retry_after is not None:
            # While a backoff is set it *replaces* the interval for this
            # calendar. Not just to hold it back: `fetched_at` records the last
            # successful contact and does not move while a feed is refusing us,
            # so once the backoff expired the interval test would still say
            # "recently fetched" and the calendar would never be retried at all.
            return ensure_utc(row.retry_after) <= now
        return row.fetched_at is None or ensure_utc(row.fetched_at) < cutoff

    stale = [r.calendar_id for r in rows if is_due(r)]

    # A calendar with no row at all has never been fetched, so it is due by
    # definition — but it joins the same list rather than triggering a full
    # sweep, which used to drag every backed-off feed along with it.
    due_ids = sorted(set(stale) | set(missing))
    if not due_ids:
        return None
    return sync_calendars(db, local_tz, calendar_ids=due_ids)
