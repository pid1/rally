"""The Connections page: pre-created MCP clients, their permissions, allowlists and tokens.

People only. Every route here is in ``mcp_policy.DENIED`` (so the audit
middleware refuses an MCP caller before a handler runs) and refuses an MCP
caller again itself: Claude cannot create, widen or re-enable a connection.

Creating, changing and revoking a connection are recorded twice: the data
layer's ``connection.create`` / ``connection.update`` diffs (the token hash
redacted by name), and explicit ``connection.created`` / ``connection.updated``
/ ``connection.revoked`` / ``connection.token_created`` events, each alerted.
A token is returned once, in the response that created it, and never again.
"""

from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from rally import connections
from rally.audit import alerts, mcp_policy, recorder
from rally.audit import context as audit_context
from rally.audit.middleware import mcp_enabled
from rally.database import get_db
from rally.models import Connection, FamilyMember, McpConnection, Setting
from rally.templating import templates
from rally.utils.timezone import now_utc

router = APIRouter(tags=["connections"])

PUBLIC_URL_KEY = "mcp_public_url"


def _refuse_mcp() -> None:
    if audit_context.current().source == audit_context.SOURCE_MCP:
        raise HTTPException(status_code=403, detail="MCP cannot manage its own connections")


def _public_url(db: Session) -> str:
    row = db.get(Setting, PUBLIC_URL_KEY)
    return (row.value or "").strip().rstrip("/") if row else ""


def _iso(value) -> str | None:
    return value.isoformat() + "Z" if value else None


def _view(db: Session, conn: Connection, catalog, members: dict[int, str]) -> dict:
    sign_ins = (
        db.query(McpConnection)
        .filter(McpConnection.connection_id == conn.id)
        .order_by(McpConnection.last_seen_at.desc())
        .limit(20)
        .all()
    )
    path = connections.endpoint_path(conn)
    base = _public_url(db)
    expired = bool(
        conn.expires_at and conn.expires_at.replace(tzinfo=None) <= now_utc().replace(tzinfo=None)
    )
    return {
        "id": conn.id,
        "name": conn.name,
        "auth_method": conn.auth_method,
        "preset": conn.preset,
        "tools": sorted(connections.permitted_tools(conn, catalog)),
        "ip_allowlist": list(conn.ip_allowlist or []),
        "status": conn.status,
        "legacy": conn.legacy,
        "authorized_by": members.get(conn.authorized_by) if conn.authorized_by else None,
        "authorized_by_id": conn.authorized_by,
        "endpoint_path": path,
        "endpoint_url": f"{base}{path}" if base else None,
        "token_hint": conn.token_hint,
        "expires_at": _iso(conn.expires_at),
        "expired": expired,
        "first_used_at": _iso(conn.first_used_at),
        "last_seen_at": _iso(conn.last_seen_at),
        "last_ip": conn.last_ip,
        "created_at": _iso(conn.created_at),
        "revoked_at": _iso(conn.revoked_at),
        "sign_ins": [
            {
                "member": members.get(g.family_member_id) or g.email,
                "client": g.client,
                "first_ip": g.first_ip,
                "last_ip": g.last_ip,
                "first_seen_at": _iso(g.first_seen_at),
                "last_seen_at": _iso(g.last_seen_at),
            }
            for g in sign_ins
        ],
    }


def _alert(db: Session, title: str, message: str) -> None:
    alerts.send(db, title=title, message=message, request_id=audit_context.current().request_id)


def _resolve_permissions(preset: str, tools: list[str] | None, catalog) -> tuple[str, list | None]:
    """A preset as given, or ``custom`` with the explicit list when it differs."""
    if preset not in connections.PRESETS:
        raise HTTPException(status_code=422, detail=f"Unknown preset {preset!r}")
    if tools is None:
        if preset == "custom":
            return "custom", []
        return preset, None
    unknown = sorted(set(tools) - set(catalog))
    if unknown:
        raise HTTPException(status_code=422, detail=f"Unknown tools: {', '.join(unknown)}")
    chosen = set(tools)
    if preset != "custom" and chosen == connections.preset_tools(preset, catalog):
        return preset, None
    return "custom", sorted(chosen)


def _allowlist(entries: list[str]) -> list[str]:
    try:
        return connections.parse_allowlist(entries)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/connections", response_class=HTMLResponse)
def connections_page(request: Request):
    """Serve the Connections page."""
    _refuse_mcp()
    return templates.TemplateResponse(request, "connections.html")


