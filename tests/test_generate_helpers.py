"""Tests for the non-LLM parts of rally.generator.generate.

Covers __init__ (config/DB + stubbed client construction), the DB data-loaders
(load_meal_plans, _load_ai_setting), and the external fetches (fetch_weather,
fetch_calendars / _fetch_ics_calendar). No LLM is invoked and no real network
happens: HTTP is stubbed with mock_requests, CalDAV with mock_caldav, and the
LLM client constructors with mock_llm.
"""

from datetime import UTC, datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from rally.database import Base
from rally.generator import generate as generate_module
from rally.generator.generate import LLMModelLimitError, SummaryGenerator
from rally.models import (
    AISettingsHistory,
    Calendar,
    FamilyMember,
    MealPlan,
    Setting,
    ShoppingItem,
    ShoppingStore,
    Todo,
)


def make_generator(tz: str = "UTC") -> SummaryGenerator:
    """Build a SummaryGenerator without running __init__, with the attributes the
    non-__init__ methods read."""
    gen = SummaryGenerator.__new__(SummaryGenerator)
    gen.local_tz = ZoneInfo(tz)
    gen.local_tz_name = tz
    gen._db_settings = {}
    gen.config = {}
    gen.calendar_owners = {}
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


def _seed_settings(session, settings: dict):
    for key, value in settings.items():
        session.add(Setting(key=key, value=value))
    session.commit()


_ICS_HEADER = "BEGIN:VCALENDAR\r\nVERSION:2.0\r\n"
_ICS_FOOTER = "END:VCALENDAR"


def _ics(summary: str, dtstart: str = "20260316T100000Z", extra: str = "") -> str:
    return (
        f"{_ICS_HEADER}BEGIN:VEVENT\r\nSUMMARY:{summary}\r\n"
        f"DTSTART:{dtstart}\r\n{extra}END:VEVENT\r\n{_ICS_FOOTER}"
    )


# --- __init__ ------------------------------------------------------------------


def test_init_anthropic_provider_from_db(gen_db, mock_llm):
    _seed_settings(
        gen_db,
        {
            "llm_provider": "anthropic",
            "llm_anthropic_model": "claude-x",
            "llm_anthropic_api_key": "sk-test",
            "local_timezone": "America/Chicago",
            "stem_concept_enabled": "true",
            "shopping_list_in_summary_enabled": "true",
            "packing_lists_in_summary_enabled": "false",
        },
    )

    gen = SummaryGenerator()

    assert gen.provider == "anthropic"
    assert gen.model == "claude-x"
    assert gen.local_tz_name == "America/Chicago"
    assert gen.stem_concept_enabled is True
    assert gen.shopping_list_in_summary_enabled is True
    assert gen.packing_lists_in_summary_enabled is False


def test_init_local_provider_from_db(gen_db, mock_llm):
    _seed_settings(
        gen_db,
        {
            "llm_provider": "local",
            "llm_local_model": "llama",
            "llm_local_base_url": "http://localhost:1234/v1",
            "local_timezone": "UTC",
        },
    )

    gen = SummaryGenerator()

    assert gen.provider == "local"
    assert gen.model == "llama"
    assert gen.stem_concept_enabled is False  # default when unset
    assert gen.shopping_list_in_summary_enabled is False  # default when unset
    # On by default: the section omits itself on days with nothing coming up.
    assert gen.packing_lists_in_summary_enabled is True


# --- Anthropic max_tokens resolution ---------------------------------------------


class _FakeModels:
    def __init__(self, result=None, exc=None):
        self.result = result
        self.exc = exc
        self.calls = []

    def retrieve(self, model_id):
        self.calls.append(model_id)
        if self.exc is not None:
            raise self.exc
        return self.result


def _anthropic_generator(models: _FakeModels, model: str = "claude-x") -> SummaryGenerator:
    gen = SummaryGenerator.__new__(SummaryGenerator)
    gen.provider = "anthropic"
    gen.model = model
    gen.client = SimpleNamespace(models=models)
    return gen


def test_init_ignores_leftover_max_tokens_settings(gen_db, mock_llm):
    """Rows for the removed max-tokens keys (before migration 038 runs) are
    simply ignored — no budget is read from them, and nothing is resolved at
    construction time."""
    _seed_settings(
        gen_db,
        {
            "llm_provider": "anthropic",
            "llm_anthropic_model": "claude-x",
            "llm_anthropic_api_key": "sk-test",
            "llm_anthropic_max_tokens": "4000",
            "llm_anthropic_max_tokens_mode": "custom",
            "llm_local_max_tokens": "4000",
        },
    )

    gen = SummaryGenerator()

    assert not hasattr(gen, "max_tokens")
    assert not [c for c in mock_llm.calls if c[0] == "models.retrieve"]


