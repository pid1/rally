"""A small, explicit client for the ElevenLabs Agents endpoints rally-phone uses.

Endpoints (checked 2026-11-05 against https://elevenlabs.io/docs/api-reference
and the generated elevenlabs-python SDK):

* ``POST /v1/convai/twilio/outbound-call``          place a call now
* ``POST /v1/convai/batch-calling/submit``          schedule a call (one recipient)
* ``GET  /v1/convai/batch-calling/{id}``            scheduled call status
* ``POST /v1/convai/batch-calling/{id}/cancel``     cancel a scheduled call
* ``GET  /v1/convai/batch-calling/workspace``       list scheduled calls
* ``GET  /v1/convai/conversations/{id}``            status, transcript, analysis
* ``GET  /v1/convai/conversations``                 recent calls
* agents, phone numbers and MCP servers for ``scripts/setup.py``

The API key travels only in the ``xi-api-key`` header. Error messages carry
the status code and ElevenLabs' ``detail``, never request headers.
"""

from __future__ import annotations

from typing import Any

import httpx

DEFAULT_BASE_URL = "https://api.elevenlabs.io"


class ElevenLabsError(RuntimeError):
    def __init__(self, status: int, detail: Any, path: str):
        self.status = status
        self.detail = detail
        self.path = path
        super().__init__(f"ElevenLabs {path} returned {status}: {detail}")


class ElevenLabs:
    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = DEFAULT_BASE_URL,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 30.0,
    ):
        self._http = httpx.Client(
            base_url=base_url,
            headers={"xi-api-key": api_key, "accept": "application/json"},
            transport=transport,
            timeout=timeout,
        )

    def close(self) -> None:
        self._http.close()

    def request(
        self, method: str, path: str, *, json: Any = None, params: dict | None = None
    ) -> Any:
        params = {k: v for k, v in (params or {}).items() if v is not None}
        resp = self._http.request(method, path, json=json, params=params or None)
        if resp.status_code >= 400:
            try:
                detail = resp.json().get("detail", resp.text[:300])
            except ValueError:
                detail = resp.text[:300]
            raise ElevenLabsError(resp.status_code, detail, path)
        if not resp.content:
            return {}
        return resp.json()

    # Calls -----------------------------------------------------------------

    def outbound_call(
        self,
        *,
        agent_id: str,
        phone_number_id: str,
        to_number: str,
        dynamic_variables: dict[str, str],
        ringing_timeout_secs: int,
    ) -> dict:
        return self.request(
            "POST",
            "/v1/convai/twilio/outbound-call",
            json={
                "agent_id": agent_id,
                "agent_phone_number_id": phone_number_id,
                "to_number": to_number,
                "call_recording_enabled": False,
                "conversation_initiation_client_data": {"dynamic_variables": dynamic_variables},
                "telephony_call_config": {
                    "ringing_timeout_secs": ringing_timeout_secs,
                    "twilio_call_recording_enabled": False,
                },
            },
        )

    def submit_batch(
        self,
        *,
        call_name: str,
        agent_id: str,
        phone_number_id: str,
        to_number: str,
        dynamic_variables: dict[str, str],
        scheduled_time_unix: int,
        timezone: str,
        ringing_timeout_secs: int,
    ) -> dict:
        return self.request(
            "POST",
            "/v1/convai/batch-calling/submit",
            json={
                "call_name": call_name,
                "agent_id": agent_id,
                "agent_phone_number_id": phone_number_id,
                "scheduled_time_unix": scheduled_time_unix,
                "timezone": timezone,
                "target_concurrency_limit": 1,
                "recipients": [
                    {
                        "phone_number": to_number,
                        "conversation_initiation_client_data": {
                            "dynamic_variables": dynamic_variables
                        },
                    }
                ],
                "telephony_call_config": {
                    "ringing_timeout_secs": ringing_timeout_secs,
                    "twilio_call_recording_enabled": False,
                },
            },
        )

    def get_batch(self, batch_id: str) -> dict:
        return self.request("GET", f"/v1/convai/batch-calling/{safe_id(batch_id)}")

    def cancel_batch(self, batch_id: str) -> dict:
        return self.request("POST", f"/v1/convai/batch-calling/{safe_id(batch_id)}/cancel")

    def list_batches(self, limit: int = 20) -> dict:
        return self.request("GET", "/v1/convai/batch-calling/workspace", params={"limit": limit})

    def get_conversation(self, conversation_id: str) -> dict:
        return self.request("GET", f"/v1/convai/conversations/{safe_id(conversation_id)}")

    def list_conversations(self, *, agent_id: str, page_size: int) -> dict:
        return self.request(
            "GET",
            "/v1/convai/conversations",
            params={"agent_id": agent_id, "page_size": page_size},
        )

    # Setup -----------------------------------------------------------------

    def list_agents(self, search: str) -> dict:
        return self.request("GET", "/v1/convai/agents", params={"search": search, "page_size": 100})

    def list_phone_numbers(self) -> list:
        data = self.request("GET", "/v1/convai/phone-numbers")
        return data if isinstance(data, list) else data.get("phone_numbers", [])

    def list_mcp_servers(self) -> dict:
        return self.request("GET", "/v1/convai/mcp-servers")


def safe_id(value: str) -> str:
    """Refuse IDs that could change the request path."""
    if not value or not all(c.isalnum() or c in "_-" for c in value):
        raise ValueError(f"not a valid ElevenLabs id: {value!r}")
    return value