@router.get("/api/connections")
def list_connections(request: Request, db: Session = Depends(get_db)):
    """Every connection, the tool catalog, the presets, and the public URL."""
    _refuse_mcp()
    catalog = mcp_policy.tool_catalog(request.app)
    members = {m.id: m.name for m in db.query(FamilyMember).all()}
    rows = db.query(Connection).order_by(Connection.legacy.desc(), Connection.created_at).all()
    return {
        "mcp_enabled": mcp_enabled(db),
        "public_url": _public_url(db),
        "presets": [
            {
                "id": key,
                "label": label,
                "tools": sorted(connections.preset_tools(key, catalog)) if key != "custom" else [],
            }
            for key, label in connections.PRESETS.items()
        ],
        "ip_presets": [{"id": key, **value} for key, value in connections.IP_PRESETS.items()],
        "catalog": [
            {"name": t.name, "scope": t.scope, "label": t.label, "summary": t.summary}
            for t in sorted(catalog.values(), key=lambda t: (t.label, t.scope, t.name))
        ],
        "members": [{"id": mid, "name": name} for mid, name in sorted(members.items())],
        "connections": [_view(db, c, catalog, members) for c in rows],
    }


class ConnectionCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    auth_method: str
    preset: str = "custom"
    tools: list[str] | None = None
    ip_allowlist: list[str] = Field(default_factory=list)
    enabled: bool = False
    # Token connections: who is creating the token (Rally has no login, so
    # the form asks), and how long it lasts; None is until revoked.
    authorized_by: int | None = None
    expires_in_days: int | None = Field(default=None, ge=1, le=3650)


@router.post("/api/connections", status_code=201)
def create_connection(payload: ConnectionCreate, request: Request, db: Session = Depends(get_db)):
    """Create a connection. A token connection's token is in this response, and only here."""
    _refuse_mcp()
    if payload.auth_method not in connections.AUTH_METHODS:
        raise HTTPException(status_code=422, detail="auth_method must be 'oauth' or 'token'")
    catalog = mcp_policy.tool_catalog(request.app)
    preset, tools = _resolve_permissions(payload.preset, payload.tools, catalog)
    allowlist = _allowlist(payload.ip_allowlist)
    members = {m.id: m.name for m in db.query(FamilyMember).all()}

    conn = Connection(
        id=connections.new_connection_id(),
        name=payload.name.strip(),
        auth_method=payload.auth_method,
        preset=preset,
        tools=tools,
        ip_allowlist=allowlist,
        status="enabled" if payload.enabled else "disabled",
        legacy=False,
    )
    token = None
    if payload.auth_method == "token":
        if payload.authorized_by not in members:
            raise HTTPException(
                status_code=422, detail="Choose the household member creating this token"
            )
        try:
            token = connections.new_token()
            conn.token_hash = connections.hash_token(token)
        except connections.TokenKeyMissing as exc:
            raise HTTPException(
                status_code=409,
                detail="Set RALLY_MCP_SERVICE_TOKEN on Rally before creating a token connection",
            ) from exc
        conn.token_hint = token[:8]
        conn.authorized_by = payload.authorized_by
        if payload.expires_in_days:
            conn.expires_at = now_utc() + timedelta(days=payload.expires_in_days)
    db.add(conn)
    db.flush()
    summary = {
        "name": conn.name,
        "auth_method": conn.auth_method,
        "preset": conn.preset,
        "tools": sorted(connections.permitted_tools(conn, catalog)),
        "ip_allowlist": allowlist,
        "status": conn.status,
    }
    recorder.record(
        db,
        commit=False,
        action="connection.created",
        entity_type="connections",
        entity_id=conn.id,
        after=summary,
    )
    if token:
        recorder.record(
            db,
            commit=False,
            action="connection.token_created",
            entity_type="connections",
            entity_id=conn.id,
            after={
                "connection": conn.name,
                "authorized_by": members[conn.authorized_by],
                "expires": _iso(conn.expires_at),
            },
        )
    db.commit()
    _alert(
        db,
        "Rally: MCP connection created" + (" with a token" if token else ""),
        f"Connection {conn.name!r} ({conn.auth_method}, {connections.PRESETS[conn.preset]}) "
        f"was created, {conn.status}.\nAllowed from: {', '.join(allowlist) or 'nowhere'}"
        + (f"\nToken created by {members[conn.authorized_by]}." if token else ""),
    )
    body = _view(db, conn, catalog, members)
    if token:
        body["token"] = token
    return body


class ConnectionUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    preset: str | None = None
    tools: list[str] | None = None
    ip_allowlist: list[str] | None = None
    status: str | None = None  # enabled | disabled; revoking has its own route


class PublicUrl(BaseModel):
    public_url: str = Field(default="", max_length=300)


