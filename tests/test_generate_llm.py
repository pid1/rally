"""Tests for the LLM-calling paths in rally.generator.generate.

Every test uses a stubbed client (or a stubbed SummaryGenerator) — no real
network or model call happens, so no API credits are consumed. The focus is the
provider branching in _call_llm and the prompt-assembly / JSON-parsing logic in
generate_summary and evaluate_summary.
"""

from datetime import UTC, datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from rally.generator.generate import LLM_MAX_TOKENS, LLMTruncatedError, SummaryGenerator


def make_generator(tz: str = "UTC") -> SummaryGenerator:
    gen = SummaryGenerator.__new__(SummaryGenerator)
    gen.local_tz = ZoneInfo(tz)
    gen.local_tz_name = tz
    gen._db_settings = {}
    gen.config = {}
    gen.calendar_owners = {}
    gen.stem_concept_enabled = False
    gen.shopping_list_in_summary_enabled = False
    gen.sports_watchlist_enabled = False
    return gen


class FakeAnthropic:
    """Stands in for anthropic.Anthropic — records the create() kwargs."""

    def __init__(self, text, blocks=None, stop_reason="end_turn", usage=None):
        self._text = text
        self._blocks = blocks
        self._stop_reason = stop_reason
        self._usage = usage
        self.messages = self
        self.last_kwargs = None

    def create(self, **kwargs):
        self.last_kwargs = kwargs
        content = self._blocks or [SimpleNamespace(type="text", text=self._text)]
        return SimpleNamespace(content=content, stop_reason=self._stop_reason, usage=self._usage)


class FakeOpenAI:
    """Stands in for openai.OpenAI — records the create() kwargs."""

    def __init__(self, text, choices=None, finish_reason="stop", usage=None):
        self._text = text
        self._choices = choices
        self._finish_reason = finish_reason
        self._usage = usage
        self.chat = SimpleNamespace(completions=self)
        self.last_kwargs = None

    def create(self, **kwargs):
        self.last_kwargs = kwargs
        if self._choices is not None:
            return SimpleNamespace(choices=self._choices, usage=self._usage)
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=self._text),
                    finish_reason=self._finish_reason,
                )
            ],
            usage=self._usage,
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


# --- truncation detection ------------------------------------------------------


def test_call_llm_anthropic_raises_on_max_tokens_stop_reason():
    gen = make_generator()
    gen.provider = "anthropic"
    gen.model = "claude-x"
    gen.client = FakeAnthropic(
        '{"greeting": "cut off mid-str',
        stop_reason="max_tokens",
        usage=SimpleNamespace(input_tokens=1200, output_tokens=LLM_MAX_TOKENS),
    )

    with pytest.raises(LLMTruncatedError) as excinfo:
        gen._call_llm("hi")

    message = str(excinfo.value)
    assert "stop_reason=max_tokens" in message
    assert f"output_tokens={LLM_MAX_TOKENS}" in message
    assert f"max_tokens={LLM_MAX_TOKENS}" in message


def test_call_llm_local_raises_on_length_finish_reason():
    gen = make_generator()
    gen.provider = "local"
    gen.model = "llama"
    gen.client = FakeOpenAI(
        "partial",
        finish_reason="length",
        usage=SimpleNamespace(prompt_tokens=900, completion_tokens=LLM_MAX_TOKENS),
    )

    with pytest.raises(LLMTruncatedError) as excinfo:
        gen._call_llm("hi")

    assert "finish_reason=length" in str(excinfo.value)


@pytest.mark.parametrize("stop_reason", ["end_turn", "stop_sequence", "refusal", None])
def test_call_llm_anthropic_other_stop_reasons_do_not_raise(stop_reason):
    """Only budget exhaustion is a truncation; everything else parses as before."""
    gen = make_generator()
    gen.provider = "anthropic"
    gen.model = "claude-x"
    gen.client = FakeAnthropic("body", stop_reason=stop_reason)

    assert gen._call_llm("hi") == "body"


