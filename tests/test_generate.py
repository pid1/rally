"""Tests for the LLM-independent helpers in rally.generator.generate.

The parsing helpers (format_weather, _extract_json_object, _is_event_declined)
are self-independent, so they're called on a bare instance built with
__new__ (bypassing the network/DB/client work in __init__).

The DB helpers (load_todos, load_recent_stem_concepts, save_stem_concept,
save_snapshot) open their own SessionLocal(), so the gen_db fixture points that
at an isolated in-memory engine and seeds through the same connection.
"""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest
from icalendar import Event, vCalAddress
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from rally.database import Base
from rally.generator.generate import STEM_REPEAT_WINDOW_DAYS, SummaryGenerator
from rally.models import DashboardSnapshot, StemConceptHistory, Todo


def make_generator(tz: str = "UTC") -> SummaryGenerator:
    """Build a SummaryGenerator without running __init__ (no DB/clients/config)."""
    gen = SummaryGenerator.__new__(SummaryGenerator)
    gen.local_tz = ZoneInfo(tz)
    gen.local_tz_name = tz
    gen._db_settings = {}
    return gen


@pytest.fixture
def gen_db(monkeypatch):
    """Point generate.SessionLocal at an isolated in-memory DB; yield a session."""
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(bind=engine)
    testing_session_local = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    monkeypatch.setattr("rally.generator.generate.SessionLocal", testing_session_local)
    session = testing_session_local()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


# --- format_weather ------------------------------------------------------------

_CURRENT_DWML = (
    "<dwml><data type='current observations'><parameters>"
    "<temperature type='apparent'><value>72</value></temperature>"
    "<weather><weather-conditions weather-summary='Sunny'/></weather>"
    "<humidity><value>50</value></humidity>"
    "</parameters></data></dwml>"
)

_FORECAST_DWML = (
    "<dwml><data type='forecast'>"
    "<time-layout><layout-key>k1</layout-key>"
    "<start-valid-time period-name='Today'/><start-valid-time period-name='Tonight'/>"
    "</time-layout>"
    "<parameters><wordedForecast time-layout='k1'>"
    "<text>Sunny and warm</text><text>Clear overnight</text>"
    "</wordedForecast></parameters></data></dwml>"
)


def test_format_weather_none_returns_placeholder():
    assert make_generator().format_weather(None) == "No weather data available."


def test_format_weather_invalid_xml_returns_placeholder():
    assert make_generator().format_weather("not xml <<<") == "No weather data available."


def test_format_weather_current_conditions():
    out = make_generator().format_weather(_CURRENT_DWML)
    assert out == "Current conditions: 72°F, Sunny. Humidity 50%"


def test_format_weather_worded_forecast():
    out = make_generator().format_weather(_FORECAST_DWML)
    assert out == "Forecast:\n  Today: Sunny and warm\n  Tonight: Clear overnight"


# --- _extract_json_object ------------------------------------------------------


def test_extract_json_plain():
    assert make_generator()._extract_json_object('{"a": 1}') == {"a": 1}


def test_extract_json_strips_code_fence():
    assert make_generator()._extract_json_object('```json\n{"a": 1}\n```') == {"a": 1}


def test_extract_json_ignores_surrounding_noise():
    assert make_generator()._extract_json_object('Here you go: {"a": 1}. Thanks!') == {"a": 1}


def test_extract_json_is_brace_and_string_aware():
    # Braces inside a string value must not end the object early.
    assert make_generator()._extract_json_object('{"a": {"b": "}"}}') == {"a": {"b": "}"}}


def test_extract_json_returns_none_when_absent():
    assert make_generator()._extract_json_object("no json here") is None


def test_extract_json_returns_none_when_unbalanced():
    assert make_generator()._extract_json_object('{"a": 1') is None


def test_extract_json_returns_none_for_empty():
    assert make_generator()._extract_json_object("") is None


# --- _is_event_declined --------------------------------------------------------


def _attendee(email: str, partstat: str | None = None) -> vCalAddress:
    addr = vCalAddress(f"mailto:{email}")
    if partstat:
        addr.params["PARTSTAT"] = partstat
    return addr


def test_declined_when_status_cancelled():
    ev = Event()
    ev.add("status", "CANCELLED")
    assert make_generator()._is_event_declined(ev) is True


def test_not_declined_without_attendees():
    assert make_generator()._is_event_declined(Event()) is False


def test_owner_email_declined_partstat():
    ev = Event()
    ev.add("attendee", _attendee("me@example.com", "DECLINED"))
    ev.add("attendee", _attendee("other@example.com", "ACCEPTED"))
    assert make_generator()._is_event_declined(ev, owner_email="me@example.com") is True


