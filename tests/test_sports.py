"""Tests for the sports watchlist.

Every fixture in ``tests/fixtures/sports/`` is a real recorded response, pruned
of logos and other bulk but otherwise shaped exactly as the providers return it.
CI never touches the live services: the ``sports_http`` fixture routes every URL
to a recorded body and fails loudly on an unrouted one, so a new call site
cannot quietly start hitting the network.

The cases below are organized around the failure modes that produce a *silent,
plausible-looking wrong answer* — evening games on the wrong day, the
opponent's regional network, radio rendered as a TV channel, a season type that
returns the wrong half of the year.
"""

import json
from datetime import UTC, date, datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from rally.sports import espn, mlb
from rally.sports.notability import evaluate, first_meetings, season_opener_reasons
from rally.sports.watchlist import build_sections

FIXTURES = Path(__file__).parent / "fixtures" / "sports"

CHICAGO = ZoneInfo("America/Chicago")

# The recorded NFL regular-season fixture is the 2026 season, opening Sept 10.
NFL_WINDOW = (date(2026, 9, 1), date(2026, 9, 30))


def load(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text())


def team(**kwargs):
    """A FollowedTeam-shaped object. The adapters only read attributes."""
    defaults = {
        "provider": "espn",
        "league": "football/nfl",
        "team_key": "ne",
        "label": "Patriots",
        "radio_station": None,
        "active": True,
    }
    return SimpleNamespace(**{**defaults, **kwargs})


@pytest.fixture
def sports_http(monkeypatch):
    """Route provider URLs to recorded fixtures; refuse anything unrouted."""
    import requests

    routes: dict[str, dict] = {}
    calls: list[tuple[str, dict]] = []

    class FakeResponse:
        def __init__(self, payload):
            self._payload = payload

        def json(self):
            return self._payload

        def raise_for_status(self):
            return None

    def fake_get(url, params=None, **kwargs):
        params = params or {}
        calls.append((url, params))
        for pattern, payload in routes.items():
            if pattern in url:
                body = payload(params) if callable(payload) else payload
                if body is None:
                    raise requests.HTTPError("404")
                return FakeResponse(body)
        raise AssertionError(f"Unrouted sports request: {url} {params}")

    monkeypatch.setattr(requests, "get", fake_get)

    controller = SimpleNamespace(calls=calls)
    controller.route = lambda pattern, payload: routes.__setitem__(pattern, payload)
    return controller


# --- Local-date bucketing ------------------------------------------------------


def test_evening_game_buckets_to_the_local_day_not_the_utc_one():
    """The single most consequential bug this feature can have.

    A Rangers game the family watches on the 10th is stamped 2026-08-11T01:38Z.
    Bucketing by UTC date would file most evening games under *tomorrow*.
    """
    start = datetime(2026, 8, 11, 1, 38, tzinfo=UTC)
    from rally.sports import local_date_of

    assert start.date() == date(2026, 8, 11)  # what UTC bucketing would give
    assert local_date_of(start, CHICAGO) == date(2026, 8, 10)


def test_game_just_after_local_midnight_stays_on_the_new_day():
    from rally.sports import local_date_of

    just_after = datetime(2026, 8, 11, 5, 30, tzinfo=UTC)  # 12:30 AM CDT on the 11th
    assert local_date_of(just_after, CHICAGO) == date(2026, 8, 11)


def test_game_just_before_local_midnight_stays_on_the_old_day():
    from rally.sports import local_date_of

    just_before = datetime(2026, 8, 11, 4, 30, tzinfo=UTC)  # 11:30 PM CDT on the 10th
    assert local_date_of(just_before, CHICAGO) == date(2026, 8, 10)


# --- ESPN schedules ------------------------------------------------------------


