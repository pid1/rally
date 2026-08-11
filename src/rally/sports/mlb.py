"""MLB statsapi adapter: schedules and standings for baseball.

Baseball deliberately uses a different provider than everything else. MLB's
official API is the only source that carries radio at all — measured across a
full Rangers season, ESPN returned two radio entries and statsapi returned one
for every game, tagged home or away so the Rangers' feed is distinguishable
from the opponent's. As a bonus it is first-party and documented.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from rally.sports import (
    SportsEvent,
    Standings,
    TeamRecord,
    TeamSchedule,
    fetch_json,
    local_date_of,
    local_time_of,
)

SCHEDULE_URL = "https://statsapi.mlb.com/api/v1/schedule"
STANDINGS_URL = "https://statsapi.mlb.com/api/v1/standings"

# Regular season plus every postseason round. Spring training (S) and
# exhibitions (E) are excluded outright: they are never notable, and an
# exhibition on the list would read as a real game.
GAME_TYPES = "R,F,D,L,W"

# gameType → season type, matching ESPN's 1/2/3 vocabulary so notability can
# reason about both providers with one set of rules.
POSTSEASON_TYPES = {"F", "D", "L", "W"}


def _parse_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _split_broadcasts(game: dict, is_home: bool) -> tuple[list[str], list[str], bool]:
    """Split statsapi broadcasts into television and radio, keeping our side.

    ``isNational`` is the accurate national-TV signal for baseball — ESPN's
    ``market`` field reports 189 "national" entries across a Rangers season,
    most of them streaming rows rather than a national television window.
    """
    ours = "home" if is_home else "away"
    tv: list[str] = []
    radio: list[str] = []
    national_tv = False

    for entry in game.get("broadcasts") or []:
        if not isinstance(entry, dict):
            continue
        name = (entry.get("name") or "").strip()
        kind = (entry.get("type") or "").strip().upper()
        if not name:
            continue
        is_national = bool(entry.get("isNational"))
        side = (entry.get("homeAway") or "").lower()

        if kind == "TV":
            # National feeds plus our own regional network; never the
            # opponent's, which the family cannot receive.
            if is_national or side == ours or not side:
                if name not in tv:
                    tv.append(name)
                national_tv = national_tv or is_national
        elif kind in ("AM", "FM") and (side == ours or not side) and name not in radio:
            radio.append(name)

    return tv, radio, national_tv


def _event(team, game: dict, tz: ZoneInfo) -> SportsEvent | None:
    start = _parse_utc(game.get("gameDate"))
    if start is None:
        return None

    teams = game.get("teams") or {}
    home = (teams.get("home") or {}).get("team") or {}
    away = (teams.get("away") or {}).get("team") or {}
    is_home = str(home.get("id")) == str(team.team_key)
    ours, other = (home, away) if is_home else (away, home)

    tv, radio, national_tv = _split_broadcasts(game, is_home)
    if not radio and team.radio_station:
        radio = [team.radio_station]

    game_type = (game.get("gameType") or "R").strip().upper()
    series = (game.get("seriesDescription") or "").strip()
    # statsapi labels its own notable days — "Rangers Home Opener", "Jackie
    # Robinson Day", the Little League Classic. First-party and free.
    note = (game.get("description") or "").strip() or None
    if game_type in POSTSEASON_TYPES and series:
        note = series

    return SportsEvent(
        event_key=f"mlb:{game.get('gamePk')}",
        league=team.league,
        team_label=team.label,
        name=f"{away.get('name', '')} at {home.get('name', '')}".strip(),
        start_utc=start,
        local_date=local_date_of(start, tz),
        local_time=local_time_of(start, tz),
        local_hour=start.astimezone(tz).hour,
        season_type=3 if game_type in POSTSEASON_TYPES else 2,
        team_id=str(ours.get("id")) if ours.get("id") else None,
        opponent_id=str(other.get("id")) if other.get("id") else None,
        opponent_name=other.get("name"),
        is_home=is_home,
        tv=tuple(tv),
        radio=tuple(radio),
        national_tv=national_tv,
        note=note,
        series_game=game.get("seriesGameNumber"),
    )


def _games(payload: dict | None) -> list[dict]:
    if not payload:
        return []
    return [game for day in payload.get("dates") or [] for game in day.get("games") or []]


def fetch_schedule(team, tz: ZoneInfo, start: date, end: date) -> TeamSchedule:
    """Fetch one team's window, plus the full regular season behind it.

    Two calls rather than ESPN's one-that-happens-to-return-everything:
    statsapi honors ``startDate``/``endDate``, so the window call is cheap and
    carries broadcasts, while the season call skips the broadcast hydration it
    does not need. The window range is widened by a day on each side because
    the parameters filter on MLB's official date while we bucket on the family's
    local date, and the two disagree for late games in some timezones.
    """
    window = _fetch_window(team, tz, start, end)
    season = fetch_full_season(team, tz, start.year)
    return TeamSchedule(window=window, season=season)


def _fetch_window(team, tz: ZoneInfo, start: date, end: date) -> list[SportsEvent]:
    payload = fetch_json(
        SCHEDULE_URL,
        {
            "sportId": 1,
            "teamId": team.team_key,
            "startDate": f"{start - timedelta(days=1):%Y-%m-%d}",
            "endDate": f"{end + timedelta(days=1):%Y-%m-%d}",
            "gameTypes": GAME_TYPES,
            "hydrate": "broadcasts(all),team",
        },
    )

    events = []
    for game in _games(payload):
        event = _event(team, game, tz)
        if event and start <= event.local_date <= end:
            events.append(event)
    return events


def fetch_full_season(team, tz: ZoneInfo, season_year: int) -> list[SportsEvent]:
    """Every regular-season game, for opener and first-meeting detection."""
    payload = fetch_json(
        SCHEDULE_URL,
        {
            "sportId": 1,
            "teamId": team.team_key,
            "season": season_year,
            "gameTypes": "R",
            "hydrate": "team",
        },
    )
    events = [e for game in _games(payload) if (e := _event(team, game, tz))]
    events.sort(key=lambda e: e.start_utc)
    return events


def fetch_standings(league: str = "baseball/mlb") -> Standings | None:
    """Current records for both leagues, already keyed by division.

    statsapi groups by division natively and reports ``divisionLeader`` and
    ``sportRank`` directly, so nothing has to be inferred from seeding.
    """
    payload = fetch_json(STANDINGS_URL, {"leagueId": "103,104"})
    if not payload:
        return None

    teams: dict[str, TeamRecord] = {}
    for record in payload.get("records") or []:
        division_id = str((record.get("division") or {}).get("id") or "") or None
        for entry in record.get("teamRecords") or []:
            team_id = str((entry.get("team") or {}).get("id") or "")
            if not team_id:
                continue
            league_record = entry.get("leagueRecord") or {}
            try:
                win_percent = float(entry.get("winningPercentage") or 0)
            except TypeError, ValueError:
                win_percent = 0.0
            teams[team_id] = TeamRecord(
                wins=int(entry.get("wins") or 0),
                losses=int(entry.get("losses") or 0),
                ties=int(league_record.get("ties") or 0),
                win_percent=win_percent,
                games_played=int(entry.get("gamesPlayed") or 0),
                streak=str((entry.get("streak") or {}).get("streakCode") or ""),
                division_id=division_id,
                division_leader=bool(entry.get("divisionLeader")),
                top_seed=str(entry.get("sportRank") or "") == "1",
            )

    return Standings(league=league, teams=teams) if teams else None
