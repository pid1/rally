"""The request half of the audit log: an actor for every request, an event for every request.

For each HTTP request (static files aside) this middleware

1. builds the ``AuditContext`` — request id, source, client IP, user agent,
   path, method, and the caller's identity (Tailscale for tailnet requests, the
   forwarded household member for rally-mcp);
2. refuses, before any handler runs, a request that claims an MCP identity
   without rally-mcp's service secret, arrives while MCP is disabled, names
   nobody Rally knows, or asks for a route MCP may not use — each refusal is a
   ``denied`` event and an alert;
3. after the handler, writes the reads the request made and one
   ``http.request`` event carrying the status, so a request that failed —
   including a change that was rolled back — is still on the record.

The response carries ``X-Request-Id`` so a person (or rally-mcp) can find
every event from one request on the Audit Log page.
"""

from __future__ import annotations

import json
from datetime import timedelta

import anyio
from starlette.routing import Match
from starlette.types import ASGIApp, Receive, Scope, Send

from rally.audit import alerts, identity, recorder
from rally.audit import context as audit_context
from rally.audit.context import (
    ACTOR_MCP,
    ACTOR_USER,
    SOURCE_API,
    SOURCE_MCP,
    SOURCE_WEB,
    UNKNOWN_ACTOR,
    AuditContext,
)
from rally.database import get_db
from rally.models import FamilyMember, McpConnection, Setting
from rally.utils.timezone import now_utc

MCP_ENABLED_KEY = "mcp_enabled"

# Which routes rally-mcp may call on someone's behalf is decided by
# ``rally.audit.mcp_policy``: every /api route except a short, reasoned
# denylist (secrets, identity, the audit log, MCP's own switch).
# rally-mcp's own plumbing: allowed with the service secret alone, even while
# MCP is disabled, because that is how rally-mcp learns it is disabled and
# reports what it refused.
MCP_SERVICE_ROUTES = {
    ("GET", "/api/mcp/status"),
    ("POST", "/api/mcp/auth-events"),
    ("GET", "/api/mcp/openapi"),
}

SKIP_PREFIXES = ("/static/", "/favicon")
LAST_SEEN_RESOLUTION = timedelta(minutes=1)


def mcp_enabled(db) -> bool:
    row = db.get(Setting, MCP_ENABLED_KEY)
    return bool(row and row.value.strip().lower() == "true")


def _session(app):
    """A session on the app's database — honouring test overrides of ``get_db``."""
    factory = app.dependency_overrides.get(get_db, get_db)
    gen = factory()
    return next(gen), gen


def _close(gen) -> None:
    try:
        next(gen)
    except StopIteration:
        pass


def iter_routes(routes):
    """Every concrete route, with included routers flattened to full paths.

    FastAPI keeps ``include_router`` results as nested router objects whose
    ``effective_candidates()`` carry the prefixed paths; those are what match a
    request, so they are what the allowlist and the coverage test must see.
    """
    for route in routes:
        if hasattr(route, "effective_candidates"):
            yield from iter_routes(route.effective_candidates())
        else:
            yield route


def _route_template(app, scope: Scope) -> str | None:
    for route in iter_routes(app.router.routes):
        match, _ = route.matches(scope)
        if match == Match.FULL:
            return getattr(route, "path", None)
    return None


def _member_by_email(db, email: str | None) -> FamilyMember | None:
    if not email:
        return None
    return (
        db.query(FamilyMember)
        .filter(FamilyMember.email.isnot(None))
        .filter(FamilyMember.email.ilike(email.strip()))
        .first()
    )