@router.put("/api/connections/public-url")
def put_public_url(payload: PublicUrl, db: Session = Depends(get_db)):
    """rally-mcp's public address, so the page can show each connection's full URL."""
    _refuse_mcp()
    value = payload.public_url.strip().rstrip("/")
    if value and not value.startswith("https://"):
        raise HTTPException(status_code=422, detail="The public URL must start with https://")
    row = db.get(Setting, PUBLIC_URL_KEY)
    if row:
        row.value = value
    else:
        db.add(Setting(key=PUBLIC_URL_KEY, value=value))
    db.commit()
    return {"public_url": value}


def _get(db: Session, connection_id: str) -> Connection:
    conn = db.get(Connection, connection_id)
    if conn is None:
        raise HTTPException(status_code=404, detail="Connection not found")
    return conn


@router.put("/api/connections/{connection_id}")
def update_connection(
    connection_id: str, payload: ConnectionUpdate, request: Request, db: Session = Depends(get_db)
):
    """Rename, change permissions or the allowlist, enable or disable."""
    _refuse_mcp()
    conn = _get(db, connection_id)
    if conn.status == "revoked":
        raise HTTPException(status_code=409, detail="A revoked connection cannot be changed")
    catalog = mcp_policy.tool_catalog(request.app)
    before = {
        "name": conn.name,
        "preset": conn.preset,
        "tools": sorted(connections.permitted_tools(conn, catalog)),
        "ip_allowlist": list(conn.ip_allowlist or []),
        "status": conn.status,
    }
    if payload.name is not None:
        conn.name = payload.name.strip()
    if payload.preset is not None or payload.tools is not None:
        conn.preset, conn.tools = _resolve_permissions(
            payload.preset or conn.preset, payload.tools, catalog
        )
    if payload.ip_allowlist is not None:
        conn.ip_allowlist = _allowlist(payload.ip_allowlist)
    if payload.status is not None:
        if payload.status not in ("enabled", "disabled"):
            raise HTTPException(
                status_code=422, detail="status must be 'enabled' or 'disabled'; revoke instead"
            )
        conn.status = payload.status
    after = {
        "name": conn.name,
        "preset": conn.preset,
        "tools": sorted(connections.permitted_tools(conn, catalog)),
        "ip_allowlist": list(conn.ip_allowlist or []),
        "status": conn.status,
    }
    changed = {k for k in after if after[k] != before[k]}
    db.flush()
    if changed:
        recorder.record(
            db,
            commit=False,
            action="connection.updated",
            entity_type="connections",
            entity_id=conn.id,
            before={k: before[k] for k in changed},
            after={k: after[k] for k in changed},
        )
    db.commit()
    if changed:
        lines = []
        if "status" in changed:
            lines.append(f"Now {conn.status}.")
        if {"preset", "tools"} & changed:
            lines.append(
                f"Tools: {len(before['tools'])} -> {len(after['tools'])} "
                f"({connections.PRESETS[conn.preset]})."
            )
        if "ip_allowlist" in changed:
            lines.append(f"Allowed from: {', '.join(after['ip_allowlist']) or 'nowhere'}.")
        if "name" in changed:
            lines.append(f"Renamed from {before['name']!r}.")
        _alert(db, "Rally: MCP connection changed", f"Connection {conn.name!r}: " + " ".join(lines))
    members = {m.id: m.name for m in db.query(FamilyMember).all()}
    return _view(db, conn, catalog, members)


@router.post("/api/connections/{connection_id}/revoke")
def revoke_connection(connection_id: str, request: Request, db: Session = Depends(get_db)):
    """Revoke for good: the endpoint stops working and the token is forgotten."""
    _refuse_mcp()
    conn = _get(db, connection_id)
    if conn.status == "revoked":
        raise HTTPException(status_code=409, detail="Already revoked")
    was = conn.status
    conn.status = "revoked"
    conn.revoked_at = now_utc()
    had_token = conn.token_hash is not None
    conn.token_hash = None
    db.flush()
    recorder.record(
        db,
        commit=False,
        action="connection.revoked",
        entity_type="connections",
        entity_id=conn.id,
        before={"status": was},
        after={"status": "revoked", "bearer_cleared": had_token},
    )
    db.commit()
    _alert(
        db,
        "Rally: MCP connection revoked",
        f"Connection {conn.name!r} was revoked"
        + (", and its token no longer works." if had_token else "."),
    )
    catalog = mcp_policy.tool_catalog(request.app)
    members = {m.id: m.name for m in db.query(FamilyMember).all()}
    return _view(db, conn, catalog, members)