def test_espn_fetches_all_three_season_types(sports_http):
    """A bare call returns only the season type the calendar is in — in August,
    three preseason games and no regular season at all."""
    by_type = {
        1: load("espn_nfl_preseason"),
        2: load("espn_nfl_regular"),
        3: load("espn_nfl_postseason"),
    }
    sports_http.route("/schedule", lambda params: by_type[int(params["seasontype"])])

    schedule = espn.fetch_schedule(team(), CHICAGO, *NFL_WINDOW)

    requested = sorted(params["seasontype"] for url, params in sports_http.calls)
    assert requested == [1, 2, 3]
    assert schedule.season, "the full season backs opener detection"


def test_espn_window_straddling_preseason_finds_the_regular_season_opener(sports_http):
    """The August case: without an explicit seasontype=2 call the opener is
    invisible, which is precisely the event this feature exists to flag."""
    by_type = {
        1: load("espn_nfl_preseason"),
        2: load("espn_nfl_regular"),
        3: load("espn_nfl_postseason"),
    }
    sports_http.route("/schedule", lambda params: by_type[int(params["seasontype"])])

    schedule = espn.fetch_schedule(team(), CHICAGO, *NFL_WINDOW)
    names = [e.name for e in schedule.window]

    assert any("New England Patriots at Seattle Seahawks" in name for name in names)
    assert all(e.season_type == 2 for e in schedule.window)


def test_espn_postseason_carries_its_round_headline(sports_http):
    sports_http.route("/schedule", lambda params: load("espn_nfl_postseason"))

    schedule = espn.fetch_schedule(
        team(team_key="kc", label="Chiefs"), CHICAGO, date(2025, 1, 1), date(2025, 2, 28)
    )
    postseason = [e for e in schedule.window if e.season_type == 3]

    assert postseason
    assert any(
        "Divisional" in (e.note or "") or "Championship" in (e.note or "") for e in postseason
    )


def test_espn_regional_entries_are_dropped_because_market_cannot_attribute_them():
    """ESPN tags every NHL regional feed ``Home`` regardless of who is home, and
    none of them is the followed team's own network — measured across a full
    recorded Stars season, all 58 are the opponent's channel.

    Keeping ``market == ours`` would surface exactly the network the family
    cannot receive. National feeds are the only ones we can attribute.
    """
    competition = {
        "competitors": [
            {"homeAway": "home", "team": {"abbreviation": "DAL", "id": "25"}},
            {"homeAway": "away", "team": {"abbreviation": "NYI", "id": "9"}},
        ],
        "broadcasts": [
            # The Islanders' network, tagged Home even though Dallas is home.
            {
                "type": {"shortName": "TV"},
                "market": {"type": "Home"},
                "media": {"shortName": "MSG2"},
            },
            {
                "type": {"shortName": "TV"},
                "market": {"type": "National"},
                "media": {"shortName": "TNT"},
            },
        ],
    }
    event = {"id": "1", "date": "2026-10-14T00:05Z", "name": "Islanders at Stars"}

    built = espn._team_event(team(team_key="DAL", league="hockey/nhl"), competition, event, CHICAGO)

    assert built.tv == ("TNT",)
    assert "MSG2" not in built.tv


def test_espn_regional_only_game_renders_tbd_rather_than_the_wrong_channel():
    competition = {
        "competitors": [
            {"homeAway": "home", "team": {"abbreviation": "DAL", "id": "25"}},
            {"homeAway": "away", "team": {"abbreviation": "SEA", "id": "124"}},
        ],
        "broadcasts": [
            {
                "type": {"shortName": "TV"},
                "market": {"type": "Home"},
                "media": {"shortName": "KONG"},
            },
        ],
    }
    event = {"id": "2", "date": "2026-10-15T00:05Z", "name": "Kraken at Stars"}

    built = espn._team_event(team(team_key="DAL", league="hockey/nhl"), competition, event, CHICAGO)

    assert built.tv == ()
    assert built.tv_label == "channel TBD"


