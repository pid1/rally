"""Working out who sent a request: a tailnet person, or rally-mcp on someone's behalf.

**Tailnet requests** are identified, never authenticated — Rally still has no
login. Two sources, in order:

1. Tailscale Serve identity headers (``Tailscale-User-Login`` and friends), but
   only when the request arrived from a trusted proxy address (Serve proxies
   from localhost). From anywhere else those headers are ignored, because any
   tailnet client could type them.
2. The Tailscale LocalAPI ``whois`` on the source address, through
   tailscaled's unix socket (``RALLY_TAILSCALE_SOCKET``), cached briefly.

Neither available means the request is served and logged as ``unknown``.

**MCP requests** come from rally-mcp, which authenticates itself with a shared
service secret (``RALLY_MCP_SERVICE_TOKEN``, held by rally-mcp alone) and names
the household member it is acting for. A request that claims an MCP identity
without the right secret is refused and logged — nothing else on the tailnet
can impersonate rally-mcp.
"""

from __future__ import annotations

import hmac
import http.client
import json
import os
import socket
import time
from dataclasses import dataclass, field
from urllib.parse import quote

MCP_HEADER_PREFIX = "x-rally-mcp-"
SERVICE_TOKEN_HEADER = "x-rally-service-token"

WHOIS_TTL_SECONDS = 60
# The login Tailscale reports for every tagged node (nodes owned by tags, not users).
TAGGED_DEVICES = "tagged-devices"
_whois_cache: dict[str, tuple[float, dict | None]] = {}


def _trusted_proxies() -> set[str]:
    raw = os.environ.get("RALLY_TRUSTED_PROXIES", "127.0.0.1,::1")
    return {p.strip() for p in raw.split(",") if p.strip()}


def tailscale_socket() -> str | None:
    path = os.environ.get("RALLY_TAILSCALE_SOCKET", "/var/run/tailscale/tailscaled.sock")
    return path if path and os.path.exists(path) else None


class _UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self, path: str, timeout: float = 1.0):
        super().__init__("local-tailscaled.sock", timeout=timeout)
        self._path = path

    def connect(self) -> None:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        sock.connect(self._path)
        self.sock = sock


def whois(addr: str) -> dict | None:
    """Tailscale identity for a source ``ip`` or ``ip:port``, or None."""
    now = time.monotonic()
    ip = addr.rsplit(":", 1)[0] if addr.count(":") == 1 else addr
    cached = _whois_cache.get(ip)
    if cached and now - cached[0] < WHOIS_TTL_SECONDS:
        return cached[1]
    sock_path = tailscale_socket()
    result = None
    if sock_path:
        try:
            conn = _UnixHTTPConnection(sock_path)
            conn.request("GET", f"/localapi/v0/whois?addr={quote(ip)}")
            resp = conn.getresponse()
            if resp.status == 200:
                data = json.loads(resp.read())
                node = data.get("Node") or {}
                profile = data.get("UserProfile") or {}
                hostinfo = node.get("Hostinfo") or {}
                result = {
                    "login": profile.get("LoginName"),
                    "display_name": profile.get("DisplayName"),
                    "node_name": node.get("ComputedName") or node.get("Name"),
                    "node_id": node.get("StableID") or node.get("ID"),
                    "tailnet_ip": ip,
                    "os": hostinfo.get("OS"),
                    "device": hostinfo.get("Hostname"),
                }
            conn.close()
        except OSError, ValueError, http.client.HTTPException:
            result = None
    _whois_cache[ip] = (now, result)
    return result


@dataclass
class TailnetIdentity:
    login: str | None
    display_name: str | None = None
    details: dict = field(default_factory=dict)


def tailnet_identity(headers: dict[str, str], client_ip: str | None) -> TailnetIdentity | None:
    if client_ip in _trusted_proxies() and headers.get("tailscale-user-login"):
        return TailnetIdentity(
            login=headers.get("tailscale-user-login"),
            display_name=headers.get("tailscale-user-name"),
            details={
                "via": "tailscale-serve",
                "login": headers.get("tailscale-user-login"),
                "display_name": headers.get("tailscale-user-name"),
            },
        )
    if client_ip:
        info = whois(client_ip)
        if info and info.get("login") == TAGGED_DEVICES and info.get("node_name"):
            # A tagged node belongs to no user: Tailscale reports the same
            # "tagged-devices" login for all of them. The device is the most
            # specific identity there is, so that is who acted.
            node = info["node_name"].rstrip(".").split(".")[0]
            return TailnetIdentity(
                login=f"device:{node}",
                display_name=node,
                details={"via": "tailscale-whois", **info},
            )
        if info and info.get("login"):
            return TailnetIdentity(
                login=info["login"],
                display_name=info.get("display_name"),
                details={"via": "tailscale-whois", **info},
            )
    return None


# --- MCP ---------------------------------------------------------------------


def service_token() -> str:
    return os.environ.get("RALLY_MCP_SERVICE_TOKEN", "")


@dataclass
class McpClaim:
    """What rally-mcp says about the call it is forwarding."""

    service_token_ok: bool
    email: str | None
    jwt_subject: str | None
    connection_id: str | None
    client: str | None
    client_ip: str | None
    cf_ray: str | None
    tool: str | None
    arguments: dict | None
    upstream_request_id: str | None
    user_agent: str | None


def mcp_claim(headers: dict[str, str]) -> McpClaim | None:
    """None when the request makes no MCP claim at all (an ordinary tailnet call)."""
    claims_mcp = SERVICE_TOKEN_HEADER in headers or any(
        k.startswith(MCP_HEADER_PREFIX) for k in headers
    )
    if not claims_mcp:
        return None
    expected = service_token()
    presented = headers.get(SERVICE_TOKEN_HEADER, "")
    ok = bool(expected) and hmac.compare_digest(presented.encode(), expected.encode())
    try:
        arguments = json.loads(headers.get("x-rally-mcp-arguments") or "null")
    except ValueError:
        arguments = {"unparseable": True}

    def h(name: str) -> str | None:
        value = headers.get(MCP_HEADER_PREFIX + name)
        return value.strip() if value and value.strip() else None

    return McpClaim(
        service_token_ok=ok,
        email=(h("email") or "").lower() or None,
        jwt_subject=h("jwt-sub"),
        connection_id=h("connection-id"),
        client=h("client"),
        client_ip=h("client-ip"),
        cf_ray=h("cf-ray"),
        tool=h("tool"),
        arguments=arguments if isinstance(arguments, dict) else None,
        upstream_request_id=h("request-id"),
        user_agent=h("user-agent"),
    )
