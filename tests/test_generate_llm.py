"""Tests for the LLM-calling paths in rally.generator.generate.

Every test uses a stubbed client (or a stubbed SummaryGenerator) — no real
network or model call happens, so no API credits are consumed. The focus is the
provider branching in _call_llm and the prompt-assembly / JSON-parsing logic in
generate_summary and evaluate_summary.
"""

from datetime import UTC, datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from rally.generator.generate import SummaryGenerator


def make_generator(tz: str = "UTC") -> SummaryGenerator:
    gen = SummaryGenerator.__new__(SummaryGenerator)
    gen.local_tz = ZoneInfo(tz)
    gen.local_tz_name = tz
    gen._db_settings = {}
    gen.config = {}
    gen.calendar_owners = {}
    gen.stem_concept_enabled = False
    return gen


class FakeAnthropic:
    """Stands in for anthropic.Anthropic — records the create() kwargs."""

    def __init__(self, text, blocks=None):
        self._text = text
        self._blocks = blocks
        self.messages = self
        self.last_kwargs = None

    def create(self, **kwargs):
        self.last_kwargs = kwargs
        content = self._blocks or [SimpleNamespace(type="text", text=self._text)]
        return SimpleNamespace(content=content)


class FakeOpenAI:
    """Stands in for openai.OpenAI — records the create() kwargs."""

    def __init__(self, text, choices=None):
        self._text = text
        self._choices = choices
        self.chat = SimpleNamespace(completions=self)
        self.last_kwargs = None

    def create(self, **kwargs):
        self.last_kwargs = kwargs
        if self._choices is not None:
            return SimpleNamespace(choices=self._choices)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=self._text))]
        )


# --- _call_llm -----------------------------------------------------------------


def test_call_llm_anthropic_sends_system_block():
    gen = make_generator()
    gen.provider = "anthropic"
    gen.model = "claude-x"
    gen.client = FakeAnthropic("hello")

    out = gen._call_llm("user text", system_prompt="system text")

    assert out == "hello"
    assert gen.client.last_kwargs["model"] == "claude-x"
    assert gen.client.last_kwargs["messages"] == [{"role": "user", "content": "user text"}]
    assert gen.client.last_kwargs["system"][0]["text"] == "system text"


def test_call_llm_anthropic_filters_non_text_blocks():
    gen = make_generator()
    gen.provider = "anthropic"
    gen.model = "claude-x"
    gen.client = FakeAnthropic(
        "",
        blocks=[
            SimpleNamespace(type="thinking", text="ignored"),
            SimpleNamespace(type="text", text="kept"),
        ],
    )

    assert gen._call_llm("hi") == "kept"


def test_call_llm_local_builds_system_and_user_messages():
    gen = make_generator()
    gen.provider = "local"
    gen.model = "llama"
    gen.client = FakeOpenAI("world")

    out = gen._call_llm("user text", system_prompt="system text")

    assert out == "world"
    assert gen.client.last_kwargs["messages"] == [
        {"role": "system", "content": "system text"},
        {"role": "user", "content": "user text"},
    ]


def test_call_llm_local_without_system_prompt():
    gen = make_generator()
    gen.provider = "local"
    gen.model = "llama"
    gen.client = FakeOpenAI("x")

    gen._call_llm("only user")

    assert gen.client.last_kwargs["messages"] == [{"role": "user", "content": "only user"}]


def test_call_llm_local_empty_choices_returns_empty_string():
    gen = make_generator()
    gen.provider = "local"
    gen.model = "llama"
    gen.client = FakeOpenAI("", choices=[])

    assert gen._call_llm("hi") == ""


# --- generate_summary ----------------------------------------------------------

FROZEN = datetime(2026, 3, 15, 9, 0, tzinfo=UTC)


def _summary_gen(response_text, *, provider="anthropic"):
    gen = make_generator()
    gen.provider = provider
    gen.model = "m"
    gen._db_settings = {"family_context": "ctx", "agent_voice": "voice"}
    gen.client = (
        FakeAnthropic(response_text) if provider == "anthropic" else FakeOpenAI(response_text)
    )
    # Stub the data loaders (covered in Phase 8) so this focuses on assembly/parsing.
    gen.fetch_calendars = lambda: []
    gen.fetch_weather = lambda: None
    gen.load_family_members = lambda: {}
    gen.load_todos = lambda: "No todos currently active."
    gen.load_dinner_plans = lambda: "No meal plans for the next 7 days."
    return gen


def test_generate_summary_parses_strict_json(frozen_now):
    frozen_now(FROZEN)
    gen = _summary_gen('{"greeting":"Hi","weather_summary":"Sunny","schedule":[],"briefing":""}')

    data = gen.generate_summary()

    assert data["greeting"] == "Hi"
    assert data["weather_summary"] == "Sunny"


def test_generate_summary_extracts_fenced_json(frozen_now):
    frozen_now(FROZEN)
    gen = _summary_gen(
        '```json\n{"greeting":"Hey","weather_summary":"","schedule":[],"briefing":""}\n```'
    )

    data = gen.generate_summary()

    assert data["greeting"] == "Hey"


