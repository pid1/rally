"""A fake ElevenLabs API (httpx.MockTransport) that records every request.

No test talks to the network: ElevenLabs and Twilio are both replaced here.
Numbers use the fictional 555-01xx range.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime

import httpx
import pytest

from rally_phone.calls import Calls
from rally_phone.config import Settings
from rally_phone.elevenlabs import ElevenLabs

API_KEY = "sk-test-not-a-real-key-123"
NOW = datetime(2026, 11, 5, 15, 0, tzinfo=UTC)  # 09:00 in America/Chicago (CST)

SETTINGS = Settings(
    agent_id="agent_test",
    phone_number_id="phnum_test",
    owner_name="Test Owner",
    assistant_number="+15125550100",
)


def brief(**overrides) -> dict:
    data = {
        "callee_name": "Example County Tax Office",
        "to_number": "+15125550123",
        "topic": "a licence plate renewal",
        "goal": "Find out whether the plate renewal can be done by mail and what it costs.",
        "questions": ["Can it be renewed by mail?", "What is the fee?"],
        "may_share": ["The car is a passenger vehicle registered in the county."],
        "must_not_share": ["The owner's employer"],
        "give_up_when": "they need the owner in person or an ID number",
        "callee_timezone": "America/Chicago",
    }
    data.update(overrides)
    return data


@dataclass
class FakeAPI:
    routes: dict = field(default_factory=dict)
    requests: list[httpx.Request] = field(default_factory=list)

    def route(self, method: str, path: str, body=None, status: int = 200) -> None:
        self.routes[(method, path)] = (status, body if body is not None else {})

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        key = (request.method, request.url.path)
        if key not in self.routes:
            return httpx.Response(404, json={"detail": f"no route {key}"})
        status, body = self.routes[key]
        return httpx.Response(status, json=body)

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)

    def sent(self, method: str, path: str) -> list[dict]:
        return [
            json.loads(r.content) if r.content else {}
            for r in self.requests
            if r.method == method and r.url.path == path
        ]

    def writes(self) -> list[httpx.Request]:
        return [r for r in self.requests if r.method != "GET"]


@pytest.fixture
def api() -> FakeAPI:
    return FakeAPI()


@pytest.fixture
def client(api) -> ElevenLabs:
    return ElevenLabs(API_KEY, transport=api.transport)


@pytest.fixture
def calls(client) -> Calls:
    return Calls(client, SETTINGS, now=lambda: NOW)


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    """No test reads the real ~/.env, Keychain or config file."""
    from rally_phone import config, secrets

    for name in (
        "ELEVENLABS_API_KEY",
        "TWILIO_ACCOUNT_SID",
        "TWILIO_AUTH_TOKEN",
        "RALLY_PHONE_MCP_TOKEN",
        "ELEVENLABS_AGENT_ID",
        "ELEVENLABS_PHONE_NUMBER_ID",
        "RALLY_PHONE_OWNER_NAME",
        "RALLY_PHONE_ASSISTANT_NUMBER",
        "ELEVENLABS_BASE_URL",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(secrets, "DOTENV", tmp_path / "dotenv")
    monkeypatch.setattr(secrets, "_keychain", lambda service: None)
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.json")
