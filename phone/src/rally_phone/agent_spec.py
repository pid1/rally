"""Build the ElevenLabs agent request body from ``phone/agent/``.

The body is the same for create (``POST /v1/convai/agents/create``) and update
(``PATCH /v1/convai/agents/{id}``), so re-running setup converges the agent on
what is in the repo.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

AGENT_DIR = Path(__file__).resolve().parents[2] / "agent"
VARIABLE = re.compile(r"\{\{\s*([a-z][a-z0-9_]*)\s*\}\}")

# Shown only in ElevenLabs' dashboard preview and tests; real calls always send
# every variable (rally_phone.brief.to_dynamic_variables).
PLACEHOLDERS = {
    "callee_name": "Example County Tax Office",
    "call_topic": "a licence plate renewal",
    "call_goal": "Find out what is needed to renew a licence plate registration by mail.",
    "questions": "1. Can the renewal be done by mail?\n2. What documents are needed?",
    "may_share": "1. The vehicle is a passenger car registered in this county.",
    "must_not_share": "1. Any account, licence or card number\n2. Any address",
    "give_up_when": "they need the owner in person or need an ID number",
    "callback_number": "512-555-0100",
    "assistant_number": "512-555-0100",
    "call_window": "none (call placed now)",
}


def load(agent_dir: Path = AGENT_DIR) -> dict:
    return json.loads((agent_dir / "agent.json").read_text())


def _strip_comments(obj):
    if isinstance(obj, dict):
        return {k: _strip_comments(v) for k, v in obj.items() if not k.startswith("_")}
    if isinstance(obj, list):
        return [_strip_comments(v) for v in obj]
    return obj


def system_prompt(agent_dir: Path = AGENT_DIR, *, with_rally_tools: bool = False) -> str:
    text = (agent_dir / "system_prompt.md").read_text()
    if with_rally_tools:
        text += "\n" + (agent_dir / "prompt_rally_tools.md").read_text()
    return text


def variables_used(text: str) -> set[str]:
    return set(VARIABLE.findall(text))


LIBRARY_KEYS = ("library_owner_id", "library_name")


def tts_config(
    spec: dict, voice_id: str | None = None, pronunciation_locators: list[dict] | None = None
) -> dict:
    tts = {k: v for k, v in spec["voice"].items() if k not in LIBRARY_KEYS}
    if voice_id:
        tts["voice_id"] = voice_id
    if pronunciation_locators is not None:
        tts["pronunciation_dictionary_locators"] = pronunciation_locators
    return tts


RULE_DEFAULTS = {"case_sensitive": True, "word_boundaries": True}


def normalise_rules(rules: list[dict]) -> list[dict]:
    """Rules with ElevenLabs' defaults filled in, in a stable order, for comparison."""
    keys = ("type", "string_to_replace", "alias", "phoneme", "alphabet", *RULE_DEFAULTS)
    out = []
    for r in rules:
        full = RULE_DEFAULTS | {k: v for k, v in r.items() if v is not None}
        out.append({k: full[k] for k in keys if k in full})
    return sorted(out, key=lambda r: (r["string_to_replace"], r["type"]))


def llm_config(llm: dict) -> dict:
    out = {
        "llm": llm["model"],
        "temperature": llm["temperature"],
        "max_tokens": llm["max_tokens"],
        "timezone": llm["timezone"],
    }
    for key in ("thinking_budget", "reasoning_effort"):
        if llm.get(key) is not None:
            out[key] = llm[key]
    return out


def built_in_tools(tools: dict) -> dict:
    def system(name: str, description: str = "", **params) -> dict:
        return {
            "type": "system",
            "name": name,
            "description": description,
            "params": {"system_tool_type": name, **params},
        }

    return {
        "end_call": system("end_call"),
        "voicemail_detection": system(
            "voicemail_detection", voicemail_message=tools["voicemail_message"]
        ),
        "play_keypad_touch_tone": system(
            "play_keypad_touch_tone",
            "Press keypad digits only to navigate a phone menu toward a person or department. "
            "Never enter account, ID, PIN, card or date-of-birth digits.",
            suppress_turn_after_dtmf=True,
        ),
        "skip_turn": system(
            "skip_turn",
            "Use when asked to hold, or during hold music or recordings; stay silent.",
            wait_timeout_secs=tools["skip_turn_wait_secs"],
        ),
    }


def build_agent_body(
    agent_dir: Path = AGENT_DIR,
    *,
    mcp_server_ids: list[str] | None = None,
    voice_id: str | None = None,
    pronunciation_locators: list[dict] | None = None,
) -> dict:
    """``voice_id`` replaces agent.json's when the library add returned a different id;
    ``pronunciation_locators`` comes from setup's pronunciation dictionary step."""
    spec = _strip_comments(load(agent_dir))
    prompt = system_prompt(agent_dir, with_rally_tools=bool(mcp_server_ids))
    first_message = (agent_dir / "first_message.txt").read_text().strip()
    placeholders = {"owner_name": spec["owner_name"], **PLACEHOLDERS}
    return {
        "name": spec["name"],
        "tags": spec["tags"],
        "conversation_config": {
            "agent": {
                "first_message": first_message,
                "language": spec["language"],
                "disable_first_message_interruptions": True,
                "dynamic_variables": {"dynamic_variable_placeholders": placeholders},
                "prompt": {
                    "prompt": prompt,
                    **llm_config(spec["llm"]),
                    "tool_ids": [],
                    "mcp_server_ids": mcp_server_ids or [],
                    "built_in_tools": built_in_tools(spec["tools"]),
                },
            },
            "tts": tts_config(spec, voice_id, pronunciation_locators),
            "turn": spec["turn"],
            "conversation": spec["conversation"],
        },
        "platform_settings": {
            "data_collection": spec["data_collection"],
            "evaluation": {
                "criteria": [
                    {"type": "prompt", "use_knowledge_base": False, **c}
                    for c in spec["evaluation_criteria"]
                ]
            },
            "privacy": spec["privacy"],
            "overrides": {"conversation_config_override": spec["overrides"]},
            "call_limits": spec["call_limits"],
        },
    }


def rally_mcp_body(agent_dir: Path = AGENT_DIR, *, token: str) -> dict:
    """The custom MCP server request (POST /v1/convai/mcp-servers). V2 hook."""
    hook = _strip_comments(load(agent_dir))["rally_mcp"]
    return {
        "config": {
            "name": hook["name"],
            "description": "Rally busy-only availability: " + ", ".join(hook["tools"]),
            "url": hook["url"],
            "transport": hook["transport"],
            "approval_policy": "auto_approve_all",
            "request_headers": {"Authorization": f"Bearer {token}"},
        }
    }
