"""Pre-created MCP connections: who may connect, how, from where, and to which tools.

A connection (``rally.models.Connection``) is a named client a household
member sets up in advance on the Connections page: "Claude", "Phone agent".
Every MCP request arrives on one connection's endpoint, ``/mcp/c/<id>``
(or the legacy ``/mcp``), and is checked here, twice: once by rally-mcp when
the request arrives (through ``POST /api/mcp/connections/resolve``), and again
by the audit middleware on every Rally API call the request makes. So a
compromised rally-mcp can still only do what the connection it names allows.

The checks, in order, each a denial that is logged and alerted:

1. the connection exists and is ``enabled`` (not ``disabled`` or ``revoked``);
2. the caller's public IP is in its allowlist (empty means nowhere);
3. its credential: a valid, unexpired bearer token for a ``token``
   connection, or, for an ``oauth`` one, a Cloudflare Access identity that is
   a household member and, once the connection is bound, the same member who
   first signed in to it;
4. on each API call, that the tool is one the connection may use and the
   route is one that tool may reach (``mcp_policy.call_denial``).

**Tokens** are generated here, shown once, and stored only as
HMAC-SHA256(key, token), the key derived from Rally's service secret
(``RALLY_MCP_SERVICE_TOKEN``) with a fixed label. Rotating that secret
therefore invalidates every token, which is the right failure: it is also
what rally-mcp authenticates with. Comparison is constant-time.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from rally.audit import alerts, identity, mcp_policy, recorder
from rally.audit import context as audit_context
from rally.models import Connection, FamilyMember, McpConnection
from rally.utils.timezone import now_utc

LEGACY_ID = "legacy"
LEGACY_PATH = "/mcp"
ENDPOINT_PREFIX = "/mcp/c/"

AUTH_METHODS = ("oauth", "token")
STATUSES = ("enabled", "disabled", "revoked")

PRESETS = {
    "full": "Full access",
    "read_only": "Read-only",
    "busy": "Busy-only",
    "custom": "Custom",
}

# Provider address ranges the Connections page can add to an allowlist in one
# click. Each records where the ranges come from and when they were checked.
IP_PRESETS = {
    "anthropic": {
        "label": "Anthropic (Claude)",
        # Anthropic's published *outbound* ranges: the addresses Claude's MCP
        # connectors call from. Source:
        # https://platform.claude.com/docs/en/api/ip-addresses (checked 2026-11-05).
        "ranges": ["160.79.104.0/21", "153.61.208.0/20"],
        "source": "https://platform.claude.com/docs/en/api/ip-addresses",
        "checked": "2026-11-05",
    },
    "elevenlabs": {
        "label": "ElevenLabs (phone agent)",
        # TODO(phase 3): ElevenLabs' outbound ranges for its MCP client go here,
        # with their source URL and the date they were checked. Until they
        # are, the preset adds nothing and the page says so.
        "ranges": [],
        "source": None,
        "checked": None,
    },
}

LAST_SEEN_RESOLUTION = timedelta(minutes=1)
TOKEN_PREFIX = "rly_"
_KEY_LABEL = b"rally connection token v1"


# --- Tokens ------------------------------------------------------------------


class TokenKeyMissing(RuntimeError):
    """No service secret to derive the token key from."""


def _token_key() -> bytes:
    secret = identity.service_token()
    if not secret:
        raise TokenKeyMissing("RALLY_MCP_SERVICE_TOKEN is not set")
    return hmac.new(secret.encode(), _KEY_LABEL, hashlib.sha256).digest()


def hash_token(token: str) -> str:
    return hmac.new(_token_key(), token.encode(), hashlib.sha256).hexdigest()


def new_token() -> str:
    return TOKEN_PREFIX + secrets.token_urlsafe(32)


def token_matches(conn: Connection, presented: str | None) -> bool:
    if not presented or not conn.token_hash:
        return False
    try:
        expected = hash_token(presented)
    except TokenKeyMissing:
        return False
    return hmac.compare_digest(expected, conn.token_hash)


def new_connection_id() -> str:
    return secrets.token_hex(8)


# --- Permissions -------------------------------------------------------------


def preset_tools(preset: str, catalog: dict[str, mcp_policy.ToolInfo]) -> set[str]:
    if preset == "full":
        return set(catalog)
    if preset == "read_only":
        return {n for n, t in catalog.items() if t.scope.endswith(":read")}
    if preset == "busy":
        return {n for n, t in catalog.items() if t.scope == mcp_policy.BUSY_SCOPE}
    return set()


def permitted_tools(conn: Connection, catalog: dict[str, mcp_policy.ToolInfo]) -> set[str]:
    """The tool names a connection may use, today.

    A named preset is resolved against the current catalog, so a Full access
    connection gets a tool Rally adds later; ``custom`` is exactly its list.
    """
    if conn.preset == "custom":
        return set(conn.tools or []) & set(catalog)
    return preset_tools(conn.preset, catalog)


# --- IP allowlists -------------------------------------------------------------


def parse_allowlist(entries: list[str]) -> list[str]:
    """Normalized CIDRs; raises ValueError naming the first bad entry."""
    out: list[str] = []
    for raw in entries:
        entry = (raw or "").strip()
        if not entry:
            continue
        try:
            net = ipaddress.ip_network(entry, strict=False)
        except ValueError as exc:
            raise ValueError(f"{entry!r} is not an IP address or CIDR range") from exc
        if str(net) not in out:
            out.append(str(net))
    return out


def ip_allowed(allowlist: list[str] | None, client_ip: str | None) -> bool:
    if not allowlist or not client_ip:
        return False
    try:
        addr = ipaddress.ip_address(client_ip.strip())
    except ValueError:
        return False
    for entry in allowlist:
        try:
            if addr in ipaddress.ip_network(entry, strict=False):
                return True
        except ValueError:
            continue
    return False


# --- Authorization -------------------------------------------------------------


@dataclass(frozen=True)
class Denial:
    status: int
    action: str
    reason: str
    title: str = "Rally: MCP request denied"


@dataclass
class Authorized:
    connection: Connection
    member: FamilyMember | None


def endpoint_path(conn: Connection) -> str:
    return LEGACY_PATH if conn.legacy else f"{ENDPOINT_PREFIX}{conn.id}"


def _naive(value: datetime) -> datetime:
    return value.replace(tzinfo=None)


def member_by_email(db: Session, email: str | None) -> FamilyMember | None:
    if not email:
        return None
    return (
        db.query(FamilyMember)
        .filter(FamilyMember.email.isnot(None))
        .filter(FamilyMember.email.ilike(email.strip()))
        .first()
    )


def authorize(
    db: Session,
    *,
    connection_id: str | None,
    email: str | None,
    token: str | None,
    client_ip: str | None,
) -> Authorized | Denial:
    """Check a request against its connection. Writes nothing on a denial.

    On success it notes the first use of a token (an event and an alert),
    binds an OAuth connection to the member who first signed in to it, and
    keeps ``last_seen_at`` / ``last_ip`` current (at most once a minute).
    """
    conn_id = connection_id or LEGACY_ID
    conn = db.get(Connection, conn_id)
    if conn is None:
        return Denial(
            403,
            "mcp.connection_unknown",
            f"No connection {conn_id!r} has been set up in Rally"
            if conn_id != LEGACY_ID
            else "The legacy /mcp path has no connection; use a connection's own endpoint",
        )
    if conn.status == "revoked":
        return Denial(403, "mcp.connection_revoked", f"Connection {conn.name!r} was revoked")
    if conn.status != "enabled":
        return Denial(403, "mcp.connection_disabled", f"Connection {conn.name!r} is disabled")
    if not ip_allowed(conn.ip_allowlist, client_ip):
        return Denial(
            403,
            "mcp.ip_rejected",
            f"{client_ip or 'An unknown address'} is not on connection {conn.name!r}'s "
            "IP allowlist",
            title="Rally: MCP connection from an address not allowed",
        )

    now = now_utc()
    if conn.auth_method == "token":
        if not token_matches(conn, token):
            return Denial(
                401,
                "connection.token_rejected",
                f"Missing or invalid token for connection {conn.name!r}",
                title="Rally: connection token rejected",
            )
        if conn.expires_at is not None and _naive(conn.expires_at) <= _naive(now):
            return Denial(
                401,
                "connection.token_rejected",
                f"The token for connection {conn.name!r} has expired",
                title="Rally: connection token rejected",
            )
        member = db.get(FamilyMember, conn.authorized_by) if conn.authorized_by else None
        if conn.first_used_at is None:
            conn.first_used_at = now
            db.flush()
            recorder.record(
                db,
                commit=False,
                action="connection.token_first_used",
                entity_type="connections",
                entity_id=conn.id,
                after={"connection": conn.name, "client_ip": client_ip},
            )
            alerts.send(
                db,
                title="Rally: connection token used for the first time",
                message=(
                    f"The token for {conn.name!r} was used for the first time.\n"
                    f"IP: {client_ip or 'unknown'}\nTime: {now.isoformat(timespec='seconds')}"
                ),
                request_id=audit_context.current().request_id,
            )
    else:
        if not email:
            return Denial(
                403,
                "mcp.identity_unresolved",
                f"Connection {conn.name!r} needs a Cloudflare Access sign-in",
            )
        member = member_by_email(db, email)
        if member is None:
            return Denial(
                403, "mcp.identity_unresolved", f"No household member has the email {email!r}"
            )
        if not conn.legacy and conn.authorized_by is not None and conn.authorized_by != member.id:
            return Denial(
                403,
                "mcp.connection_mismatch",
                f"Connection {conn.name!r} belongs to another household member",
            )
        if not conn.legacy and conn.authorized_by is None:
            conn.authorized_by = member.id

    stale = conn.last_seen_at is None or _naive(now) - _naive(conn.last_seen_at) >= (
        LAST_SEEN_RESOLUTION
    )
    if stale or conn.last_ip != client_ip:
        conn.last_seen_at = now
        conn.last_ip = client_ip
    db.commit()
    return Authorized(connection=conn, member=member)


def track_grant(
    db: Session,
    *,
    conn: Connection,
    member: FamilyMember,
    grant_id: str | None,
    email: str | None,
    jwt_subject: str | None,
    client: str | None,
    client_ip: str | None,
) -> None:
    """Record one person's OAuth sign-in on a connection; alert on the first."""
    grant_id = grant_id or f"{email}:{jwt_subject}"
    grant = db.get(McpConnection, grant_id)
    now = now_utc()
    if grant is not None:
        stale = _naive(now) - _naive(grant.last_seen_at) >= LAST_SEEN_RESOLUTION
        if stale or grant.last_ip != client_ip or grant.connection_id != conn.id:
            grant.last_seen_at = now
            grant.last_ip = client_ip
            grant.connection_id = conn.id
            db.commit()
        return
    db.add(
        McpConnection(
            id=grant_id,
            family_member_id=member.id,
            email=email,
            jwt_subject=jwt_subject,
            client=client,
            first_ip=client_ip,
            last_ip=client_ip,
            first_seen_at=now,
            last_seen_at=now,
            connection_id=conn.id,
        )
    )
    recorder.record(
        db,
        action="mcp.connection_authorized",
        entity_type="mcp_connections",
        entity_id=grant_id,
        after={
            "member": member.name,
            "email": email,
            "client": client,
            "client_ip": client_ip,
            "connection": conn.name,
            "connection_id": conn.id,
        },
    )
    alerts.send(
        db,
        title="Rally: new MCP connection",
        message=(
            f"{member.name} ({email}) signed in to the MCP connection {conn.name!r}.\n"
            f"Client: {client or 'unknown'}\nIP: {client_ip or 'unknown'}\n"
            f"Time: {now.isoformat(timespec='seconds')}"
        ),
        request_id=audit_context.current().request_id,
    )


