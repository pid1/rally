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
DEFAULT_SYNC_INTERVAL_MINUTES = 15

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


# ── Serialisation ────────────────────────────────────────────────────────────


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
    """Freshness summary for the UI."""
    rows = db.query(CalendarCache).all()
    external = external_calendar_ids(db)
    if not external:
        return {"cached": 0, "expected": 0, "oldest_fetched_at": None, "failing": []}

    labels = {cal.id: cal.label for cal in db.query(Calendar).all()}
    return {
        "cached": len(rows),
        "expected": len(external),
        "oldest_fetched_at": min(
            (ensure_utc(r.fetched_at) for r in rows if r.fetched_at), default=None
        ),
        "failing": [labels.get(r.calendar_id, str(r.calendar_id)) for r in rows if r.last_error],
    }


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
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "label": label}


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
        return {"synced": 0, "unchanged": 0, "failed": 0, "calendars": 0}

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

    synced = unchanged = failed = 0
    now = now_utc()

    for cal, _owner in targets:
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
            failed += 1
            continue

        if outcome.get("unchanged"):
            row.fetched_at = now
            if outcome.get("sync_tokens") is not None:
                row.sync_tokens = outcome["sync_tokens"]
            row.last_error = None
            row.failure_count = 0
            unchanged += 1
            continue

        digest = outcome.get("body_hash")
        if digest and row.content_hash == digest and row.occurrences:
            # Body identical to last time: skip the expansion entirely.
            row.fetched_at = now
            row.last_error = None
            row.failure_count = 0
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
        synced += 1

    db.commit()
    return {
        "synced": synced,
        "unchanged": unchanged,
        "failed": failed,
        "calendars": len(targets),
    }


def sync_if_stale(db: Session, local_tz: ZoneInfo) -> dict | None:
    """Sync when the oldest cache entry is older than the configured interval.

    Called from the minute loop and opportunistically from the API, the same
    arrangement event reminders use. Returns ``None`` when nothing was due.
    """
    external = external_calendar_ids(db)
    if not external:
        return None

    rows = db.query(CalendarCache).all()
    cached_ids = {r.calendar_id for r in rows}
    missing = [cid for cid in external if cid not in cached_ids]

    if missing:
        return sync_calendars(db, local_tz)

    cutoff = now_utc() - timedelta(minutes=sync_interval_minutes(db))
    # SQLite hands back naive datetimes, so a bare comparison against an aware
    # `now_utc()` raises — and this runs from a background loop, where that
    # would mean the cache silently never refreshed.
    stale = [
        r.calendar_id for r in rows if r.fetched_at is None or ensure_utc(r.fetched_at) < cutoff
    ]
    if not stale:
        return None
    return sync_calendars(db, local_tz, calendar_ids=stale)
