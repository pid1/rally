"""Which upcoming events earn a "Coming up" mention, and why.

Every rule returns a short reason string alongside its verdict, so a row can
say *why* it earned the slot. The rules are **per sport** on purpose: a 17-game
season and a 162-game season do not mean the same thing by "ordinary game", and
a single global rule would either bury the Rangers' openers under a dozen
ordinary games or drop half the Patriots' season.

Two hard boundaries:

* **Preseason is never notable.** It still appears in "Tonight" on its day.
* **Reasons cite records and streaks, never results.** "Bills are 6-0" is
  context; the score of last Sunday's game is not, and scores are a non-goal.
"""

from __future__ import annotations

from rally.sports import SportsEvent, Standings

# An NFL team is not meaningfully "undefeated" in week 1. Four games in, it is.
UNDEFEATED_MIN_GAMES = 4

# Streak lengths that mean something in each sport, scaled to season length.
NFL_HOT_STREAK = 4
NHL_HOT_STREAK = 5
MLB_HOT_STREAK = 7

# Record-driven rules stay quiet early: a division lead in April is noise.
NHL_RECORD_RULES_FROM_MONTH = 12  # December
MLB_RECORD_RULES_FROM_MONTH = 9  # September

# NFL standalone windows. Sunday afternoon is the ordinary case; anything else
# is the game the family plans an evening around.
SUNDAY = 6
NFL_PRIMETIME_HOUR = 19

# ESPN's event notes mix two different things. Some are event *identity* and are
# exactly the reason we want — "NFL Munich Game", "NHL Global Series". Others
# are scheduling metadata that says nothing about whether an event is worth
# watching: measured on a real Patriots season, three of seventeen games carry
# "Flex Game: 1/2 or 1/3". Treating those as notability would mark an ordinary
# NHL game notable and, in the NFL, replace a useful reason with a shrug.
_SCHEDULING_NOTE_PREFIXES = ("flex",)


def _is_event_identity(note: str | None) -> bool:
    if not note:
        return False
    return not note.strip().lower().startswith(_SCHEDULING_NOTE_PREFIXES)


def _record_phrase(label: str, record) -> str | None:
    """A record or streak, phrased for a reason string. Never a game result."""
    if record is None:
        return None
    if (
        record.losses == 0
        and record.ties == 0
        and record.games_played >= UNDEFEATED_MIN_GAMES
    ):
        return f"{label} are {record.wins}-0"
    return None


def _streak_phrase(label: str, record, threshold: int) -> str | None:
    if record is None or record.streak_wins < threshold:
        return None
    return f"{label} have won {record.streak_wins} straight"


def _universal(event: SportsEvent, season_openers: dict[str, str]) -> str | None:
    """Rules that hold in every sport. Returns a reason, or None."""
    if event.season_type == 3:
        # ESPN's round headline ("AFC Divisional Playoffs", "West 1st Round -
        # Game 1", "Super Bowl LIX") and statsapi's seriesDescription are both
        # better labels than anything we could synthesize.
        return event.note or "Postseason"

    opener = season_openers.get(event.event_key)
    if opener:
        return opener

    # statsapi labels its own openers and league days, and ESPN names its
    # special events; use them verbatim when they identify the event.
    if _is_event_identity(event.note):
        return event.note

    return None


def season_opener_reasons(full_season: list[SportsEvent]) -> dict[str, str]:
    """Map event keys to opener reasons, from a team's full regular season.

    The season opener is the first regular-season event; the home opener is the
    first with the followed team at home. One event can be both, in which case
    "Season opener" wins — it is the larger fact.
    """
    reasons: dict[str, str] = {}
    regular = [e for e in full_season if e.season_type == 2]
    if not regular:
        return reasons

    home = [e for e in regular if e.is_home]
    if home:
        reasons[home[0].event_key] = "Home opener"
    reasons[regular[0].event_key] = "Season opener"
    return reasons


def first_meetings(
    full_season: list[SportsEvent], standings: Standings | None
) -> set[str]:
    """Event keys that are the season's first meeting with a division rival.

    Scoped to the division rather than every opponent: an unscoped rule fires on
    nearly every game in October, which is the opposite of a filter.
    """
    if standings is None:
        return set()

    keys: set[str] = set()
    seen: set[str] = set()
    for event in full_season:
        if event.season_type != 2 or not event.opponent_id:
            continue
        if event.opponent_id in seen:
            continue
        seen.add(event.opponent_id)
        if standings.same_division(event.team_id, event.opponent_id):
            keys.add(event.event_key)
    return keys


