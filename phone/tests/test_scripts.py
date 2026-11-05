"""setup.py and safety_tests.py: dry run by default, idempotent with --apply."""

import json

import httpx
import pytest
import safety_tests
import setup
from conftest import API_KEY

from rally_phone import config

AGENTS = "/v1/convai/agents"
CREATE = "/v1/convai/agents/create"
VOICE = "Cz0K1kOv9tD8l0b5Qu53"
OWNER = "b45c1524a1f360319535f16b301e5b6f37edf26fd1d59861eda66206ff25cdc9"
ADD_VOICE = f"/v1/voices/add/{OWNER}/{VOICE}"


DICTS = "/v1/pronunciation-dictionaries"
DICT_ID = "dict_1"
RULE = {
    "type": "alias",
    "string_to_replace": "Roemer",
    "alias": "Roamer",
    "case_sensitive": True,
    "word_boundaries": True,
}
DICT_NAME = "Rally phone assistant names"


@pytest.fixture(autouse=True)
def voice_in_account(api):
    api.route("GET", "/v2/voices", {"voices": [{"voice_id": VOICE, "name": "Jon"}]})


@pytest.fixture(autouse=True)
def dictionary_up_to_date(api):
    api.route(
        "GET",
        DICTS,
        {
            "pronunciation_dictionaries": [
                {"id": "other", "name": "Something else", "latest_version_id": "x"},
                {"id": DICT_ID, "name": DICT_NAME, "latest_version_id": "v1"},
            ],
            "has_more": False,
        },
    )
    # ElevenLabs omits nothing here, but returns keys in its own order.
    api.route(
        "GET",
        f"{DICTS}/{DICT_ID}",
        {"id": DICT_ID, "latest_version_id": "v1", "rules": [dict(reversed(RULE.items()))]},
    )


def locators(body):
    return body["conversation_config"]["tts"]["pronunciation_dictionary_locators"]


def test_dry_run_sends_no_writes(api, client, capsys):
    api.route("GET", AGENTS, {"agents": []})
    api.route("GET", "/v1/convai/phone-numbers/phnum_1", {"phone_number": "+15125550123"})
    assert setup.main(["all", "--phone-number-id", "phnum_1"], eleven=client) == 0
    assert api.writes() == []
    out = capsys.readouterr().out
    assert f"DRY RUN: POST https://api.elevenlabs.io{CREATE}" in out
    assert "nothing was sent" in out
    assert not config.CONFIG_PATH.exists()


def test_dry_run_without_a_key_still_shows_the_plan(capsys):
    assert setup.main(["agent"]) == 0
    assert "DRY RUN: POST" in capsys.readouterr().out


def test_apply_creates_then_updates(api, client):
    api.route("GET", AGENTS, {"agents": []})
    api.route("POST", CREATE, {"agent_id": "agent_new"})
    api.route("GET", "/v1/convai/phone-numbers/phnum_1", {"phone_number": "+15125550123"})
    assert setup.main(["all", "--phone-number-id", "phnum_1", "--apply"], eleven=client) == 0
    saved = json.loads(config.CONFIG_PATH.read_text())
    assert saved == {
        "agent_id": "agent_new",
        "phone_number_id": "phnum_1",
        "owner_name": "Jonathan Roemer",
        "assistant_number": "+15125550123",
    }
    # Second run finds the agent by name and PATCHes it.
    api.requests.clear()
    api.route(
        "GET", AGENTS, {"agents": [{"agent_id": "agent_new", "name": "Rally phone assistant"}]}
    )
    api.route("PATCH", "/v1/convai/agents/agent_new", {"agent_id": "agent_new"})
    api.route("GET", "/v1/convai/agents/agent_new", {"conversation_config": {}})
    assert setup.main(["agent", "--apply"], eleven=client) == 0
    assert [(r.method, r.url.path) for r in api.writes()] == [
        ("PATCH", "/v1/convai/agents/agent_new")
    ]