def test_espn_never_concatenates_radio_into_the_tv_line():
    """`ERADM` is a radio callsign that reads exactly like a TV channel."""
    competition = {
        "competitors": [
            {"homeAway": "away", "team": {"abbreviation": "TEX", "id": "13"}},
            {"homeAway": "home", "team": {"abbreviation": "LAA", "id": "3"}},
        ],
        "broadcasts": [
            {
                "type": {"shortName": "TV"},
                "market": {"type": "National"},
                "media": {"shortName": "Peacock"},
            },
            {
                "type": {"shortName": "Radio"},
                "market": {"type": "National"},
                "media": {"shortName": "ERADM"},
            },
        ],
    }
    event = {"id": "3", "date": "2026-08-11T01:38Z", "name": "Rangers at Angels"}

    built = espn._team_event(
        team(team_key="TEX", league="baseball/mlb"), competition, event, CHICAGO
    )

    assert built.tv == ("Peacock",)
    assert built.radio == ("ERADM",)
    assert "ERADM" not in built.tv_label


def test_espn_reads_the_racing_broadcast_shape(sports_http):
    """Racing puts typed entries under geoBroadcasts and an untyped
    {market, names[]} list under broadcasts. Reading the wrong one yields no
    channel rather than an error."""
    sports_http.route("/scoreboard", load("espn_nascar_cup"))

    schedule = espn.fetch_schedule(
        team(league="racing/nascar-premier", team_key=None, label="NASCAR Cup Series"),
        CHICAGO,
        date(2026, 8, 10),
        date(2026, 8, 24),
    )

    assert schedule.window
    first = schedule.window[0]
    assert first.tv, "the racing fixture has a fully populated broadcast"
    assert first.tv_label != "channel TBD"
    assert first.is_race


def test_racing_radio_falls_back_to_the_generic_label(sports_http):
    sports_http.route("/scoreboard", load("espn_nascar_truck"))

    schedule = espn.fetch_schedule(
        team(league="racing/nascar-truck", team_key=None, label="NASCAR Truck Series"),
        CHICAGO,
        date(2026, 8, 10),
        date(2026, 8, 24),
    )

    assert all(e.radio == ("MRN or PRN",) for e in schedule.window)


def test_missing_broadcast_renders_tbd_and_the_event_survives(sports_http):
    """Routine over a 14-day window, especially early season. An event must
    never be suppressed just because its channel is unknown."""
    sports_http.route("/schedule", lambda params: load("espn_nfl_preseason"))

    schedule = espn.fetch_schedule(team(), CHICAGO, date(2026, 8, 1), date(2026, 8, 31))
    unannounced = [e for e in schedule.window if not e.tv]

    assert unannounced, "the recorded preseason fixture has a game with no broadcast"
    assert all(e.tv_label == "channel TBD" for e in unannounced)
    # The events still render — suppressing them is the failure this guards.
    assert len(schedule.window) == 3


def test_configured_radio_station_fills_the_gap(sports_http):
    sports_http.route("/schedule", lambda params: load("espn_nhl_regular"))

    schedule = espn.fetch_schedule(
        team(league="hockey/nhl", team_key="dal", label="Stars", radio_station="96.7 The Ticket"),
        CHICAGO,
        date(2026, 10, 1),
        date(2026, 10, 31),
    )

    assert all(e.radio == ("96.7 The Ticket",) for e in schedule.window)


# --- MLB ------------------------------------------------------------------------


def rangers():
    return team(provider="mlb", league="baseball/mlb", team_key="140", label="Rangers")


def test_mlb_carries_real_radio_for_every_game(sports_http):
    """The whole reason baseball uses a different provider."""
    sports_http.route("statsapi.mlb.com/api/v1/schedule", load("mlb_schedule"))

    schedule = mlb.fetch_schedule(rangers(), CHICAGO, date(2026, 8, 10), date(2026, 8, 24))

    assert schedule.window
    assert all(e.radio for e in schedule.window)
    assert all(e.radio_label != "radio TBD" for e in schedule.window)