def test_anthropic_max_tokens_resolves_from_models_api():
    models = _FakeModels(result=SimpleNamespace(id="claude-x", max_tokens=128000))
    gen = _anthropic_generator(models)

    assert gen._anthropic_max_tokens() == 128000
    assert models.calls == ["claude-x"]


def test_anthropic_max_tokens_cached_per_model():
    """Looked up once per model per process, and a different model gets its own."""
    models = _FakeModels(result=SimpleNamespace(max_tokens=64000))
    gen = _anthropic_generator(models)
    gen._anthropic_max_tokens()
    gen._anthropic_max_tokens()
    assert models.calls == ["claude-x"]

    # A model change never reuses the previous model's number.
    other = _FakeModels(result=SimpleNamespace(max_tokens=128000))
    gen2 = _anthropic_generator(other, model="claude-y")
    assert gen2._anthropic_max_tokens() == 128000
    assert other.calls == ["claude-y"]


def test_anthropic_max_tokens_lookup_failure_raises_clear_error():
    class NotFoundError(Exception):
        status_code = 404

    gen = _anthropic_generator(_FakeModels(exc=NotFoundError("model not found")))

    with pytest.raises(LLMModelLimitError) as excinfo:
        gen._anthropic_max_tokens()
    assert "claude-x" in str(excinfo.value)
    assert "model not found" in str(excinfo.value)
    assert generate_module._ANTHROPIC_MODEL_MAX_TOKENS == {}


def test_anthropic_max_tokens_transient_error_reraised_for_retry():
    """A network blip is left as-is so _call_llm's retry budget covers it."""

    class APIConnectionError(Exception):
        pass

    gen = _anthropic_generator(_FakeModels(exc=APIConnectionError("down")))

    with pytest.raises(APIConnectionError):
        gen._anthropic_max_tokens()


@pytest.mark.parametrize("bad", [None, 0, -1, "128000"])
def test_anthropic_max_tokens_rejects_unusable_value(bad):
    gen = _anthropic_generator(_FakeModels(result=SimpleNamespace(max_tokens=bad)))

    with pytest.raises(LLMModelLimitError):
        gen._anthropic_max_tokens()


# --- load_meal_plans ---------------------------------------------------------


def test_load_meal_plans_next_7_days(gen_db, frozen_now):
    frozen_now(datetime(2026, 5, 10, 12, tzinfo=UTC))
    gen_db.add(MealPlan(date="2026-05-10", meal_type="Dinner", plan="Tacos"))
    gen_db.add(MealPlan(date="2026-05-11", meal_type="Dinner", plan="Pizza"))
    gen_db.add(MealPlan(date="2026-05-20", meal_type="Dinner", plan="TooFar"))  # beyond 7 days
    gen_db.commit()

    out = make_generator().load_meal_plans()

    assert "Today (Dinner): Tacos" in out
    assert "Tomorrow (Dinner): Pizza" in out
    assert "TooFar" not in out


def test_load_meal_plans_empty(gen_db, frozen_now):
    frozen_now(datetime(2026, 5, 10, 12, tzinfo=UTC))
    assert make_generator().load_meal_plans() == "No meal plans for the next 7 days."


def test_load_meal_plans_annotates_attendees_and_cook(gen_db, frozen_now):
    frozen_now(datetime(2026, 5, 10, 12, tzinfo=UTC))
    dad = FamilyMember(name="Dad")
    mom = FamilyMember(name="Mom")
    gen_db.add_all([dad, mom])
    gen_db.flush()
    gen_db.add(
        MealPlan(
            date="2026-05-12",
            meal_type="Dinner",
            plan="Soup",
            attendee_ids=[dad.id, mom.id],
            cook_id=dad.id,
        )
    )
    gen_db.commit()

    out = make_generator().load_meal_plans()

    assert "Eating: Dad, Mom" in out
    assert "Cook: Dad" in out


def test_load_meal_plans_annotates_notes(gen_db, frozen_now):
    frozen_now(datetime(2026, 5, 10, 12, tzinfo=UTC))
    gen_db.add(
        MealPlan(date="2026-05-10", meal_type="Dinner", plan="Brinner", notes="Sausages, eggs")
    )
    gen_db.commit()

    out = make_generator().load_meal_plans()

    assert "Today (Dinner): Brinner [Notes: Sausages, eggs]" in out