def test_call_llm_local_other_finish_reasons_do_not_raise():
    gen = make_generator()
    gen.provider = "local"
    gen.model = "llama"
    gen.client = FakeOpenAI("body", finish_reason="stop")

    assert gen._call_llm("hi") == "body"


def test_call_llm_logs_stop_reason_and_usage_on_success(capsys):
    """Logged on success too — budget headroom must be visible before an outage."""
    gen = make_generator()
    gen.provider = "anthropic"
    gen.model = "claude-x"
    gen.client = FakeAnthropic(
        "ok",
        usage=SimpleNamespace(
            input_tokens=1500,
            output_tokens=820,
            cache_read_input_tokens=1400,
        ),
    )

    gen._call_llm("hi", label="summary")

    out = capsys.readouterr().out
    assert "[summary]" in out
    assert "stop_reason=end_turn" in out
    assert "input=1500" in out
    assert "output=820" in out
    assert "cache_read=1400" in out


def test_call_llm_logs_reasoning_tokens_when_reported(capsys):
    gen = make_generator()
    gen.provider = "local"
    gen.model = "llama"
    gen.client = FakeOpenAI(
        "ok",
        usage=SimpleNamespace(
            prompt_tokens=100,
            completion_tokens=900,
            completion_tokens_details=SimpleNamespace(reasoning_tokens=700),
        ),
    )

    gen._call_llm("hi", label="eval")

    out = capsys.readouterr().out
    assert "[eval]" in out
    assert "reasoning=700" in out


def test_call_llm_logs_when_provider_reports_no_usage(capsys):
    """Stubs and older servers omit usage entirely — logging must still work."""
    gen = make_generator()
    gen.provider = "anthropic"
    gen.model = "claude-x"
    gen.client = FakeAnthropic("ok")

    gen._call_llm("hi")

    assert "usage=unavailable" in capsys.readouterr().out


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


def test_generate_summary_truncation_is_not_reported_as_a_json_error(frozen_now, capsys):
    """The regression this guards: a cut-off response used to surface as
    'Unable to parse JSON ... line 1 column 1 (char 0)', pointing reviewers at
    the JSON instead of at the token budget."""
    frozen_now(FROZEN)
    gen = _summary_gen('{"greeting": "Happy Monday! Let')
    gen.client = FakeAnthropic(
        '{"greeting": "Happy Monday! Let',
        stop_reason="max_tokens",
        usage=SimpleNamespace(input_tokens=2000, output_tokens=LLM_MAX_TOKENS),
    )

    data = gen.generate_summary()

    assert "Unable to generate" in data["greeting"]
    assert data["weather_summary"].startswith("Truncation Error:")
    assert "stop_reason=max_tokens" in data["weather_summary"]
    assert "JSON Error" not in data["weather_summary"]

    out = capsys.readouterr().out
    assert "LLM truncation error:" in out
    assert "Unable to parse JSON" not in out


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


def _shopping_prompts(gen):
    """(system_prompt, user_prompt) from the last recorded LLM call."""
    return gen.client.last_kwargs["system"][0]["text"], gen.client.last_kwargs["messages"][0][
        "content"
    ]


def test_generate_summary_omits_shopping_section_when_disabled(frozen_now):
    frozen_now(FROZEN)
    gen = _summary_gen('{"greeting":"Hi","weather_summary":"","schedule":[],"briefing":""}')
    gen.load_shopping_items = lambda: "Costco:\n  - Paper towels"

    gen.generate_summary()

    system_prompt, user_prompt = _shopping_prompts(gen)
    assert "SHOPPING LIST" not in user_prompt
    assert "Paper towels" not in user_prompt
    assert "SHOPPING LIST FILTERING" not in system_prompt