def test_duplicate_agents_stop_setup(api, client):
    dup = {"agent_id": "a", "name": "Rally phone assistant"}
    api.route("GET", AGENTS, {"agents": [dup, dup | {"agent_id": "b"}]})
    with pytest.raises(SystemExit, match="keep one"):
        setup.main(["agent", "--apply"], eleven=client)


def test_import_number_redacts_twilio_secrets(api, client, monkeypatch, capsys):
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "AC-secret-sid")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "tw-secret-token")
    api.route("GET", "/v1/convai/phone-numbers", [])
    assert setup.main(["phone-number", "--number", "+15125550123"], eleven=client) == 0
    out = capsys.readouterr().out
    assert "AC-secret-sid" not in out and "tw-secret-token" not in out
    assert '"sid": "<redacted>"' in out
    assert api.writes() == []


def test_already_imported_number_is_reused(api, client):
    api.route(
        "GET",
        "/v1/convai/phone-numbers",
        [{"phone_number": "+15125550123", "phone_number_id": "phnum_1", "label": "x"}],
    )
    assert setup.main(["phone-number", "--number", "+15125550123", "--apply"], eleven=client) == 0
    assert api.writes() == []
    assert json.loads(config.CONFIG_PATH.read_text())["phone_number_id"] == "phnum_1"


def test_twilio_numbers_is_read_only(api, client, monkeypatch, capsys):
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "ACtest")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "tok")
    seen = []

    def twilio(request):
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "incoming_phone_numbers": [
                    {
                        "phone_number": "+15125550188",
                        "friendly_name": "Spare",
                        "voice_url": "https://demo.twilio.com/welcome/voice/",
                        "capabilities": {"voice": True},
                    }
                ]
            },
        )

    api.route("GET", "/v1/convai/phone-numbers", [])
    s = setup.Setup(eleven=client, apply=False, twilio_transport=httpx.MockTransport(twilio))
    s.twilio_numbers()
    assert [r.method for r in seen] == ["GET"]
    assert seen[0].url.path == "/2010-04-01/Accounts/ACtest/IncomingPhoneNumbers.json"
    assert "answers_via=demo.twilio.com" in capsys.readouterr().out


def test_rally_mcp_hook_refuses_while_disabled(client):
    with pytest.raises(SystemExit, match="V2 hook"):
        setup.main(["agent", "--enable-rally-mcp"], eleven=client)


def test_apply_needs_a_key():
    assert setup.main(["agent", "--apply"]) == 1


def test_safety_tests_dry_run(capsys):
    assert safety_tests.main([]) == 0
    out = capsys.readouterr().out
    assert "nothing was sent" in out


def test_safety_test_bodies():
    bodies = safety_tests.test_bodies()
    llm = [b for b in bodies if b["type"] == "llm"]
    sims = [b for b in bodies if b["type"] == "simulation"]
    assert len(llm) >= 12 and len(sims) >= 2
    ids = {b["name"].removeprefix(safety_tests.PREFIX) for b in llm}
    for needed in (
        "ssn",
        "dob",
        "address",
        "card_payment",
        "ignore_instructions",
        "claims_owner",
        "calendar",
        "money_transfer",
    ):
        assert needed in ids
    for b in llm:
        assert b["chat_history"][0]["role"] == "agent"
        assert "{{" not in b["chat_history"][0]["message"]
        assert b["success_examples"][0]["type"] == "success"
        assert b["failure_examples"][0]["type"] == "failure"


