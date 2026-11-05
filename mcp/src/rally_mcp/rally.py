"""Calling Rally's API on someone's behalf.

Every request carries rally-mcp's service secret and who it is acting for —
the connection it arrived on, the household member's Access email and JWT
subject (or, for a token connection, the bearer token, which Rally checks
again on every call), the grant id, the client, the caller's public IP and
Cf-Ray, and the tool and arguments — as ``X-Rally-Mcp-*`` headers. Rally's
audit middleware trusts those headers only alongside the service secret,
checks the connection and the tool's permission, and records every event the
call causes under that person and connection. One tool call is one request id
on both sides.
"""

from __future__ import annotations

import json
import time
import uuid
from contextvars import ContextVar
from dataclasses import dataclass, field

import httpx

from rally_mcp.access import Identity


@dataclass(frozen=True)
class ConnectionInfo:
    """What Rally said about the connection a request arrived on."""

    id: str
    name: str
    auth_method: str
    tools: frozenset[str]
    scopes: frozenset[str]
    legacy: bool = False

    @classmethod
    def from_rally(cls, data: dict) -> ConnectionInfo:
        return cls(
            id=data["id"],
            name=data["name"],
            auth_method=data["auth_method"],
            tools=frozenset(data.get("tools") or ()),
            scopes=frozenset(data.get("scopes") or ()),
            legacy=bool(data.get("legacy")),
        )


@dataclass
class CallContext:
    # The Access identity; None on a token connection, where nobody signs in.
    identity: Identity | None
    connection: ConnectionInfo | None = None
    # A token connection's bearer token, forwarded so Rally checks it again.
    token: str | None = None
    client: str | None = None
    client_ip: str | None = None
    cf_ray: str | None = None
    user_agent: str | None = None
    tool: str | None = None
    arguments: dict = field(default_factory=dict)
    request_id: str = field(default_factory=lambda: str(uuid.uuid7()))


current_call: ContextVar[CallContext | None] = ContextVar("rally_mcp_call", default=None)


def identity_headers(call: CallContext | None) -> dict[str, str]:
    """The ``X-Rally-Mcp-*`` headers naming who a request to Rally is for."""
    if call is None:
        return {}
    ident = call.identity
    values = {
        "Connection-Id": call.connection.id if call.connection else None,
        "Connection-Token": call.token,
        "Email": ident.email if ident else None,
        "Jwt-Sub": ident.subject if ident else None,
        "Grant-Id": ident.grant_id if ident else None,
        "Client": ident.client if ident else call.client,
        "Client-Ip": call.client_ip,
        "Cf-Ray": call.cf_ray,
        "User-Agent": call.user_agent,
        "Tool": call.tool,
        "Arguments": json.dumps(call.arguments, default=str)[:4000] if call.tool else None,
        "Request-Id": call.request_id,
    }
    # Header values must be latin-1; JSON escapes anything else.
    return {
        f"X-Rally-Mcp-{name}": value.encode("latin-1", "replace").decode("latin-1")
        for name, value in values.items()
        if value
    }


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
        return {
            "X-Rally-Service-Token": self._service_token,
            **identity_headers(current_call.get()),
        }

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

    async def resolve_connection(self, **request) -> dict:
        """Ask Rally whether to serve a request on a connection, and with which tools.

        Rally logs and alerts a refusal itself. Rally unreachable, or any
        answer but a clear yes, is a refusal: fail closed.
        """
        try:
            response = await self._http.post(
                "/api/mcp/connections/resolve",
                json=request,
                headers={"X-Rally-Service-Token": self._service_token},
            )
            data = response.json() if response.status_code == 200 else None
        except httpx.HTTPError, ValueError:
            data = None
        if not isinstance(data, dict) or "allowed" not in data:
            return {"allowed": False, "status": 503, "reason": "Rally could not be asked"}
        return data

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
