"""The agent config in phone/agent/ is complete and keeps the privacy rules."""

import json
import re

from conftest import NOW, brief

from rally_phone import agent_spec
from rally_phone.brief import parse_brief, to_dynamic_variables
from rally_phone.calls import DATA_FIELDS


def body():
    return agent_spec.build_agent_body()


def test_every_variable_is_sent_and_has_a_placeholder():
    b = body()
    agent = b["conversation_config"]["agent"]
    voicemail = agent["prompt"]["built_in_tools"]["voicemail_detection"]["params"]
    used = agent_spec.variables_used(
        agent["prompt"]["prompt"] + agent["first_message"] + voicemail["voicemail_message"]
    )
    sent = set(to_dynamic_variables(parse_brief(brief(), now=NOW), "X", "+15125550100"))
    placeholders = set(agent["dynamic_variables"]["dynamic_variable_placeholders"])
    assert used <= sent, used - sent
    assert used <= placeholders, used - placeholders
    assert sent == placeholders


def test_first_message_discloses_ai_and_owner():
    first = body()["conversation_config"]["agent"]["first_message"]
    assert first.startswith("Hi, I'm an AI assistant calling on behalf of {{owner_name}}")
    assert "transcribed" in first and "recorded" not in first


def test_prompt_carries_no_personal_data():
    prompt = body()["conversation_config"]["agent"]["prompt"]["prompt"]
    assert not re.search(r"\+?\d[\d\s().-]{8,}\d", prompt)  # no phone or ID numbers
    assert "Roemer" not in prompt  # the name arrives only as {{owner_name}}


def test_built_in_tools_only():
    prompt = body()["conversation_config"]["agent"]["prompt"]
    tools = prompt["built_in_tools"]
    assert set(tools) == {"end_call", "voicemail_detection", "play_keypad_touch_tone", "skip_turn"}
    for name, tool in tools.items():
        assert tool["type"] == "system" and tool["params"]["system_tool_type"] == name
    assert tools["end_call"]["description"] == ""  # ElevenLabs' default description
    assert tools["play_keypad_touch_tone"]["params"]["suppress_turn_after_dtmf"] is True
    vm = tools["voicemail_detection"]["params"]["voicemail_message"]
    assert "{{callback_number}}" in vm  # the brief's callback, else the assistant number
    assert prompt["tool_ids"] == [] and prompt["mcp_server_ids"] == []
    assert "Rally availability" not in prompt["prompt"]


def test_prompt_follows_the_prompting_guide():
    text = body()["conversation_config"]["agent"]["prompt"]["prompt"]
    headings = [line for line in text.splitlines() if line.startswith("# ")]
    assert headings[:7] == [
        "# Personality",
        "# Environment",
        "# Tone",
        "# Goal",
        "# Guardrails",
        "# Tools",
        "# Tool error handling",
    ]
    assert text.count("This step is important.") >= 3
    assert len(text.split()) < 1100  # well under ~2000 tokens
    assert "8 a.m. and 9 p.m." in text


def test_voice_and_llm():
    b = body()
    tts = b["conversation_config"]["tts"]
    assert tts["model_id"] == "eleven_v4_turbo"
    assert tts["expressive_mode"] is True
    assert tts["text_normalisation_type"] == "elevenlabs"
    assert "speed" not in tts and "style" not in tts
    assert {"stability", "similarity_boost", "voice_id"} <= set(tts)
    assert not set(tts) & set(agent_spec.LIBRARY_KEYS)
    assert (
        agent_spec.build_agent_body(voice_id="v_new")["conversation_config"]["tts"]["voice_id"]
        == "v_new"
    )
    prompt = b["conversation_config"]["agent"]["prompt"]
    assert prompt["llm"] == "claude-sonnet-5-5"
    assert prompt["temperature"] <= 0.3
    assert "thinking_budget" not in prompt  # null in agent.json: ElevenLabs default
    assert "reasoning_effort" not in prompt


def test_privacy_and_limits():
    ps = body()["platform_settings"]
    assert ps["privacy"]["record_voice"] is False
    assert 0 < ps["privacy"]["retention_days"] <= 90
    assert ps["call_limits"] == {
        "agent_concurrency_limit": 1,
        "daily_limit": 10,
        "bursting_enabled": False,
    }
    # Overrides limited to what batch calling requires.
    ov = ps["overrides"]["conversation_config_override"]
    assert ov == {
        "agent": {"first_message": True, "language": True, "prompt": {"prompt": True}},
        "tts": {"voice_id": True},
    }


def test_analysis_fields_match_what_get_call_reads():
    ps = body()["platform_settings"]
    assert set(ps["data_collection"]) == set(DATA_FIELDS)
    for item in ps["data_collection"].values():
        assert item["type"] in {"string", "boolean", "integer", "number"}
        assert item["description"]
    ids = [c["id"] for c in ps["evaluation"]["criteria"]]
    assert "no_sensitive_info_shared" in ids and "disclosed_ai" in ids
    assert all(c["type"] == "prompt" for c in ps["evaluation"]["criteria"])


def test_no_comment_keys_leak_into_requests():
    assert '"_' not in json.dumps(body())


def test_rally_mcp_hook_is_disabled_and_uses_a_bearer_header():
    assert agent_spec.load()["rally_mcp"]["enabled"] is False
    b = agent_spec.rally_mcp_body(token="tok")
    assert b["config"]["request_headers"] == {"Authorization": "Bearer tok"}
    assert b["config"]["transport"] == "STREAMABLE_HTTP"
    with_tools = agent_spec.build_agent_body(mcp_server_ids=["m1"])
    assert "Rally availability" in with_tools["conversation_config"]["agent"]["prompt"]["prompt"]