def test_load_meal_plans_omits_notes_when_absent(gen_db, frozen_now):
    frozen_now(datetime(2026, 5, 10, 12, tzinfo=UTC))
    gen_db.add(MealPlan(date="2026-05-10", meal_type="Dinner", plan="Tacos"))
    gen_db.commit()

    out = make_generator().load_meal_plans()

    assert "Notes" not in out


def test_load_meal_plans_flattens_multiline_notes(gen_db, frozen_now):
    frozen_now(datetime(2026, 5, 10, 12, tzinfo=UTC))
    gen_db.add(
        MealPlan(
            date="2026-05-10", meal_type="Dinner", plan="Brinner", notes="Sausages\neggs\npancakes"
        )
    )
    gen_db.commit()

    out = make_generator().load_meal_plans()

    assert "[Notes: Sausages eggs pancakes]" in out


# --- _load_ai_setting ----------------------------------------------------------


def test_load_ai_setting_via_history_pointer(gen_db):
    row = AISettingsHistory(field_name="agent_voice", value="Cheerful and brief")
    gen_db.add(row)
    gen_db.commit()

    gen = make_generator()
    gen._db_settings = {"current_agent_voice_history_id": str(row.id)}

    assert gen._load_ai_setting("agent_voice") == "Cheerful and brief"


def test_load_ai_setting_falls_back_to_direct_value(gen_db):
    gen = make_generator()
    gen._db_settings = {"family_context": "We are the Smiths"}
    assert gen._load_ai_setting("family_context") == "We are the Smiths"


def test_load_ai_setting_none_when_absent(gen_db):
    gen = make_generator()
    gen._db_settings = {}
    assert gen._load_ai_setting("agent_voice") is None


# --- load_todos formatting -----------------------------------------------------


def test_load_todos_formats_assignee_due_and_description_dates(gen_db, frozen_now):
    frozen_now(datetime(2026, 3, 1, 12, tzinfo=UTC))
    dad = FamilyMember(name="Dad")
    gen_db.add(dad)
    gen_db.flush()
    gen_db.add(
        Todo(
            title="Field trip",
            completed=False,
            assigned_to=dad.id,
            due_date="2026-03-05",
            description="Permission slip due 2026-03-04",
        )
    )
    gen_db.commit()

    out = make_generator().load_todos()

    assert "[Assigned to Dad]" in out
    assert "[Due Thursday, Mar 05]" in out
    assert "2026-03-04 (Wednesday)" in out  # date in the description is annotated


def test_load_todos_includes_todo_with_unparseable_due_date(gen_db, frozen_now):
    frozen_now(datetime(2026, 3, 1, 12, tzinfo=UTC))
    gen_db.add(Todo(title="Weird", completed=False, due_date="not-a-date", remind_days_before=1))
    gen_db.commit()

    out = make_generator().load_todos()

    assert "Weird" in out
    assert "[Due not-a-date]" in out


# --- load_shopping_items -------------------------------------------------------


def test_load_shopping_items_groups_by_store_with_anywhere_last(gen_db):
    costco = ShoppingStore(name="Costco")
    aldi = ShoppingStore(name="Aldi")
    gen_db.add_all([costco, aldi])
    gen_db.flush()
    gen_db.add_all(
        [
            ShoppingItem(name="Paper towels", store_id=costco.id),
            ShoppingItem(name="Rotisserie chicken", note="2", store_id=costco.id),
            ShoppingItem(name="Bread", store_id=aldi.id),
            ShoppingItem(name="Stamps"),
        ]
    )
    gen_db.commit()

    out = make_generator().load_shopping_items()

    assert out.index("Aldi:") < out.index("Costco:") < out.index("Anywhere:")
    assert "  - Rotisserie chicken - 2" in out
    assert "  - Stamps" in out


def test_load_shopping_items_excludes_completed_items(gen_db):
    gen_db.add_all(
        [
            ShoppingItem(name="Milk", completed=True, completed_at=datetime(2026, 3, 1, 12)),
            ShoppingItem(name="Eggs"),
        ]
    )
    gen_db.commit()

    out = make_generator().load_shopping_items()

    assert "Eggs" in out
    assert "Milk" not in out


def test_load_shopping_items_empty(gen_db):
    assert make_generator().load_shopping_items() == "No shopping items currently active."


def test_load_shopping_items_falls_back_when_store_was_deleted(gen_db):
    """An item pointing at a since-deleted store must still be listed."""
    gen_db.add(ShoppingItem(name="Orphan", store_id=999))
    gen_db.commit()

    out = make_generator().load_shopping_items()

    assert out == "Anywhere:\n  - Orphan"


# --- load_context / load_voice -------------------------------------------------


def test_load_context_uses_ai_setting(gen_db):
    gen = make_generator()
    gen._db_settings = {"family_context": "We are the Smiths"}
    assert gen.load_context() == "We are the Smiths"