def test_generate_summary_includes_shopping_section_when_enabled(frozen_now):
    frozen_now(FROZEN)
    gen = _summary_gen('{"greeting":"Hi","weather_summary":"","schedule":[],"briefing":""}')
    gen.shopping_list_in_summary_enabled = True
    gen.load_shopping_items = lambda: "Costco:\n  - Paper towels\nAnywhere:\n  - Stamps"

    gen.generate_summary()

    system_prompt, user_prompt = _shopping_prompts(gen)
    assert "SHOPPING LIST (open items):" in user_prompt
    assert "Costco" in user_prompt
    assert "Paper towels" in user_prompt
    assert "11. SHOPPING LIST FILTERING" in system_prompt


def test_generate_summary_shopping_empty_list_phrasing(frozen_now):
    frozen_now(FROZEN)
    gen = _summary_gen('{"greeting":"Hi","weather_summary":"","schedule":[],"briefing":""}')
    gen.shopping_list_in_summary_enabled = True
    gen.load_shopping_items = lambda: "No shopping items currently active."

    gen.generate_summary()

    _, user_prompt = _shopping_prompts(gen)
    assert "No shopping items currently active." in user_prompt


def test_optional_guidelines_are_numbered_sequentially(frozen_now):
    """With both toggles on the appended guidelines must run 11 then 12 — no
    collision, no gap, whichever combination of features is enabled."""
    frozen_now(FROZEN)
    gen = _summary_gen('{"greeting":"Hi","weather_summary":"","schedule":[],"briefing":""}')
    gen.stem_concept_enabled = True
    gen.shopping_list_in_summary_enabled = True
    gen.load_recent_stem_concepts = lambda: []
    gen.load_shopping_items = lambda: "Anywhere:\n  - Stamps"

    gen.generate_summary()

    system_prompt, _ = _shopping_prompts(gen)
    assert "11. STEM CONCEPT OF THE DAY" in system_prompt
    assert "12. SHOPPING LIST FILTERING" in system_prompt
    assert "13." not in system_prompt


def test_shopping_guideline_is_11_when_stem_is_off(frozen_now):
    frozen_now(FROZEN)
    gen = _summary_gen('{"greeting":"Hi","weather_summary":"","schedule":[],"briefing":""}')
    gen.shopping_list_in_summary_enabled = True
    gen.load_shopping_items = lambda: "Anywhere:\n  - Stamps"

    gen.generate_summary()

    system_prompt, _ = _shopping_prompts(gen)
    assert "11. SHOPPING LIST FILTERING" in system_prompt
    assert "12." not in system_prompt


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


def test_evaluate_summary_omits_shopping_ground_truth_when_absent():
    gen = _eval_gen('{"overall_score":4.0,"pass":true}')
    gen.evaluate_summary({"greeting": "Hi"})
    assert "SHOPPING LIST" not in gen.client.last_kwargs["messages"][0]["content"]


def test_evaluate_summary_includes_shopping_ground_truth():
    gen = _eval_gen('{"overall_score":4.0,"pass":true}')
    gen._generation_context["shopping_items"] = "Costco:\n  - Paper towels"
    gen.evaluate_summary({"greeting": "Hi"})

    eval_user = gen.client.last_kwargs["messages"][0]["content"]
    assert "SHOPPING LIST:" in eval_user
    assert "Paper towels" in eval_user


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


# --- Sports watchlist ----------------------------------------------------------


def _sports_gen(response_text='{"greeting":"Hi","weather_summary":"","schedule":[],"briefing":""}'):
    gen = _summary_gen(response_text)
    gen.sports_watchlist_enabled = True
    gen.load_sports_watchlist = lambda: (
        "Tonight:\n- 7:05 PM — Astros at Rangers\n    TV: Peacock\n    Radio: 105.3 The Fan"
    )
    return gen