def test_generate_summary_unparseable_returns_error_dict(frozen_now):
    frozen_now(FROZEN)
    gen = _summary_gen("this is not json at all")

    data = gen.generate_summary()

    assert "Unable to generate" in data["greeting"]
    assert data["schedule"] == []


def test_generate_summary_local_provider(frozen_now):
    frozen_now(FROZEN)
    gen = _summary_gen(
        '{"greeting":"Yo","weather_summary":"","schedule":[],"briefing":""}', provider="local"
    )

    data = gen.generate_summary()

    assert data["greeting"] == "Yo"


def test_generate_summary_formats_calendar_events_into_prompt(frozen_now):
    frozen_now(FROZEN)
    gen = _summary_gen('{"greeting":"Hi","weather_summary":"","schedule":[],"briefing":""}')
    gen.fetch_calendars = lambda: [
        {
            "name": "Dad Cal",
            "member": "Dad",
            "events": [
                {
                    "date": "2026-03-15",
                    "time": "10:00 AM",
                    "summary": "Soccer",
                    "location": "Field",
                    "description": "bring cleats",
                }
            ],
        }
    ]

    gen.generate_summary()

    user_prompt = gen.client.last_kwargs["messages"][0]["content"]
    assert "Dad Cal" in user_prompt
    assert "Soccer" in user_prompt
    assert "at Field" in user_prompt


def test_generate_summary_dedupes_and_annotates_shared_events(frozen_now):
    frozen_now(FROZEN)
    gen = _summary_gen('{"greeting":"Hi","weather_summary":"","schedule":[],"briefing":""}')
    shared = {
        "date": "2026-03-15",
        "time": "10:00 AM",
        "summary": "Recital",
        "location": "",
        "description": "",
    }
    gen.fetch_calendars = lambda: [
        {"name": "Mom Cal", "member": "Mom", "events": [dict(shared)]},
        {"name": "Dad Cal", "member": "Dad", "events": [dict(shared)]},
        {"name": "Nameless", "member": None, "events": [dict(shared)]},  # skipped in attendance
    ]

    gen.generate_summary()

    prompt = gen.client.last_kwargs["messages"][0]["content"]
    # The event is emitted once (cross-calendar dedupe) and tagged with all attendees.
    assert prompt.count("Recital") == 1
    assert "[Attending: Mom, Dad]" in prompt


def test_generate_summary_with_stem_enabled(frozen_now):
    frozen_now(FROZEN)
    gen = _summary_gen(
        '{"greeting":"Hi","weather_summary":"","schedule":[],"briefing":"",'
        '"stem_concept":{"title":"Buoyancy"}}'
    )
    gen.stem_concept_enabled = True
    gen.load_recent_stem_concepts = lambda: ["Gravity"]

    data = gen.generate_summary()

    assert data["stem_concept"]["title"] == "Buoyancy"
    # The recent-concepts avoid-list is injected into the prompt.
    assert "Gravity" in gen.client.last_kwargs["messages"][0]["content"]


# --- evaluate_summary ----------------------------------------------------------


def _eval_gen(response_text):
    gen = make_generator()
    gen.provider = "anthropic"
    gen.model = "m"
    gen.client = FakeAnthropic(response_text)
    gen._generation_context = {
        "cal_text": "c",
        "weather": "w",
        "todos": "t",
        "dinner_plans": "d",
        "family_members": "f",
    }
    return gen


def test_evaluate_summary_without_context_returns_error():
    gen = make_generator()
    out = gen.evaluate_summary({"greeting": "x"})
    assert "No generation context" in out["error"]


def test_evaluate_summary_parses_json():
    gen = _eval_gen('{"overall_score":4.5,"pass":true}')
    out = gen.evaluate_summary({"greeting": "Hi"})
    assert out["overall_score"] == 4.5
    assert out["pass"] is True


def test_evaluate_summary_extracts_fenced_json():
    gen = _eval_gen('```json\n{"overall_score":2.0,"pass":false}\n```')
    out = gen.evaluate_summary({"greeting": "Hi"})
    assert out["pass"] is False


def test_evaluate_summary_unparseable_returns_error():
    gen = _eval_gen("not json")
    out = gen.evaluate_summary({"greeting": "Hi"})
    assert out["error"] == "Failed to parse eval response"
    assert out["raw"] == "not json"


# --- GET /api/dashboard/regenerate ---------------------------------------------


def test_regenerate_endpoint_wires_generator(client, monkeypatch):
    saved = {}

    class FakeGen:
        def generate_summary(self):
            return {"greeting": "Regenerated"}

        def save_snapshot(self, data):
            saved["data"] = data

    monkeypatch.setattr("rally.routers.dashboard.SummaryGenerator", lambda: FakeGen())

    resp = client.get("/api/dashboard/regenerate")

    assert resp.status_code == 200
    assert resp.json()["status"] == "success"
    assert saved["data"] == {"greeting": "Regenerated"}