def test_load_voice_uses_ai_setting(gen_db):
    gen = make_generator()
    gen._db_settings = {"agent_voice": "Warm and concise"}
    assert gen.load_voice() == "Warm and concise"


# --- fetch_weather -------------------------------------------------------------


def test_fetch_weather_returns_body(mock_requests):
    gen = make_generator()
    gen._db_settings = {"weather_nws_url": "https://forecast.example/nws"}
    mock_requests.set_response(text="<dwml>ok</dwml>", status_code=200)

    assert gen.fetch_weather() == "<dwml>ok</dwml>"
    assert mock_requests.calls[0]["url"] == "https://forecast.example/nws"


def test_fetch_weather_no_url_returns_none():
    gen = make_generator()
    gen._db_settings = {}
    assert gen.fetch_weather() is None


def test_fetch_weather_request_error_returns_none(mock_requests):
    gen = make_generator()
    gen._db_settings = {"weather_nws_url": "https://forecast.example/nws"}
    mock_requests.set_response(status_code=500)
    assert gen.fetch_weather() is None


# --- fetch_calendars / _fetch_ics_calendar -------------------------------------


def _seed_calendar(session, cal_type="ics", **overrides):
    dad = FamilyMember(name="Dad")
    session.add(dad)
    session.flush()
    fields = {
        "label": "Fam",
        "url": "https://cal.example/c.ics",
        "family_member_id": dad.id,
        "cal_type": cal_type,
    }
    fields.update(overrides)
    session.add(Calendar(**fields))
    session.commit()


def test_fetch_calendars_ics(gen_db, frozen_now, mock_requests):
    frozen_now(datetime(2026, 3, 15, 12, tzinfo=UTC))
    _seed_calendar(gen_db, cal_type="ics")
    mock_requests.set_response(text=_ics("Soccer"), status_code=200)

    occurrences = make_generator().fetch_calendars()

    assert len(occurrences) == 1
    assert occurrences[0].member == "Dad"
    assert occurrences[0].title == "Soccer"


def test_fetch_calendars_ics_skips_declined(gen_db, frozen_now, mock_requests):
    frozen_now(datetime(2026, 3, 15, 12, tzinfo=UTC))
    _seed_calendar(gen_db, cal_type="ics")
    mock_requests.set_response(text=_ics("Dead", extra="STATUS:CANCELLED\r\n"), status_code=200)

    # The only event is cancelled -> no events -> calendar dropped.
    assert make_generator().fetch_calendars() == []


def test_fetch_calendars_skips_calendar_on_fetch_error(gen_db, frozen_now, mock_requests):
    frozen_now(datetime(2026, 3, 15, 12, tzinfo=UTC))
    _seed_calendar(gen_db, cal_type="ics")
    mock_requests.set_response(status_code=500)

    assert make_generator().fetch_calendars() == []


def test_fetch_calendars_caldav_google(gen_db, frozen_now, mock_caldav):
    frozen_now(datetime(2026, 3, 15, 12, tzinfo=UTC))
    _seed_calendar(gen_db, cal_type="caldav_google", url="https://dav", username="u", password="p")
    mock_caldav.set_events([SimpleNamespace(data=_ics("Standup", dtstart="20260316T090000Z"))])

    occurrences = make_generator().fetch_calendars()

    assert [o.title for o in occurrences] == ["Standup"]


def test_fetch_calendars_caldav_apple(gen_db, frozen_now, mock_caldav):
    frozen_now(datetime(2026, 3, 15, 12, tzinfo=UTC))
    _seed_calendar(gen_db, cal_type="caldav_apple", url="https://dav", username="u", password="p")
    mock_caldav.set_events([SimpleNamespace(data=_ics("Recital", dtstart="20260317T180000Z"))])

    occurrences = make_generator().fetch_calendars()

    assert [o.title for o in occurrences] == ["Recital"]


def test_fetch_calendars_empty_returns_empty(gen_db):
    assert make_generator().fetch_calendars() == []


def test_fetch_calendars_config_fallback(gen_db, frozen_now, mock_requests):
    # No DB calendars -> fall back to config.toml's [calendars] table.
    frozen_now(datetime(2026, 3, 15, 12, tzinfo=UTC))
    mock_requests.set_response(text=_ics("Cleanup"), status_code=200)
    gen = make_generator()
    gen.config = {"calendars": {"Family": "https://cal.example/c.ics"}}

    occurrences = gen.fetch_calendars()

    assert len(occurrences) == 1
    assert occurrences[0].calendar_label == "Family"
    assert occurrences[0].title == "Cleanup"