class AuditMiddleware:
    def __init__(self, app: ASGIApp, fastapi_app) -> None:
        self.app = app
        self.fastapi_app = fastapi_app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["path"].startswith(SKIP_PREFIXES):
            await self.app(scope, receive, send)
            return

        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
        client_ip = scope["client"][0] if scope.get("client") else None
        method = scope["method"]
        path = scope["path"]
        route = _route_template(self.fastapi_app, scope)
        claim = identity.mcp_claim(headers)

        ctx = AuditContext(
            request_id=audit_context.new_request_id(),
            source=SOURCE_MCP
            if claim
            else (SOURCE_API if path.startswith("/api/") else SOURCE_WEB),
            actor_type=ACTOR_MCP if claim else ACTOR_USER,
            is_request=True,
            client_info={
                "ip": client_ip,
                "user_agent": headers.get("user-agent"),
                "method": method,
                "path": path,
                "route": route,
            },
        )
        token = audit_context.activate(ctx)
        try:
            if claim is not None:
                denial = await anyio.to_thread.run_sync(
                    self._authorize_mcp, ctx, claim, method, route
                )
            else:
                await anyio.to_thread.run_sync(self._identify_tailnet, ctx, headers, client_ip)
                denial = None

            if denial is not None:
                await self._send_denial(send, ctx, denial)
                return

            status = {"code": 500}
            error_body = bytearray()

            async def send_wrapper(message):
                if message["type"] == "http.response.start":
                    status["code"] = message["status"]
                    message.setdefault("headers", [])
                    message["headers"] = list(message["headers"]) + [
                        (b"x-request-id", ctx.request_id.encode())
                    ]
                elif message["type"] == "http.response.body" and status["code"] >= 400:
                    if len(error_body) < 1000:
                        error_body.extend(message.get("body", b"")[:1000])
                await send(message)

            error: str | None = None
            try:
                await self.app(scope, receive, send_wrapper)
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
                raise
            finally:
                if error is None and status["code"] >= 400:
                    error = error_body.decode("utf-8", "replace") or None
                await anyio.to_thread.run_sync(self._finish, ctx, status["code"], error)
        finally:
            audit_context.reset(token)

    # --- identity ------------------------------------------------------------

    def _identify_tailnet(self, ctx: AuditContext, headers: dict, client_ip: str | None) -> None:
        found = identity.tailnet_identity(headers, client_ip)
        if not found or not found.login:
            ctx.actor_id = UNKNOWN_ACTOR
            ctx.flagged = True
            return
        ctx.actor_id = found.login
        ctx.client_info["identity"] = found.details
        db, gen = _session(self.fastapi_app)
        try:
            member = _member_by_email(db, found.login)
            ctx.actor_name = member.name if member else found.display_name
            if member:
                ctx.client_info["member_id"] = member.id
        finally:
            _close(gen)

    def _authorize_mcp(self, ctx: AuditContext, claim, method: str, route: str | None):
        """Return None to let the request through, or (status, action, reason)."""
        ctx.actor_id = claim.email or UNKNOWN_ACTOR
        ctx.mcp_tool = claim.tool
        ctx.mcp_arguments = claim.arguments
        ctx.client_info.update(
            {
                "mcp_client": claim.client,
                "client_ip": claim.client_ip,
                "cf_ray": claim.cf_ray,
                "connection_id": claim.connection_id,
                "jwt_subject": claim.jwt_subject,
                "upstream_request_id": claim.upstream_request_id,
                "upstream_user_agent": claim.user_agent,
                "via": "rally-mcp",
            }
        )
        if claim.upstream_request_id:
            # Keep rally-mcp's id so one tool call is one request_id end to end.
            ctx.request_id = claim.upstream_request_id[:36]

        if not claim.service_token_ok:
            ctx.flagged = True
            return 403, "mcp.service_auth_failed", "Missing or invalid rally-mcp service credential"

        db, gen = _session(self.fastapi_app)
        try:
            if (method, route) in MCP_SERVICE_ROUTES:
                ctx.actor_type = audit_context.ACTOR_SYSTEM
                ctx.actor_id = claim.email or "rally-mcp"
                return None
            if not mcp_enabled(db):
                return 403, "mcp.disabled", "MCP is disabled in Rally's settings"
            from rally.audit import mcp_policy

            if (method, route) not in mcp_policy.allowed_routes(self.fastapi_app):
                return (
                    403,
                    "mcp.route_not_allowed",
                    f"{method} {route or ctx.client_info['path']} is not an MCP route",
                )
            member = _member_by_email(db, claim.email)
            if member is None:
                ctx.flagged = True
                return (
                    403,
                    "mcp.identity_unresolved",
                    (
                        f"No household member has the email {claim.email!r}"
                        if claim.email
                        else "No acting identity was forwarded"
                    ),
                )
            ctx.actor_name = member.name
            ctx.client_info["member_id"] = member.id
            self._track_connection(db, ctx, claim, member)
            return None
        finally:
            _close(gen)

    def _track_connection(self, db, ctx: AuditContext, claim, member: FamilyMember) -> None:
        conn_id = claim.connection_id or f"{claim.email}:{claim.jwt_subject}"
        conn = db.get(McpConnection, conn_id)
        now = now_utc()
        if conn is not None:
            # Refreshed at most once a minute (or when the IP changes): every
            # tool call is already its own audit trail, and a last-seen write
            # per request would double the log for no new information.
            stale = now.replace(tzinfo=None) - conn.last_seen_at.replace(tzinfo=None)
            if stale >= LAST_SEEN_RESOLUTION or conn.last_ip != claim.client_ip:
                conn.last_seen_at = now
                conn.last_ip = claim.client_ip
                db.commit()
            return
        db.add(
            McpConnection(
                id=conn_id,
                family_member_id=member.id,
                email=claim.email,
                jwt_subject=claim.jwt_subject,
                client=claim.client,
                first_ip=claim.client_ip,
                last_ip=claim.client_ip,
                first_seen_at=now,
                last_seen_at=now,
            )
        )
        recorder.record(
            db,
            action="mcp.connection_authorized",
            entity_type="mcp_connections",
            entity_id=conn_id,
            after={
                "member": member.name,
                "email": claim.email,
                "client": claim.client,
                "client_ip": claim.client_ip,
            },
        )
        alerts.send(
            db,
            title="Rally: new MCP connection",
            message=(
                f"{member.name} ({claim.email}) authorized a new MCP connection.\n"
                f"Client: {claim.client or 'unknown'}\nIP: {claim.client_ip or 'unknown'}\n"
                f"Time: {now.isoformat(timespec='seconds')}"
            ),
            request_id=ctx.request_id,
        )

    # --- writing ---------------------------------------------------------------

    def _record_denial(self, ctx: AuditContext, action: str, reason: str) -> None:
        db, gen = _session(self.fastapi_app)
        try:
            recorder.record(db, action=action, outcome="denied", error=reason)
            alerts.send(
                db,
                title="Rally: MCP request denied",
                message=(
                    f"{reason}\nActor: {ctx.actor_id}\n"
                    f"Client: {ctx.client_info.get('mcp_client') or 'unknown'}\n"
                    f"IP: {ctx.client_info.get('client_ip') or ctx.client_info.get('ip')}\n"
                    f"Request: {ctx.client_info['method']} {ctx.client_info['path']}"
                ),
                request_id=ctx.request_id,
            )
        finally:
            _close(gen)

    async def _send_denial(self, send: Send, ctx: AuditContext, denial) -> None:
        status, action, reason = denial
        await anyio.to_thread.run_sync(self._record_denial, ctx, action, reason)
        body = json.dumps({"detail": reason, "request_id": ctx.request_id}).encode()
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                    (b"x-request-id", ctx.request_id.encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})

    def _finish(self, ctx: AuditContext, status: int, error: str | None) -> None:
        if status < 400:
            outcome = "success"
        elif status in (401, 403):
            outcome = "denied"
        else:
            outcome = "failure"
        if ctx.sessions:
            db, gen = ctx.sessions[0], None
        else:
            db, gen = _session(self.fastapi_app)
            ctx.sessions.append(db)
        try:
            recorder.flush_request_reads(ctx)
            recorder.record(
                db,
                action="http.request",
                after={
                    "method": ctx.client_info["method"],
                    "path": ctx.client_info["path"],
                    "route": ctx.client_info.get("route"),
                    "status": status,
                },
                outcome=outcome,
                error=error if outcome != "success" else None,
            )
        finally:
            if gen is not None:
                _close(gen)