def record_denial(db: Session, denial: Denial) -> None:
    """The denied event and its alert, under the current request."""
    ctx = audit_context.current()
    recorder.record(db, action=denial.action, outcome="denied", error=denial.reason)
    alerts.send(
        db,
        title=denial.title,
        message=(
            f"{denial.reason}\nActor: {ctx.actor_id}\n"
            f"Connection: {ctx.connection_name or ctx.client_info.get('connection_id') or '?'}\n"
            f"Client: {ctx.client_info.get('mcp_client') or 'unknown'}\n"
            f"IP: {ctx.client_info.get('client_ip') or ctx.client_info.get('ip')}\n"
            f"Request: {ctx.client_info.get('method')} "
            f"{ctx.client_info.get('upstream_path') or ctx.client_info.get('path')}"
        ),
        request_id=ctx.request_id,
    )


def describe(conn: Connection, catalog: dict[str, mcp_policy.ToolInfo]) -> dict:
    """What rally-mcp needs to serve a connection: its tools and their scopes."""
    tools = sorted(permitted_tools(conn, catalog))
    return {
        "id": conn.id,
        "name": conn.name,
        "auth_method": conn.auth_method,
        "legacy": conn.legacy,
        "tools": tools,
        "scopes": sorted({catalog[t].scope for t in tools}),
    }
