"""Sports watchlist: a 14-day, broadcast-aware view of the family's teams.

The shape of this package follows the shape of the problem:

* ``espn`` and ``mlb`` are the two provider adapters. Baseball deliberately uses
  a different provider than everything else — MLB's official API is the only
  source that carries radio at all, and radio is a headline requirement.
* ``notability`` decides which upcoming events earn a "Coming up" mention. Its
  rules are per sport, because a 17-game season and a 162-game season do not
  mean the same thing by "ordinary game".
* This module normalizes both providers into ``SportsEvent``, fans the fetches
  out concurrently, and renders the two blocks the summary consumes.

Every provider call is best-effort. A third-party outage degrades to a missing
section — never a failed summary.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

# One short budget for the whole fan-out, so a dozen third-party endpoints
# cannot collectively delay the 4:00 AM run.
PER_REQUEST_TIMEOUT_SECONDS = 8
OVERALL_TIMEOUT_SECONDS = 25

WINDOW_DAYS = 14

# Notices are kept a month past the event so the announce-once record survives
# any plausible regeneration, then purged.
NOTICE_RETENTION_DAYS = 30

USER_AGENT = "Rally family dashboard (https://github.com/pid1/rally)"

# Races are carried by MRN or PRN depending on the track. A per-track mapping
# would be more precise but is a static table someone has to notice has gone
# stale; a listener finds the right one in seconds.
NASCAR_RADIO = "MRN or PRN"


@dataclass(frozen=True)
class TeamRecord:
    """A team's current standing, as far as notability needs to care."""

    wins: int = 0
    losses: int = 0
    ties: int = 0
    win_percent: float = 0.0
    games_played: int = 0
    streak: str = ""  # e.g. "W6"
    division_id: str | None = None
    division_leader: bool = False  # Best record in its own division
    top_seed: bool = False  # Best in its conference (ESPN) / in MLB (statsapi)

    @property
    def streak_wins(self) -> int:
        """Length of the current winning streak, 0 if the streak is a losing one."""
        if self.streak.startswith("W"):
            try:
                return int(self.streak[1:])
            except ValueError:
                return 0
        return 0


@dataclass(frozen=True)
class TeamSchedule:
    """One followed team's window, plus the full season behind it.

    Both are needed and they are not the same question. The window is what gets
    rendered; the season is what "is this the opener" and "is this the first
    meeting" are answered against. Deriving either from the window alone would
    label the first game *in the window* as the season opener — a confidently
    wrong answer, every day, all season.
    """

    window: list[SportsEvent] = field(default_factory=list)
    season: list[SportsEvent] = field(default_factory=list)


@dataclass(frozen=True)
class Standings:
    """Current records for one league, keyed by the provider's team id."""

    league: str
    teams: dict[str, TeamRecord] = field(default_factory=dict)

    def get(self, team_id: str | None) -> TeamRecord | None:
        return self.teams.get(str(team_id)) if team_id is not None else None

    def same_division(self, a: str | None, b: str | None) -> bool:
        """Whether two teams share a division, per the standings grouping."""
        ra, rb = self.get(a), self.get(b)
        if not ra or not rb or ra.division_id is None:
            return False
        return ra.division_id == rb.division_id


@dataclass(frozen=True)
class SportsEvent:
    """One game or race, normalized across providers and bucketed to local time."""

    event_key: str  # provider + event id; the announce-once key
    league: str
    team_label: str  # The followed team's display name
    name: str  # "Texas Rangers at Los Angeles Angels"
    start_utc: datetime
    local_date: date
    local_time: str  # "7:05 PM"
    local_hour: int  # 24-hour local start; how the NFL's standalone windows are found
    season_type: int  # 1 preseason, 2 regular, 3 postseason
    team_id: str | None = None
    opponent_id: str | None = None
    opponent_name: str | None = None
    is_home: bool | None = None
    tv: tuple[str, ...] = ()
    radio: tuple[str, ...] = ()
    national_tv: bool = False
    note: str | None = None  # Round headline, or MLB's own event label
    series_game: int | None = None  # MLB position within a series
    is_race: bool = False

    @property
    def tv_label(self) -> str:
        """Never an empty string: an unknown channel is stated, not implied."""
        return ", ".join(self.tv) if self.tv else "channel TBD"

    @property
    def radio_label(self) -> str:
        return ", ".join(self.radio) if self.radio else "radio TBD"