def test_mlb_keeps_our_radio_feed_not_the_opponents():
    game = {
        "gamePk": 1,
        "gameDate": "2026-08-11T01:38:00Z",
        "gameType": "R",
        "teams": {
            "home": {"team": {"id": 108, "name": "Los Angeles Angels"}},
            "away": {"team": {"id": 140, "name": "Texas Rangers"}},
        },
        "broadcasts": [
            {"name": "KLAA 830", "type": "AM", "homeAway": "home"},
            {"name": "105.3 The Fan", "type": "FM", "homeAway": "away"},
            {"name": "Rangers Sports Network", "type": "TV", "homeAway": "away"},
        ],
    }

    built = mlb._event(rangers(), game, CHICAGO)

    assert built.radio == ("105.3 The Fan",)
    assert "KLAA 830" not in built.radio


def test_mlb_national_tv_uses_the_is_national_flag():
    game = {
        "gamePk": 2,
        "gameDate": "2026-08-11T01:38:00Z",
        "gameType": "R",
        "teams": {
            "home": {"team": {"id": 140, "name": "Texas Rangers"}},
            "away": {"team": {"id": 108, "name": "Los Angeles Angels"}},
        },
        "broadcasts": [{"name": "MLBN", "type": "TV", "isNational": True}],
    }

    assert mlb._event(rangers(), game, CHICAGO).national_tv is True


def test_mlb_postseason_is_typed_as_postseason():
    game = {
        "gamePk": 3,
        "gameDate": "2026-10-05T23:38:00Z",
        "gameType": "D",
        "seriesDescription": "AL Division Series",
        "teams": {
            "home": {"team": {"id": 140, "name": "Texas Rangers"}},
            "away": {"team": {"id": 117, "name": "Houston Astros"}},
        },
    }

    built = mlb._event(rangers(), game, CHICAGO)

    assert built.season_type == 3
    assert built.note == "AL Division Series"


def test_mlb_own_labels_are_carried_through():
    """statsapi labels its own notable days, first-party and free."""
    game = {
        "gamePk": 4,
        "gameDate": "2026-04-03T23:05:00Z",
        "gameType": "R",
        "description": "Rangers Home Opener",
        "teams": {
            "home": {"team": {"id": 140, "name": "Texas Rangers"}},
            "away": {"team": {"id": 117, "name": "Houston Astros"}},
        },
    }

    assert mlb._event(rangers(), game, CHICAGO).note == "Rangers Home Opener"


def test_mlb_spring_training_is_never_requested(sports_http):
    sports_http.route("statsapi.mlb.com/api/v1/schedule", load("mlb_schedule"))

    mlb.fetch_schedule(rangers(), CHICAGO, date(2026, 8, 10), date(2026, 8, 24))

    game_types = [params.get("gameTypes") for _url, params in sports_http.calls]
    assert all("S" not in (gt or "") for gt in game_types)


# --- Standings ------------------------------------------------------------------


def test_espn_standings_carry_records_and_divisions(sports_http):
    sports_http.route("/standings", load("espn_nfl_standings"))

    standings = espn.fetch_standings("football/nfl")

    assert standings is not None
    assert len(standings.teams) == 32
    assert all(record.division_id for record in standings.teams.values())


def test_level_three_is_requested_so_divisions_are_available(sports_http):
    """Without level=3 ESPN returns conference groupings only, and the schedule
    payload carries no team grouping at all."""
    sports_http.route("/standings", load("espn_nhl_standings"))

    espn.fetch_standings("hockey/nhl")

    assert all(params.get("level") == 3 for _url, params in sports_http.calls)


def test_division_rivals_are_detected_from_standings(sports_http):
    sports_http.route("/standings", load("espn_nfl_standings"))
    standings = espn.fetch_standings("football/nfl")

    # New England (17) and Buffalo (2) are both AFC East; Dallas (6) is not.
    assert standings.same_division("17", "2") is True
    assert standings.same_division("17", "6") is False