def test_safety_tests_apply_creates_missing_and_runs(api, client):
    config.write_config(
        {"agent_id": "agent_t", "phone_number_id": "p", "owner_name": "O", "assistant_number": "+1"}
    )
    first = safety_tests.test_bodies()[0]["name"]
    api.route("GET", "/v1/convai/agent-testing", {"tests": [{"name": first, "id": "t0"}]})
    api.route("PUT", "/v1/convai/agent-testing/t0", {})
    api.route("POST", "/v1/convai/agent-testing/create", {"id": "tN"})
    api.route("POST", "/v1/convai/agents/agent_t/run-tests", {"id": "inv1"})
    assert safety_tests.main(["--apply"], eleven=client) == 0
    assert len(api.sent("PUT", "/v1/convai/agent-testing/t0")) == 1
    n = len(safety_tests.test_bodies())
    assert len(api.sent("POST", "/v1/convai/agent-testing/create")) == n - 1
    (run,) = api.sent("POST", "/v1/convai/agents/agent_t/run-tests")
    assert run["tests"][0] == {"test_id": "t0"}
    assert all(r.headers["xi-api-key"] == API_KEY for r in api.requests)


def test_default_phone_number_comes_from_agent_json(api, client):
    pn_id = "phnum_5201m6p2gb63fncb57v3z6r7740n"
    api.route("GET", AGENTS, {"agents": []})
    api.route("POST", CREATE, {"agent_id": "agent_new"})
    api.route(
        "GET",
        f"/v1/convai/phone-numbers/{pn_id}",
        {"phone_number": "+15125550199", "provider": "twilio"},
    )
    assert setup.main(["all", "--apply"], eleven=client) == 0
    assert json.loads(config.CONFIG_PATH.read_text())["phone_number_id"] == pn_id
    # Setup never assigns the agent to the number or touches its SMS.
    assert [(r.method, r.url.path) for r in api.writes()] == [("POST", CREATE)]


def test_missing_voice_is_added_from_the_library_on_apply(api, client):
    api.route("GET", "/v2/voices", {"voices": [{"voice_id": "other", "name": "Roger"}]})
    api.route("POST", ADD_VOICE, {"voice_id": "added_voice_id"})
    api.route("GET", AGENTS, {"agents": []})
    api.route("POST", CREATE, {"agent_id": "agent_new"})
    assert setup.main(["agent", "--apply"], eleven=client) == 0
    assert api.sent("POST", ADD_VOICE) == [{"new_name": "Rally assistant - Jon"}]
    (body,) = api.sent("POST", CREATE)
    # The agent uses the id the add call returned.
    assert body["conversation_config"]["tts"]["voice_id"] == "added_voice_id"
    order = [(r.method, r.url.path) for r in api.writes()]
    assert order == [("POST", ADD_VOICE), ("POST", CREATE)]


def test_missing_voice_dry_run_only_shows_the_add(api, client, capsys):
    api.route("GET", "/v2/voices", {"voices": []})
    api.route("GET", AGENTS, {"agents": []})
    assert setup.main(["agent"], eleven=client) == 0
    assert api.writes() == []
    assert f"DRY RUN: POST https://api.elevenlabs.io{ADD_VOICE}" in capsys.readouterr().out


def test_voice_already_added_under_our_name_is_reused(api, client):
    api.route(
        "GET",
        "/v2/voices",
        {
            "voices": [{"voice_id": "renamed_id", "name": "Rally assistant - Jon"}],
            "has_more": False,
        },
    )
    api.route("GET", AGENTS, {"agents": []})
    api.route("POST", CREATE, {"agent_id": "a"})
    assert setup.main(["agent", "--apply"], eleven=client) == 0
    assert api.sent("POST", ADD_VOICE) == []
    assert api.sent("POST", CREATE)[0]["conversation_config"]["tts"]["voice_id"] == "renamed_id"


def test_up_to_date_dictionary_is_only_referenced(api, client):
    api.route("GET", AGENTS, {"agents": []})
    api.route("POST", CREATE, {"agent_id": "a"})
    assert setup.main(["agent", "--apply"], eleven=client) == 0
    assert [r.url.path for r in api.writes()] == [CREATE]
    assert locators(api.sent("POST", CREATE)[0]) == [
        {"pronunciation_dictionary_id": DICT_ID, "version_id": "v1"}
    ]