def test_generate_summary_omits_sports_section_when_disabled(frozen_now):
    frozen_now(FROZEN)
    gen = _summary_gen('{"greeting":"Hi","weather_summary":"","schedule":[],"briefing":""}')
    gen.load_sports_watchlist = lambda: "Tonight:\n- 7:05 PM — Astros at Rangers"

    gen.generate_summary()

    system_prompt, user_prompt = _shopping_prompts(gen)
    assert "SPORTS" not in user_prompt
    assert "SPORTS WATCHLIST" not in system_prompt


def test_generate_summary_includes_sports_section_when_enabled(frozen_now):
    frozen_now(FROZEN)
    gen = _sports_gen()

    gen.generate_summary()

    system_prompt, user_prompt = _shopping_prompts(gen)
    assert "SPORTS (followed teams — TV and radio):" in user_prompt
    assert "Astros at Rangers" in user_prompt
    assert "TV: Peacock" in user_prompt
    assert "11. SPORTS WATCHLIST" in system_prompt


def test_sports_guideline_forbids_inventing_games_and_scores(frozen_now):
    """Sports schedules are exactly the content the model has strong, confident,
    wrong priors about."""
    frozen_now(FROZEN)
    gen = _sports_gen()

    gen.generate_summary()

    system_prompt, _ = _shopping_prompts(gen)
    assert "ONLY source of truth" in system_prompt
    assert "VERBATIM" in system_prompt
    assert "never guess a network" in system_prompt
    assert "Never state or imply the outcome of any game" in system_prompt


def test_empty_sports_section_adds_neither_prompt_nor_guideline(frozen_now):
    """An outage returns an empty string; an empty section would burn tokens and
    invite the model to reference a list that is not there."""
    frozen_now(FROZEN)
    gen = _summary_gen('{"greeting":"Hi","weather_summary":"","schedule":[],"briefing":""}')
    gen.sports_watchlist_enabled = True
    gen.load_sports_watchlist = lambda: ""

    gen.generate_summary()

    system_prompt, user_prompt = _shopping_prompts(gen)
    assert "SPORTS (followed teams" not in user_prompt
    assert "SPORTS WATCHLIST" not in system_prompt


def test_all_three_optional_guidelines_are_numbered_without_collision(frozen_now):
    frozen_now(FROZEN)
    gen = _sports_gen()
    gen.stem_concept_enabled = True
    gen.shopping_list_in_summary_enabled = True
    gen.load_recent_stem_concepts = lambda: []
    gen.load_shopping_items = lambda: "Anywhere:\n  - Stamps"

    gen.generate_summary()

    system_prompt, _ = _shopping_prompts(gen)
    assert "11. STEM CONCEPT OF THE DAY" in system_prompt
    assert "12. SHOPPING LIST FILTERING" in system_prompt
    assert "13. SPORTS WATCHLIST" in system_prompt
    assert "14." not in system_prompt


def test_load_sports_watchlist_swallows_a_failure_and_returns_empty(monkeypatch):
    """A third-party outage must degrade to a missing section, never an
    exception that reaches generate_summary."""
    import rally.sports.watchlist as watchlist_module

    def explode(*args, **kwargs):
        raise RuntimeError("provider down")

    monkeypatch.setattr(watchlist_module, "load_followed_teams", explode)

    gen = make_generator()
    gen.sports_watchlist_enabled = True

    assert SummaryGenerator.load_sports_watchlist(gen) == ""
    assert gen._pending_sports_notices == []


def test_a_sports_failure_still_produces_a_summary(frozen_now, monkeypatch):
    frozen_now(FROZEN)
    import rally.sports.watchlist as watchlist_module

    def explode(*args, **kwargs):
        raise RuntimeError("provider down")

    monkeypatch.setattr(watchlist_module, "load_followed_teams", explode)

    gen = _summary_gen('{"greeting":"Hi","weather_summary":"","schedule":[],"briefing":""}')
    gen.sports_watchlist_enabled = True

    data = gen.generate_summary()

    assert data["greeting"] == "Hi"
    assert "error" not in data