def test_mlb_standings_report_division_and_top_seed(sports_http):
    sports_http.route("statsapi.mlb.com/api/v1/standings", load("mlb_standings"))

    standings = mlb.fetch_standings()

    assert standings is not None
    assert len(standings.teams) == 30
    assert any(record.top_seed for record in standings.teams.values())
    assert any(record.division_leader for record in standings.teams.values())


def test_a_standings_outage_returns_none_rather_than_raising(sports_http):
    sports_http.route("/standings", lambda params: None)

    assert espn.fetch_standings("football/nfl") is None


# --- Notability -----------------------------------------------------------------
#
# The rules are per sport because a 17-game season and a 162-game season do not
# mean the same thing by "ordinary game". These cases pin each sport's answer to
# the *ordinary* game, which is the one that decides whether the block is
# readable at all.


def event(**kwargs):
    from rally.sports import SportsEvent

    start = kwargs.pop("start", datetime(2026, 8, 12, 0, 5, tzinfo=UTC))
    defaults = {
        "event_key": "espn:1",
        "league": "baseball/mlb",
        "team_label": "Rangers",
        "name": "Astros at Rangers",
        "start_utc": start,
        "local_date": start.astimezone(CHICAGO).date(),
        "local_time": "7:05 PM",
        "local_hour": start.astimezone(CHICAGO).hour,
        "season_type": 2,
    }
    return SportsEvent(**{**defaults, **kwargs})


def test_preseason_is_never_notable():
    notable, _ = evaluate(event(season_type=1, league="football/nfl"), None, {}, set())
    assert notable is False


def test_postseason_is_notable_with_its_round_as_the_reason():
    notable, reason = evaluate(
        event(season_type=3, league="hockey/nhl", note="West 1st Round - Game 1"), None, {}, set()
    )
    assert notable is True
    assert reason == "West 1st Round - Game 1"


def test_season_opener_is_notable():
    game = event(league="hockey/nhl")
    notable, reason = evaluate(game, None, {game.event_key: "Season opener"}, set())
    assert (notable, reason) == (True, "Season opener")


def test_every_nfl_regular_season_game_is_notable():
    """A deliberate per-sport departure: one game a week is at most two rows."""
    ordinary = event(
        league="football/nfl",
        name="Patriots at Dolphins",
        start=datetime(2026, 9, 13, 18, 0, tzinfo=UTC),  # Sunday, 1 PM Chicago
    )
    notable, reason = evaluate(ordinary, None, {}, set())
    assert notable is True
    assert reason == "Regular season"


def test_nfl_standalone_window_is_named_as_such():
    monday_night = event(
        league="football/nfl",
        name="Patriots at Bills",
        start=datetime(2026, 9, 15, 0, 15, tzinfo=UTC),  # Monday 7:15 PM Chicago
    )
    notable, reason = evaluate(monday_night, None, {}, set())
    assert (notable, reason) == (True, "Standalone window")


def test_an_ordinary_mlb_game_is_not_notable():
    """The rule that decides whether "Coming up" is readable: the Rangers alone
    contribute ~12 games to any two-week window."""
    notable, _ = evaluate(event(), None, {}, set())
    assert notable is False


def test_a_nationally_televised_mlb_game_is_notable():
    notable, reason = evaluate(event(national_tv=True), None, {}, set())
    assert (notable, reason) == (True, "National TV")


def test_an_mlb_league_special_day_is_notable():
    notable, reason = evaluate(event(note="Jackie Robinson Day"), None, {}, set())
    assert (notable, reason) == (True, "Jackie Robinson Day")


def test_an_ordinary_nhl_game_is_not_notable():
    notable, _ = evaluate(event(league="hockey/nhl", name="Blues at Stars"), None, {}, set())
    assert notable is False


def test_a_nationally_televised_nhl_game_is_notable():
    game = event(league="hockey/nhl", name="Blues at Stars", national_tv=True)
    notable, reason = evaluate(game, None, {}, set())
    assert (notable, reason) == (True, "National TV")