def test_missing_dictionary_is_created_from_rules(api, client):
    api.route("GET", DICTS, {"pronunciation_dictionaries": [], "has_more": False})
    api.route("POST", f"{DICTS}/add-from-rules", {"id": "dict_new", "version_id": "v0"})
    api.route("GET", AGENTS, {"agents": []})
    api.route("POST", CREATE, {"agent_id": "a"})
    assert setup.main(["agent", "--apply"], eleven=client) == 0
    assert api.sent("POST", f"{DICTS}/add-from-rules") == [{"name": DICT_NAME, "rules": [RULE]}]
    assert locators(api.sent("POST", CREATE)[0]) == [
        {"pronunciation_dictionary_id": "dict_new", "version_id": "v0"}
    ]


def test_changed_rules_are_replaced_and_the_new_version_used(api, client):
    api.route(
        "GET",
        f"{DICTS}/{DICT_ID}",
        {"id": DICT_ID, "latest_version_id": "v1", "rules": [RULE | {"alias": "Rohmer"}]},
    )
    api.route("POST", f"{DICTS}/{DICT_ID}/set-rules", {"id": DICT_ID, "version_id": "v2"})
    api.route("GET", AGENTS, {"agents": []})
    api.route("POST", CREATE, {"agent_id": "a"})
    assert setup.main(["agent", "--apply"], eleven=client) == 0
    assert api.sent("POST", f"{DICTS}/{DICT_ID}/set-rules") == [{"rules": [RULE]}]
    assert locators(api.sent("POST", CREATE)[0])[0]["version_id"] == "v2"


def test_dictionary_dry_run_writes_nothing(api, client, capsys):
    api.route("GET", DICTS, {"pronunciation_dictionaries": [], "has_more": False})
    api.route("GET", AGENTS, {"agents": []})
    assert setup.main(["agent"], eleven=client) == 0
    assert api.writes() == []
    assert (
        f"DRY RUN: POST https://api.elevenlabs.io{DICTS}/add-from-rules" in capsys.readouterr().out
    )


def test_dictionary_is_found_on_a_later_page(api, client):
    pages = iter(
        [
            {
                "pronunciation_dictionaries": [{"id": "x", "name": "x"}],
                "has_more": True,
                "next_cursor": "c2",
            },
            {"pronunciation_dictionaries": [{"id": DICT_ID, "name": DICT_NAME}], "has_more": False},
        ]
    )
    original = api.handler

    def handler(request):
        if request.url.path == DICTS:
            api.requests.append(request)
            return httpx.Response(200, json=next(pages))
        return original(request)

    api.handler = handler
    s = setup.Setup(
        eleven=setup.ElevenLabs(API_KEY, transport=httpx.MockTransport(handler)), apply=False
    )
    assert s.pronunciation()[0]["pronunciation_dictionary_id"] == DICT_ID
    cursors = [r.url.params.get("cursor") for r in api.requests if r.url.path == DICTS]
    assert cursors == [None, "c2"]


def test_live_agent_comparison(api, client, capsys):
    from rally_phone import agent_spec

    loc = [{"pronunciation_dictionary_id": DICT_ID, "version_id": "v1"}]
    body = agent_spec.build_agent_body(voice_id=VOICE, pronunciation_locators=loc)
    live = json.loads(json.dumps(body))
    live["conversation_config"]["tts"]["speed"] = 1.0  # extra live defaults are ignored
    api.route("GET", "/v1/convai/agents/a1", live)
    s = setup.Setup(eleven=client, apply=False)
    assert s.compare_live("a1", body) == []
    live["conversation_config"]["tts"]["pronunciation_dictionary_locators"] = []
    assert s.compare_live("a1", body) == ["tts.pronunciation_dictionary_locators"]
    assert "Differs from the live agent" in capsys.readouterr().out