def test_owner_email_accepted_partstat():
    ev = Event()
    ev.add("attendee", _attendee("me@example.com", "ACCEPTED"))
    assert make_generator()._is_event_declined(ev, owner_email="me@example.com") is False


def test_all_attendees_declined_heuristic():
    ev = Event()
    ev.add("attendee", _attendee("a@example.com", "DECLINED"))
    ev.add("attendee", _attendee("b@example.com", "DECLINED"))
    assert make_generator()._is_event_declined(ev) is True


def test_not_all_declined_is_not_declined():
    ev = Event()
    ev.add("attendee", _attendee("a@example.com", "DECLINED"))
    ev.add("attendee", _attendee("b@example.com", "ACCEPTED"))
    assert make_generator()._is_event_declined(ev) is False


def test_outlook_busystatus_free_with_declined():
    ev = Event()
    ev.add("X-MICROSOFT-CDO-BUSYSTATUS", "FREE")
    ev.add("attendee", _attendee("a@example.com", "DECLINED"))
    ev.add("attendee", _attendee("b@example.com", "ACCEPTED"))
    assert make_generator()._is_event_declined(ev) is True


# --- load_todos reminder window ------------------------------------------------


def test_load_todos_respects_reminder_window(gen_db, frozen_now):
    frozen_now(datetime(2026, 3, 1, 12, tzinfo=UTC))
    # Outside its window: due Mar 20, remind 5 days -> window opens Mar 15 > today.
    gen_db.add(Todo(title="TooEarly", completed=False, due_date="2026-03-20", remind_days_before=5))
    # Inside its window: due Mar 3, remind 5 days -> window opened Feb 26 <= today.
    gen_db.add(Todo(title="InWindow", completed=False, due_date="2026-03-03", remind_days_before=5))
    # No due date -> always included.
    gen_db.add(Todo(title="NoDue", completed=False))
    gen_db.commit()

    out = make_generator().load_todos()

    assert "InWindow" in out
    assert "NoDue" in out
    assert "TooEarly" not in out


# --- STEM concept history ------------------------------------------------------


def test_load_recent_stem_concepts_windows_and_dedupes(gen_db, frozen_now):
    frozen_now(datetime(2026, 3, 1, 12, tzinfo=UTC))
    # Within the 60-day window (cutoff = 2026-01-30), inserted oldest-id first.
    gen_db.add(StemConceptHistory(title="Photosynthesis", field="Biology", used_on="2026-02-15"))
    gen_db.add(StemConceptHistory(title="photosynthesis", field="Biology", used_on="2026-02-20"))
    # Older than the window -> excluded.
    gen_db.add(StemConceptHistory(title="Gravity", field="Physics", used_on="2025-12-01"))
    gen_db.commit()

    titles = make_generator().load_recent_stem_concepts()

    # Case-insensitive dedupe keeps the newest-id occurrence; Gravity is out of window.
    assert titles == ["photosynthesis"]
    assert STEM_REPEAT_WINDOW_DAYS == 60


def test_save_stem_concept_records_and_dedupes_same_day(gen_db, frozen_now):
    frozen_now(datetime(2026, 3, 1, 12, tzinfo=UTC))
    gen = make_generator()

    gen.save_stem_concept({"title": "Volcanoes", "field": "Geology"})
    # Same title (case-insensitive) same day -> no duplicate row.
    gen.save_stem_concept({"title": "volcanoes"})

    rows = gen_db.query(StemConceptHistory).all()
    assert len(rows) == 1
    assert rows[0].title == "Volcanoes"
    assert rows[0].used_on == "2026-03-01"


def test_save_stem_concept_noop_without_title(gen_db):
    gen = make_generator()

    gen.save_stem_concept(None)
    gen.save_stem_concept({"field": "Geology"})
    gen.save_stem_concept({"title": "   "})

    assert gen_db.query(StemConceptHistory).count() == 0


# --- save_snapshot single-active invariant -------------------------------------


def test_save_snapshot_deactivates_prior_same_day(gen_db, frozen_now):
    frozen_now(datetime(2026, 3, 1, 12, tzinfo=UTC))
    gen = make_generator()

    gen.save_snapshot({"summary": "first"})
    gen.save_snapshot({"summary": "second"})

    snapshots = gen_db.query(DashboardSnapshot).filter(DashboardSnapshot.date == "2026-03-01").all()
    active = [s for s in snapshots if s.is_active]
    assert len(snapshots) == 2
    assert len(active) == 1
    assert active[0].data == {"summary": "second"}