def test_every_race_is_notable():
    race = event(league="racing/nascar-premier", name="NASCAR Cup Series at Richmond", is_race=True)
    notable, reason = evaluate(race, None, {}, set())
    assert (notable, reason) == (True, "Race weekend")


def test_a_named_race_uses_its_own_name_as_the_reason():
    race = event(league="racing/nascar-premier", name="Daytona 500", is_race=True)
    notable, reason = evaluate(race, None, {}, set())
    assert (notable, reason) == (True, "Daytona 500")


def test_season_opener_is_derived_from_the_season_not_the_window():
    """The bug this guards: the first game *in a two-week window* is not the
    season opener, and calling it one would be wrong every day, all season."""
    season = [
        event(event_key="espn:1", is_home=False, start=datetime(2026, 9, 10, 0, 5, tzinfo=UTC)),
        event(event_key="espn:2", is_home=True, start=datetime(2026, 9, 20, 0, 5, tzinfo=UTC)),
        event(event_key="espn:3", is_home=False, start=datetime(2026, 9, 27, 0, 5, tzinfo=UTC)),
    ]
    reasons = season_opener_reasons(season)

    assert reasons["espn:1"] == "Season opener"
    assert reasons["espn:2"] == "Home opener"
    assert "espn:3" not in reasons


def test_first_meeting_is_scoped_to_division_rivals(sports_http):
    sports_http.route("/standings", load("espn_nfl_standings"))
    standings = espn.fetch_standings("football/nfl")

    season = [
        # New England (17) vs Buffalo (2): AFC East, so a division first meeting.
        event(event_key="espn:1", league="football/nfl", team_id="17", opponent_id="2"),
        # Then Dallas (6), a non-rival: first meeting, but not scoped in.
        event(event_key="espn:2", league="football/nfl", team_id="17", opponent_id="6"),
        # A second Buffalo game is no longer the first meeting.
        event(event_key="espn:3", league="football/nfl", team_id="17", opponent_id="2"),
    ]

    assert first_meetings(season, standings) == {"espn:1"}


def test_a_record_driven_reason_never_states_a_result(sports_http):
    """Standings are read to decide notability; scores remain a non-goal."""
    sports_http.route("/standings", load("espn_nfl_standings"))
    standings = espn.fetch_standings("football/nfl")

    reasons = []
    for team_id in list(standings.teams)[:12]:
        game = event(
            league="football/nfl",
            team_id="17",
            opponent_id=team_id,
            opponent_name="Opponent",
            start=datetime(2026, 9, 13, 18, 0, tzinfo=UTC),
        )
        _notable, reason = evaluate(game, standings, {}, set())
        reasons.append(reason)

    banned = ("won ", "lost ", "beat ", "-0 win", "final")
    assert not any(word in reason.lower() for reason in reasons for word in banned)


# --- Section assembly -----------------------------------------------------------


def stub_collect(monkeypatch, schedules, standings=None):
    import rally.sports.watchlist as watchlist_module

    monkeypatch.setattr(
        watchlist_module, "collect_events", lambda *a, **k: (schedules, standings or {})
    )


def test_tonight_lists_every_event_notable_or_not(monkeypatch):
    """Tonight is the replacement for the manual check and must never filter."""
    from rally.sports import TeamSchedule

    tonight = event(start=datetime(2026, 8, 11, 0, 5, tzinfo=UTC), tv=("Rangers Sports Network",))
    stub_collect(monkeypatch, [TeamSchedule(window=[tonight], season=[])])

    section, notices = build_sections([], CHICAGO, date(2026, 8, 10), set())

    assert "Tonight:" in section
    assert "Astros at Rangers" in section
    assert "TV: Rangers Sports Network" in section
    assert notices == []


