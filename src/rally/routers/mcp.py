"""MCP enablement and rally-mcp's plumbing.

MCP is **off by default** on every install and is switched on here, from
Settings. While it is off, the audit middleware refuses every MCP-identified
request and rally-mcp refuses every tool call (it asks ``/api/mcp/status``).
Switching it either way is an explicit audit event on top of the settings-row
change the data layer records anyway.

The two service routes (``status`` and ``auth-events``) are reachable only
with rally-mcp's service secret; the middleware enforces that before these
handlers run, so they can trust ``request`` came from rally-mcp.
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from rally.audit import alerts, recorder
from rally.audit import context as audit_context
from rally.audit.middleware import MCP_ENABLED_KEY, mcp_enabled
from rally.database import get_db
from rally.models import FamilyMember, McpConnection, Setting

router = APIRouter(tags=["mcp"])

# What rally-mcp may report. Each is a denial at the MCP edge (an Access JWT
# that failed validation, a call while disabled, an identity Rally cannot map)
# and every one alerts.
AUTH_EVENT_ACTIONS = {
    "mcp.token_validation_failed",
    "mcp.access_denied",
    "mcp.identity_unresolved",
    "mcp.disabled",
}


def _require_rally_mcp() -> None:
    ctx = audit_context.current()
    if ctx.client_info.get("via") != "rally-mcp":
        raise HTTPException(status_code=403, detail="rally-mcp service credential required")


@router.get("/api/mcp/status")
def mcp_status(db: Session = Depends(get_db)):
    """Whether MCP is enabled — rally-mcp's gate. Service credential required."""
    _require_rally_mcp()
    return {"enabled": mcp_enabled(db)}


class AuthEvent(BaseModel):
    action: str
    reason: str = Field(max_length=1000)
    email: str | None = None
    client_ip: str | None = None
    cf_ray: str | None = None
    user_agent: str | None = None
    path: str | None = None


@router.post("/api/mcp/auth-events", status_code=202)
def report_auth_event(payload: AuthEvent, db: Session = Depends(get_db)):
    """rally-mcp reports a request it refused before it ever reached Rally."""
    _require_rally_mcp()
    if payload.action not in AUTH_EVENT_ACTIONS:
        raise HTTPException(status_code=422, detail=f"Unknown auth event {payload.action!r}")
    ctx = audit_context.current()
    ctx.actor_id = payload.email or audit_context.UNKNOWN_ACTOR
    ctx.flagged = not payload.email
    ctx.client_info.update(
        {
            "client_ip": payload.client_ip,
            "cf_ray": payload.cf_ray,
            "upstream_user_agent": payload.user_agent,
            "upstream_path": payload.path,
        }
    )
    recorder.record(db, action=payload.action, outcome="denied", error=payload.reason)
    alerts.send(
        db,
        title="Rally: MCP request denied",
        message=(
            f"{payload.reason}\nActor: {payload.email or 'unknown'}\n"
            f"IP: {payload.client_ip or 'unknown'}\nPath: {payload.path or '?'}"
        ),
        request_id=ctx.request_id,
    )
    return {"recorded": True, "request_id": ctx.request_id}


class McpSettings(BaseModel):
    enabled: bool


@router.get("/api/mcp/settings")
def get_mcp_settings(db: Session = Depends(get_db)):
    """The Settings page's view: the switch and every connection Rally has seen."""
    members = {m.id: m.name for m in db.query(FamilyMember).all()}
    connections = (
        db.query(McpConnection).order_by(McpConnection.last_seen_at.desc()).limit(50).all()
    )
    return {
        "enabled": mcp_enabled(db),
        "connections": [
            {
                "id": c.id,
                "member": members.get(c.family_member_id),
                "email": c.email,
                "client": c.client,
                "first_ip": c.first_ip,
                "last_ip": c.last_ip,
                "first_seen_at": c.first_seen_at.isoformat() + "Z",
                "last_seen_at": c.last_seen_at.isoformat() + "Z",
            }
            for c in connections
        ],
    }


@router.put("/api/mcp/settings")
def put_mcp_settings(payload: McpSettings, db: Session = Depends(get_db)):
    """Switch MCP on or off. Refused for MCP callers: Claude cannot enable itself."""
    if audit_context.current().source == audit_context.SOURCE_MCP:
        raise HTTPException(status_code=403, detail="MCP cannot change its own enablement")
    was = mcp_enabled(db)
    row = db.get(Setting, MCP_ENABLED_KEY)
    value = "true" if payload.enabled else "false"
    if row:
        row.value = value
    else:
        db.add(Setting(key=MCP_ENABLED_KEY, value=value))
    db.flush()
    recorder.record(
        db,
        action="mcp.enabled" if payload.enabled else "mcp.disabled",
        entity_type="settings",
        entity_id=MCP_ENABLED_KEY,
        before={"enabled": was},
        after={"enabled": payload.enabled},
    )
    return get_mcp_settings(db)