def _nfl(event: SportsEvent, standings: Standings | None) -> str:
    """Every regular-season and postseason NFL game qualifies.

    A deliberate departure from "ordinary games don't qualify", on volume: the
    team plays once a week, so every NFL game in a 14-day window is at most two
    rows. There is no ordinary NFL game in the sense that there is an ordinary
    Tuesday in August against the Angels.
    """
    if standings:
        if standings.same_division(event.team_id, event.opponent_id):
            return "Division rival"
        for label, team_id in (
            (event.opponent_name or "Opponent", event.opponent_id),
            (event.team_label, event.team_id),
        ):
            phrase = _record_phrase(label, standings.get(team_id))
            if phrase:
                return phrase
            phrase = _streak_phrase(label, standings.get(team_id), NFL_HOT_STREAK)
            if phrase:
                return phrase

    # Not `market: National` — ESPN reports every NFL game as national,
    # including regional Sunday-afternoon CBS windows, so the field cannot
    # distinguish anything. The standalone windows are what the family plans an
    # evening around, and the kickoff slot identifies them exactly.
    if event.local_date.weekday() != SUNDAY or event.local_hour >= NFL_PRIMETIME_HOUR:
        return "Standalone window"
    return "Regular season"


def _nhl(
    event: SportsEvent, standings: Standings | None, first_meeting: bool
) -> str | None:
    """82 games, so filter. Nationally televised games are 12 of them."""
    if event.national_tv:
        return "National TV"
    if first_meeting:
        return "First division meeting"

    if standings and event.local_date.month >= NHL_RECORD_RULES_FROM_MONTH:
        opponent = standings.get(event.opponent_id)
        if opponent and opponent.top_seed:
            return f"{event.opponent_name or 'Opponent'} lead the conference"
        for label, team_id in (
            (event.opponent_name or "Opponent", event.opponent_id),
            (event.team_label, event.team_id),
        ):
            phrase = _streak_phrase(label, standings.get(team_id), NHL_HOT_STREAK)
            if phrase:
                return phrase

    return None


def _mlb(
    event: SportsEvent, standings: Standings | None, first_meeting: bool
) -> str | None:
    """162 games, so filter hardest — this is the rule that decides whether
    "Coming up" is readable at all."""
    if event.national_tv:
        return "National TV"
    if first_meeting and event.series_game == 1:
        return "First division series"

    if standings and event.local_date.month >= MLB_RECORD_RULES_FROM_MONTH:
        opponent = standings.get(event.opponent_id)
        if opponent and opponent.top_seed:
            return (
                f"{event.opponent_name or 'Opponent'} have the best record in baseball"
            )
        ours = standings.get(event.team_id)
        if ours and ours.division_leader:
            return f"{event.team_label} lead the division"

    if standings:
        for label, team_id in (
            (event.opponent_name or "Opponent", event.opponent_id),
            (event.team_label, event.team_id),
        ):
            phrase = _streak_phrase(label, standings.get(team_id), MLB_HOT_STREAK)
            if phrase:
                return phrase

    return None


def _racing(event: SportsEvent) -> str:
    """Every points race qualifies — roughly one weekend a week, three series,
    running on different networks on different nights.

    ESPN gives real names to the crown jewels ("Daytona 500", "NASCAR Cup
    Series All Star Race") and a generic "NASCAR Cup Series at Richmond" to
    everything else, so the reason falls back to the plain fact.
    """
    name = event.name or ""
    generic = " at " in name and name.lower().startswith("nascar")
    return "Race weekend" if generic or not name else name


def evaluate(
    event: SportsEvent,
    standings: Standings | None,
    season_openers: dict[str, str],
    first_meeting_keys: set[str],
) -> tuple[bool, str]:
    """Decide whether an upcoming event is notable, and why.

    Returns ``(notable, reason)``. The reason is meaningful only when notable.
    """
    if event.season_type == 1:
        return False, ""

    if event.is_race:
        return True, _racing(event)

    universal = _universal(event, season_openers)
    if universal:
        return True, universal

    first_meeting = event.event_key in first_meeting_keys
    league = event.league

    if league == "football/nfl":
        return True, _nfl(event, standings)
    if league == "hockey/nhl":
        reason = _nhl(event, standings, first_meeting)
        return (True, reason) if reason else (False, "")
    if league == "baseball/mlb":
        reason = _mlb(event, standings, first_meeting)
        return (True, reason) if reason else (False, "")

    # An unrecognized league falls back to the universal rules only. Better a
    # quiet section than a flood from a sport whose volume we haven't reasoned
    # about.
    return False, ""