def test_an_ordinary_upcoming_game_stays_out_of_coming_up(monkeypatch):
    from rally.sports import TeamSchedule

    ordinary = event(start=datetime(2026, 8, 15, 0, 5, tzinfo=UTC))
    stub_collect(monkeypatch, [TeamSchedule(window=[ordinary], season=[])])

    section, notices = build_sections([], CHICAGO, date(2026, 8, 10), set())

    assert "Nothing notable coming up." in section
    assert notices == []


def test_a_notable_upcoming_event_is_announced_once(monkeypatch):
    from rally.sports import TeamSchedule

    notable = event(
        event_key="espn:99",
        start=datetime(2026, 8, 15, 0, 5, tzinfo=UTC),
        national_tv=True,
    )
    stub_collect(monkeypatch, [TeamSchedule(window=[notable], season=[])])

    section, notices = build_sections([], CHICAGO, date(2026, 8, 10), set())
    assert "National TV" in section
    assert [e.event_key for e, _ in notices] == ["espn:99"]

    # The next morning, with the notice recorded, it is silent.
    section, notices = build_sections([], CHICAGO, date(2026, 8, 10), {"espn:99"})
    assert "Nothing notable coming up." in section
    assert notices == []


def test_an_already_announced_event_still_appears_in_tonight(monkeypatch):
    """Coming up is the heads-up; Tonight is the utility. Being announced
    earlier must not remove it from the day it actually happens."""
    from rally.sports import TeamSchedule

    today = event(
        event_key="espn:99", start=datetime(2026, 8, 11, 0, 5, tzinfo=UTC), national_tv=True
    )
    stub_collect(monkeypatch, [TeamSchedule(window=[today], season=[])])

    section, _ = build_sections([], CHICAGO, date(2026, 8, 10), {"espn:99"})

    assert "Tonight:" in section
    assert "Astros at Rangers" in section


def test_tv_and_radio_are_separate_lines(monkeypatch):
    from rally.sports import TeamSchedule

    game = event(
        start=datetime(2026, 8, 11, 0, 5, tzinfo=UTC),
        tv=("Peacock",),
        radio=("105.3 The Fan",),
    )
    stub_collect(monkeypatch, [TeamSchedule(window=[game], season=[])])

    section, _ = build_sections([], CHICAGO, date(2026, 8, 10), set())

    assert "    TV: Peacock" in section
    assert "    Radio: 105.3 The Fan" in section
    assert "Peacock, 105.3 The Fan" not in section


def test_an_empty_window_renders_the_quiet_section(monkeypatch):
    stub_collect(monkeypatch, [])

    section, notices = build_sections([], CHICAGO, date(2026, 8, 10), set())

    assert "Nothing scheduled tonight" in section
    assert notices == []


def test_a_total_provider_outage_degrades_to_an_empty_section(monkeypatch, sports_http):
    """A third-party outage must degrade the section, never fail the summary."""
    sports_http.route("site.api.espn.com", lambda params: None)
    sports_http.route("statsapi.mlb.com", lambda params: None)

    section, notices = build_sections(
        [team(league="hockey/nhl", team_key="dal", label="Stars")],
        CHICAGO,
        date(2026, 8, 10),
        set(),
    )

    assert section == "Nothing scheduled tonight, and nothing notable in the next two weeks."
    assert notices == []


# --- Announce-once persistence --------------------------------------------------


def test_notices_are_recorded_once_and_purged_when_stale(db_session):
    from rally.models import SportsEventNotice
    from rally.sports.watchlist import load_announced_keys, record_notices

    fresh = event(event_key="espn:new", start=datetime(2026, 8, 15, 0, 5, tzinfo=UTC))
    today = date(2026, 8, 10)

    record_notices(db_session, [(fresh, "National TV")], today)
    record_notices(db_session, [(fresh, "A better reason")], today)

    rows = db_session.query(SportsEventNotice).all()
    assert len(rows) == 1, "an event is announced once, and never re-announced"
    assert rows[0].notability_reason == "National TV", "the first reason is not rewritten"
    assert load_announced_keys(db_session) == {"espn:new"}


