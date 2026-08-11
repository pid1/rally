"""ESPN adapter: schedules and standings for football, hockey and racing.

ESPN's site API is undocumented, and several of its behaviours produce a
silent, plausible-looking wrong answer rather than an error. Each guard below
exists because of one of them — see the comments, and issue #135.
"""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from rally.sports import (
    NASCAR_RADIO,
    SportsEvent,
    Standings,
    TeamRecord,
    TeamSchedule,
    fetch_json,
    local_date_of,
    local_time_of,
)

SITE_BASE = "https://site.api.espn.com/apis/site/v2/sports"
STANDINGS_BASE = "https://site.api.espn.com/apis/v2/sports"

# Preseason, regular season, postseason. Requested explicitly and merged: a
# bare `teams/{key}/schedule` returns ONLY the season type the calendar happens
# to be in, so in August it returns three preseason games and no regular season
# at all — silently missing the season opener this feature exists to flag. The
# postseason lives behind seasontype=3 and is absent from every other response,
# so a playoff run would otherwise render an empty section.
SEASON_TYPES = (1, 2, 3)


def _parse_utc(value: str | None) -> datetime | None:
    """Parse ESPN's ``2026-08-11T01:38Z`` timestamps."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _broadcast_entries(competition: dict) -> list[dict]:
    """Normalize the two broadcast shapes ESPN uses.

    Team-schedule responses carry typed entries at ``broadcasts[]``. Racing
    scoreboards carry an untyped ``{market, names[]}`` list there and put the
    typed equivalents under ``geoBroadcasts[]``. Reading the wrong path yields
    no channel rather than an error — a game that renders as "channel TBD"
    forever.
    """
    typed = competition.get("geoBroadcasts")
    if isinstance(typed, list) and typed:
        return typed
    entries = competition.get("broadcasts")
    return entries if isinstance(entries, list) else []


def _split_broadcasts(competition: dict) -> tuple[list[str], list[str], bool]:
    """Split broadcasts into television and radio, keeping only national feeds.

    Radio callsigns read exactly like TV channels, so the two are split and
    never concatenated — a naive join renders a Rangers game as "Peacock,
    ERADM", and ERADM is a radio station.

    ⚠️ **Regional entries are dropped, because ESPN's ``market`` field does not
    identify whose feed it is.** Measured across a full recorded Stars season:
    all 58 regional TV entries are tagged ``Home`` whether Dallas is home or
    away, and not one of them is the Stars' own network — they are MSG2, KONG,
    NBC Sports BA, MNMT, Sportsnet, i.e. the *opponent's* channel every time.
    Keeping ``market == ours`` would therefore surface exactly the network the
    family cannot receive while hiding the one they can.

    Rendering "channel TBD" for those games is the honest answer: ESPN did not
    tell us a channel we can attribute. Baseball, where the family's own
    regional feed matters most, uses MLB statsapi instead — its ``homeAway``
    tag is correct, and ``mlb._split_broadcasts`` relies on it.
    """
    tv: list[str] = []
    radio: list[str] = []
    national_tv = False

    for entry in _broadcast_entries(competition):
        if not isinstance(entry, dict):
            continue
        kind = ((entry.get("type") or {}).get("shortName") or "").strip()
        market = ((entry.get("market") or {}).get("type") or "").strip()
        name = ((entry.get("media") or {}).get("shortName") or "").strip()

        # The untyped racing shape: a market string and a list of names, with no
        # type at all. Everything there is television, and racing is national.
        if not kind and isinstance(entry.get("names"), list):
            for raw in entry["names"]:
                if raw and raw not in tv:
                    tv.append(str(raw))
            national_tv = national_tv or str(entry.get("market", "")).lower() == "national"
            continue

        if not name or market != "National":
            continue

        if kind == "TV":
            if name not in tv:
                tv.append(name)
            national_tv = True
        elif kind == "Radio" and name not in radio:
            radio.append(name)

    return tv, radio, national_tv


def _team_event(team, competition: dict, event: dict, tz: ZoneInfo) -> SportsEvent | None:
    """Build a normalized event from a team-schedule competition."""
    start = _parse_utc(event.get("date"))
    if start is None:
        return None

    ours = other = None
    for competitor in competition.get("competitors", []):
        side = (competitor.get("team") or {}).get("abbreviation", "")
        if side and team.team_key and side.lower() == team.team_key.lower():
            ours = competitor
        else:
            other = competitor
    is_home = (ours.get("homeAway") == "home") if ours else None

    tv, radio, national_tv = _split_broadcasts(competition)
    # Radio affiliation is a season-long constant for these sports and no feed
    # carries it, so the configured string is both simpler and more accurate.
    if not radio and team.radio_station:
        radio = [team.radio_station]

    notes = competition.get("notes") or []
    headline = notes[0].get("headline") if notes and isinstance(notes[0], dict) else None

    opponent_team = (other or {}).get("team") or {}
    return SportsEvent(
        event_key=f"espn:{event.get('id')}",
        league=team.league,
        team_label=team.label,
        name=event.get("name") or "",
        start_utc=start,
        local_date=local_date_of(start, tz),
        local_time=local_time_of(start, tz),
        local_hour=start.astimezone(tz).hour,
        season_type=int((event.get("seasonType") or {}).get("type") or 2),
        team_id=str((ours or {}).get("id")) if ours else None,
        opponent_id=str(opponent_team.get("id")) if opponent_team.get("id") else None,
        opponent_name=opponent_team.get("displayName"),
        is_home=is_home,
        tv=tuple(tv),
        radio=tuple(radio),
        national_tv=national_tv,
        note=headline,
    )


def _race_event(team, event: dict, tz: ZoneInfo) -> SportsEvent | None:
    """Build a normalized event from a racing scoreboard entry."""
    start = _parse_utc(event.get("date"))
    if start is None:
        return None

    competitions = event.get("competitions") or [{}]
    tv, _radio, national_tv = _split_broadcasts(competitions[0])

    return SportsEvent(
        event_key=f"espn:{event.get('id')}",
        league=team.league,
        team_label=team.label,
        name=event.get("name") or team.label,
        start_utc=start,
        local_date=local_date_of(start, tz),
        local_time=local_time_of(start, tz),
        local_hour=start.astimezone(tz).hour,
        season_type=int((event.get("season") or {}).get("type") or 2),
        tv=tuple(tv),
        radio=(team.radio_station or NASCAR_RADIO,),
        national_tv=national_tv,
        is_race=True,
    )


def fetch_schedule(team, tz: ZoneInfo, start: date, end: date) -> TeamSchedule:
    """Fetch one followed team's or series' window, plus its full season."""
    if team.league.startswith("racing/"):
        return _fetch_racing(team, tz, start, end)
    return _fetch_team(team, tz, start, end)


def _fetch_racing(team, tz: ZoneInfo, start: date, end: date) -> TeamSchedule:
    """Racing scoreboards are the one ESPN endpoint that honors a date range.

    No season list: every points race is notable regardless, so racing needs no
    opener or first-meeting detection and the extra call would buy nothing.
    """
    payload = fetch_json(
        f"{SITE_BASE}/{team.league}/scoreboard",
        {"dates": f"{start:%Y%m%d}-{end:%Y%m%d}"},
    )
    if not payload:
        return TeamSchedule()

    events = []
    for raw in payload.get("events") or []:
        event = _race_event(team, raw, tz)
        if event and start <= event.local_date <= end:
            events.append(event)
    return TeamSchedule(window=events, season=[])


def _fetch_team(team, tz: ZoneInfo, start: date, end: date) -> TeamSchedule:
    """Fetch all three season types and filter to the window ourselves.

    ``?dates=20260810-20260824`` on a team schedule is **silently ignored** —
    it returns the full season, identical to the call without it. Code that
    trusts the parameter appears to work while quietly processing 162 events.

    That misfeature is what makes the full season free here: we already hold
    every event, so opener and first-meeting detection costs no extra call.
    """
    events: list[SportsEvent] = []
    seen: set[str] = set()

    for season_type in SEASON_TYPES:
        payload = fetch_json(
            f"{SITE_BASE}/{team.league}/teams/{team.team_key}/schedule",
            {"seasontype": season_type},
        )
        if not payload:
            continue
        for raw in payload.get("events") or []:
            competitions = raw.get("competitions") or []
            if not competitions:
                continue
            event = _team_event(team, competitions[0], raw, tz)
            if event is None or event.event_key in seen:
                continue
            seen.add(event.event_key)
            events.append(event)

    events.sort(key=lambda e: e.start_utc)
    return TeamSchedule(
        window=[e for e in events if start <= e.local_date <= end],
        season=events,
    )


def _stat(entry: dict, name: str):
    for stat in entry.get("stats") or []:
        if stat.get("name") == name:
            return stat
    return None


def _stat_value(entry: dict, name: str, default: float = 0.0) -> float:
    stat = _stat(entry, name)
    if stat is None or stat.get("value") is None:
        return default
    try:
        return float(stat["value"])
    except TypeError, ValueError:
        return default


def _stat_display(entry: dict, name: str) -> str:
    stat = _stat(entry, name)
    return str(stat.get("displayValue") or "") if stat else ""


def fetch_standings(league: str) -> Standings | None:
    """Current records for a league, grouped by division.

    ``level=3`` is what makes division rivalry free: without it ESPN returns
    conference-level groupings only, and the schedule payload carries no team
    grouping at all, so this is the only cheap source for "is this a division
    rival".
    """
    payload = fetch_json(f"{STANDINGS_BASE}/{league}/standings", {"level": 3})
    if not payload:
        return None

    teams: dict[str, TeamRecord] = {}

    def walk(node: dict, division_id: str | None) -> None:
        group_id = node.get("id") or division_id
        standings = node.get("standings") or {}
        entries = standings.get("entries") or []
        # Entries arrive in standings order within their group, so the first is
        # the division leader. `playoffSeed` is a *conference* seed and would be
        # the wrong answer here — seed 1 is one team per conference, not per
        # division.
        best = max((_stat_value(e, "winPercent") for e in entries), default=None)
        for entry in entries:
            team_id = str((entry.get("team") or {}).get("id") or "")
            if not team_id:
                continue
            wins = int(_stat_value(entry, "wins"))
            losses = int(_stat_value(entry, "losses"))
            ties = int(_stat_value(entry, "ties"))
            played = int(_stat_value(entry, "gamesPlayed")) or wins + losses + ties
            win_percent = _stat_value(entry, "winPercent")
            teams[team_id] = TeamRecord(
                wins=wins,
                losses=losses,
                ties=ties,
                win_percent=win_percent,
                games_played=played,
                streak=_stat_display(entry, "streak"),
                division_id=str(group_id) if group_id else None,
                division_leader=best is not None and win_percent >= best > 0,
                top_seed=int(_stat_value(entry, "playoffSeed")) == 1,
            )
        for child in node.get("children") or []:
            walk(child, str(group_id) if group_id else None)

    walk(payload, None)
    return Standings(league=league, teams=teams) if teams else None
