"""Assemble the two blocks the daily summary consumes.

**Tonight** lists every event today, notable or not — it is the direct
replacement for the manual check someone does most days, and it must never
filter. **Coming up** lists only notable events on days 2–14, each announced
once, so a season opener does not appear in all fourteen morning summaries
leading up to it.

An event never appears in both blocks on the same morning. One already
announced in "Coming up" still appears in "Tonight" when its day arrives:
"Tonight" is the utility, "Coming up" is the heads-up.
"""

from __future__ import annotations

from datetime import date, timedelta
from zoneinfo import ZoneInfo

from rally.sports import (
    NOTICE_RETENTION_DAYS,
    WINDOW_DAYS,
    SportsEvent,
    collect_events,
)
from rally.sports.notability import evaluate, first_meetings, season_opener_reasons

EMPTY_SECTION = "Nothing scheduled tonight, and nothing notable in the next two weeks."


def _lines_for(event: SportsEvent, reason: str | None = None) -> list[str]:
    """One event, as the summary sees it.

    Television and radio are separate lines end to end and are never
    concatenated: broadcast lists mix radio callsigns in with TV channels, and
    a naive join renders a Rangers game as "Peacock, ERADM".
    """
    head = f"- {event.local_time} — {event.name}"
    if reason:
        head += f" ({reason})"
    return [head, f"    TV: {event.tv_label}", f"    Radio: {event.radio_label}"]


def build_sections(
    followed: list,
    tz: ZoneInfo,
    today: date,
    announced_keys: set[str],
) -> tuple[str, list[tuple[SportsEvent, str]]]:
    """Return the rendered section text and the notices that should be recorded.

    The caller records the notices only after a summary is actually produced, so
    a failed generation does not silently consume a "Coming up" announcement.
    """
    schedules, standings = collect_events(followed, tz, today)

    events: list[SportsEvent] = []
    # Openers and first meetings are answered against the *season*, never the
    # window: the first game in a two-week window is not the season opener.
    openers: dict[str, str] = {}
    meetings: set[str] = set()
    for schedule in schedules:
        events.extend(schedule.window)
        if schedule.season:
            openers.update(season_opener_reasons(schedule.season))
            meetings |= first_meetings(
                schedule.season, standings.get(schedule.season[0].league)
            )

    if not events:
        return EMPTY_SECTION, []
    events.sort(key=lambda e: (e.start_utc, e.team_label))

    horizon = today + timedelta(days=WINDOW_DAYS)
    tonight = [e for e in events if e.local_date == today]
    upcoming = [e for e in events if today < e.local_date <= horizon]

    notable: list[tuple[SportsEvent, str]] = []
    for event in upcoming:
        if event.event_key in announced_keys:
            continue
        is_notable, reason = evaluate(
            event, standings.get(event.league), openers, meetings
        )
        if is_notable:
            notable.append((event, reason))

    blocks: list[str] = []

    blocks.append("Tonight:")
    if tonight:
        for event in tonight:
            blocks.extend(_lines_for(event))
    else:
        blocks.append("- Nothing on tonight.")

    blocks.append("")
    blocks.append(f"Coming up (next {WINDOW_DAYS} days, notable events only):")
    if notable:
        for event, reason in notable:
            blocks.append(f"  {event.local_date:%a %-m/%-d}")
            blocks.extend(f"  {line}" for line in _lines_for(event, reason))
    else:
        blocks.append("- Nothing notable coming up.")

    return "\n".join(blocks), notable


def load_followed_teams(db) -> list:
    """Active followed teams, ordered so the section reads consistently."""
    from rally.models import FollowedTeam

    return (
        db.query(FollowedTeam)
        .filter(FollowedTeam.active == True)  # noqa: E712
        .order_by(FollowedTeam.label.asc())
        .all()
    )


def load_announced_keys(db) -> set[str]:
    """Event keys already announced in a previous "Coming up" block."""
    from rally.models import SportsEventNotice

    return {row.event_key for row in db.query(SportsEventNotice.event_key).all()}


def record_notices(db, notices: list[tuple[SportsEvent, str]], today: date) -> None:
    """Persist one notice per announced event, and purge stale ones.

    Written once and never rewritten: an event that becomes notable for a better
    reason later is not re-announced.
    """
    from rally.models import SportsEventNotice

    announced_on = today.strftime("%Y-%m-%d")
    existing = {row.event_key for row in db.query(SportsEventNotice.event_key).all()}

    for event, reason in notices:
        if event.event_key in existing:
            continue
        db.add(
            SportsEventNotice(
                event_key=event.event_key,
                event_local_date=event.local_date.strftime("%Y-%m-%d"),
                announced_on=announced_on,
                notability_reason=(reason or "")[:60] or None,
            )
        )
        existing.add(event.event_key)

    cutoff = (today - timedelta(days=NOTICE_RETENTION_DAYS)).strftime("%Y-%m-%d")
    db.query(SportsEventNotice).filter(
        SportsEventNotice.event_local_date < cutoff
    ).delete(synchronize_session=False)
    db.commit()
