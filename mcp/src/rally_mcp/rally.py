"""Calling Rally's API on someone's behalf.

Every request carries rally-mcp's service secret and who it is acting for —
the household member's Access email, the JWT subject, the connection id, the
Claude client, the caller's public IP and Cf-Ray, and the tool and arguments
— as ``X-Rally-Mcp-*`` headers. Rally's audit middleware trusts those headers
only alongside the service secret, maps the email to a family member, and
records every event the call causes under that person and connection. One
tool call is one request id on both sides.
"""

from __future__ import annotations

import json
import time
import uuid
from contextvars import ContextVar
from dataclasses import dataclass, field

import httpx

from rally_mcp.access import Identity


@dataclass
class CallContext:
    identity: Identity
    client_ip: str | None = None
    cf_ray: str | None = None
    user_agent: str | None = None
    tool: str | None = None
    arguments: dict = field(default_factory=dict)
    request_id: str = field(default_factory=lambda: str(uuid.uuid7()))


current_call: ContextVar[CallContext | None] = ContextVar("rally_mcp_call", default=None)


class RallyError(Exception):
    def __init__(self, status: int, detail: str) -> None:
        super().__init__(f"Rally answered {status}: {detail}")
        self.status = status
        self.detail = detail


class RallyClient:
    def __init__(self, base_url: str, service_token: str, transport=None) -> None:
        self._service_token = service_token
        self._http = httpx.AsyncClient(base_url=base_url, timeout=15.0, transport=transport)
        self._status: tuple[float, bool] | None = None

    def _headers(self) -> dict[str, str]:
        headers = {"X-Rally-Service-Token": self._service_token}
        call = current_call.get()
        if call is None:
            return headers
        ident = call.identity
        values = {
            "Email": ident.email,
            "Jwt-Sub": ident.subject,
            "Connection-Id": ident.connection_id,
            "Client": ident.client,
            "Client-Ip": call.client_ip,
            "Cf-Ray": call.cf_ray,
            "User-Agent": call.user_agent,
            "Tool": call.tool,
            "Arguments": json.dumps(call.arguments, default=str)[:4000] if call.tool else None,
            "Request-Id": call.request_id,
        }
        for name, value in values.items():
            if value:
                # Header values must be latin-1; JSON escapes anything else.
                headers[f"X-Rally-Mcp-{name}"] = value.encode("latin-1", "replace").decode(
                    "latin-1"
                )
        return headers

    async def request(self, method: str, path: str, **kwargs):
        response = await self._http.request(method, path, headers=self._headers(), **kwargs)
        if response.status_code >= 400:
            try:
                detail = response.json().get("detail", response.text)
            except ValueError:
                detail = response.text
            raise RallyError(response.status_code, str(detail)[:500])
        if response.status_code == 204 or not response.content:
            return None
        return response.json()

    async def mcp_enabled(self, ttl: float = 10.0) -> bool:
        now = time.monotonic()
        if self._status and now - self._status[0] < ttl:
            return self._status[1]
        try:
            enabled = bool((await self.request("GET", "/api/mcp/status")).get("enabled"))
        except httpx.HTTPError, RallyError:
            enabled = False  # Rally unreachable or refusing: fail closed.
        self._status = (now, enabled)
        return enabled

    async def report_denial(self, **event) -> None:
        """Tell Rally about a request refused here, so it is logged and alerted."""
        try:
            await self._http.post(
                "/api/mcp/auth-events",
                json=event,
                headers={"X-Rally-Service-Token": self._service_token},
            )
        except httpx.HTTPError:
            pass  # Rally down: the refusal still stands; uvicorn logs the request.

    async def aclose(self) -> None:
        await self._http.aclose()