def local_date_of(start_utc: datetime, tz: ZoneInfo) -> date:
    """Bucket a UTC instant to its local calendar date.

    Load-bearing: a US evening game crosses midnight UTC, so a Rangers game the
    family watches on the 10th is timestamped ``2026-08-11T01:38Z``. Bucketing
    by UTC date puts most evening games on *tomorrow*, inverting the point of
    the feature.
    """
    return start_utc.astimezone(tz).date()


def local_time_of(start_utc: datetime, tz: ZoneInfo) -> str:
    local = start_utc.astimezone(tz)
    return local.strftime("%-I:%M %p") if local.minute else local.strftime("%-I %p")


def fetch_json(url: str, params: dict | None = None) -> dict | None:
    """GET and parse JSON, returning None on any failure.

    Deliberately swallowing: every caller is one of a dozen independent fetches
    whose individual failure must degrade the section, not break it.
    """
    import requests

    try:
        response = requests.get(
            url,
            params=params or {},
            timeout=PER_REQUEST_TIMEOUT_SECONDS,
            headers={"User-Agent": USER_AGENT},
        )
        response.raise_for_status()
        return response.json()
    except Exception as e:  # noqa: BLE001 — a provider outage is not our error
        print(f"Sports: request failed for {url}: {e}")
        return None


def gather(tasks: list) -> list:
    """Run zero-argument callables concurrently under one overall budget.

    Results come back in submission order. A task that raises or overruns the
    budget contributes ``None`` rather than failing the batch.
    """
    if not tasks:
        return []

    results: list = [None] * len(tasks)
    with ThreadPoolExecutor(max_workers=min(len(tasks), 8)) as pool:
        futures = {pool.submit(task): index for index, task in enumerate(tasks)}
        for future, index in futures.items():
            try:
                results[index] = future.result(timeout=OVERALL_TIMEOUT_SECONDS)
            except Exception as e:  # noqa: BLE001
                print(f"Sports: fetch task {index} failed: {e}")
    return results


def collect_events(
    followed: list,
    tz: ZoneInfo,
    today: date,
) -> tuple[list[TeamSchedule], dict[str, Standings]]:
    """Fetch every followed team's schedule plus the standings each sport needs.

    Returns one ``TeamSchedule`` per followed team and standings keyed by
    league. Both are best-effort: a provider that fails contributes nothing and
    is not retried.
    """
    from rally.sports import espn, mlb

    window_end = today + timedelta(days=WINDOW_DAYS)

    schedule_tasks = []
    for team in followed:
        if team.provider == "mlb":
            schedule_tasks.append(
                lambda t=team: mlb.fetch_schedule(t, tz, today, window_end)
            )
        else:
            schedule_tasks.append(
                lambda t=team: espn.fetch_schedule(t, tz, today, window_end)
            )

    # One standings call per distinct league that has a team-based rule. Racing
    # standings are driver-centric, so a series subscription needs none.
    leagues = sorted(
        {
            t.league
            for t in followed
            if t.team_key and not t.league.startswith("racing/")
        }
    )
    standings_tasks = [
        (
            (lambda lg=lg: mlb.fetch_standings(lg))
            if lg == "baseball/mlb"
            else (lambda lg=lg: espn.fetch_standings(lg))
        )
        for lg in leagues
    ]

    outcomes = gather(schedule_tasks + standings_tasks)
    schedule_results = outcomes[: len(schedule_tasks)]
    standings_results = outcomes[len(schedule_tasks) :]

    schedules = [result for result in schedule_results if result]

    standings = {
        league: result
        for league, result in zip(leagues, standings_results, strict=True)
        if result
    }
    return schedules, standings