def test_notices_older_than_the_retention_window_are_purged(db_session):
    from rally.models import SportsEventNotice
    from rally.sports.watchlist import record_notices

    db_session.add(
        SportsEventNotice(
            event_key="espn:ancient",
            event_local_date="2026-06-01",
            announced_on="2026-06-01",
        )
    )
    db_session.commit()

    recent = event(event_key="espn:recent", start=datetime(2026, 8, 15, 0, 5, tzinfo=UTC))
    record_notices(db_session, [(recent, "National TV")], date(2026, 8, 10))

    keys = {row.event_key for row in db_session.query(SportsEventNotice).all()}
    assert keys == {"espn:recent"}


def test_an_inactive_team_is_not_followed(db_session):
    from rally.models import FollowedTeam
    from rally.sports.watchlist import load_followed_teams

    db_session.add(
        FollowedTeam(
            provider="espn", league="hockey/nhl", team_key="dal", label="Stars", active=True
        )
    )
    db_session.add(
        FollowedTeam(
            provider="espn", league="football/nfl", team_key="ne", label="Patriots", active=False
        )
    )
    db_session.commit()

    assert [t.label for t in load_followed_teams(db_session)] == ["Stars"]


# --- Settings CRUD ---------------------------------------------------------------


def test_followed_team_crud_round_trip(client):
    created = client.post(
        "/api/followed-teams",
        json={"provider": "espn", "league": "hockey/nhl", "team_key": "dal", "label": "Stars"},
    )
    assert created.status_code == 201
    team_id = created.json()["id"]

    assert [t["label"] for t in client.get("/api/followed-teams").json()] == ["Stars"]

    updated = client.put(
        f"/api/followed-teams/{team_id}", json={"radio_station": "96.7 The Ticket", "active": False}
    )
    assert updated.json()["radio_station"] == "96.7 The Ticket"
    assert updated.json()["active"] is False
    assert updated.json()["label"] == "Stars", "omitted fields are left alone"

    assert client.delete(f"/api/followed-teams/{team_id}").status_code == 204
    assert client.get("/api/followed-teams").json() == []


def test_a_racing_series_is_stored_without_a_team_key(client):
    """A racing series is a league-level subscription; forcing a team id would
    be a lie."""
    created = client.post(
        "/api/followed-teams",
        json={
            "provider": "espn",
            "league": "racing/nascar-premier",
            "team_key": None,
            "label": "NASCAR Cup Series",
        },
    )

    assert created.status_code == 201
    assert created.json()["team_key"] is None


def test_updating_a_missing_followed_team_is_404(client):
    assert client.put("/api/followed-teams/999", json={"label": "Nope"}).status_code == 404
    assert client.delete("/api/followed-teams/999").status_code == 404


def test_the_connection_test_reports_an_empty_window_honestly(client, db_session, sports_http):
    """A wrong key and an off-season team look identical from here, so the
    endpoint says so rather than claiming failure."""
    from rally.models import FollowedTeam

    sports_http.route("/schedule", lambda params: {"events": []})
    team_row = FollowedTeam(
        provider="espn", league="hockey/nhl", team_key="typo", label="Stars", active=True
    )
    db_session.add(team_row)
    db_session.commit()

    result = client.post(f"/api/followed-teams/{team_row.id}/test").json()

    assert result["success"] is True
    assert result["events"] == []
    assert "check the league and team key" in result["message"]


def test_the_connection_test_returns_the_window_it_found(client, db_session, sports_http):
    from rally.models import FollowedTeam

    sports_http.route("/scoreboard", load("espn_nascar_cup"))
    team_row = FollowedTeam(
        provider="espn",
        league="racing/nascar-premier",
        team_key=None,
        label="NASCAR Cup Series",
        active=True,
    )
    db_session.add(team_row)
    db_session.commit()

    result = client.post(f"/api/followed-teams/{team_row.id}/test").json()

    assert result["success"] is True
    assert result["events"]
    assert all(e["radio"] for e in result["events"])
