"""Audit Log: the investigation page, its feed, request drill-down and export.

Reading the log is itself audited — the queries here are ORM reads like any
other, so they leave ``audit.read`` events, and an export adds an explicit
``audit.export`` event naming the filters and format.
"""

import csv
import io
import json
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel
from sqlalchemy import or_
from sqlalchemy.orm import Session

from rally.audit import alerts, recorder
from rally.database import get_db
from rally.models import AuditEvent, FamilyMember, Setting
from rally.templating import templates

router = APIRouter(tags=["audit"])

EXPORT_LIMIT = 10_000
COLUMNS = (
    "id",
    "occurred_at",
    "actor_type",
    "actor_id",
    "actor_name",
    "source",
    "client_info",
    "request_id",
    "action",
    "entity_type",
    "entity_id",
    "before",
    "after",
    "mcp_tool",
    "mcp_arguments",
    "outcome",
    "error",
    "flagged",
)


class AuditFilters(BaseModel):
    start: str | None = None
    end: str | None = None
    actor: str | None = None
    source: str | None = None
    action: str | None = None
    entity_type: str | None = None
    entity_id: str | None = None
    request_id: str | None = None
    outcome: str | None = None
    flagged: bool | None = None
    include_reads: bool = True


def _filters(
    start: str | None = Query(None, description="ISO date or datetime, inclusive (UTC)"),
    end: str | None = Query(None, description="ISO date or datetime, exclusive (UTC)"),
    actor: str | None = Query(None, description="Matches actor id or name"),
    source: str | None = Query(None, pattern="^(web|api|mcp|job)$"),
    action: str | None = Query(None, description="Exact action, or a prefix ending in '.'"),
    entity_type: str | None = None,
    entity_id: str | None = None,
    request_id: str | None = None,
    outcome: str | None = Query(None, pattern="^(success|failure|denied)$"),
    flagged: bool | None = None,
    include_reads: bool = True,
) -> AuditFilters:
    return AuditFilters(**locals())


def _parse_when(value: str, field: str) -> datetime:
    try:
        return datetime.fromisoformat(value).replace(tzinfo=None)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"{field} must be an ISO date") from exc


def _query(db: Session, f: AuditFilters):
    q = db.query(AuditEvent)
    if f.start:
        q = q.filter(AuditEvent.occurred_at >= _parse_when(f.start, "start"))
    if f.end:
        q = q.filter(AuditEvent.occurred_at < _parse_when(f.end, "end"))
    if f.actor:
        like = f"%{f.actor}%"
        q = q.filter(or_(AuditEvent.actor_id.ilike(like), AuditEvent.actor_name.ilike(like)))
    if f.source:
        q = q.filter(AuditEvent.source == f.source)
    if f.action:
        if f.action.endswith("."):
            q = q.filter(AuditEvent.action.startswith(f.action))
        else:
            q = q.filter(AuditEvent.action == f.action)
    if f.entity_type:
        q = q.filter(AuditEvent.entity_type == f.entity_type)
    if f.entity_id:
        q = q.filter(AuditEvent.entity_id == f.entity_id)
    if f.request_id:
        q = q.filter(AuditEvent.request_id == f.request_id)
    if f.outcome:
        q = q.filter(AuditEvent.outcome == f.outcome)
    if f.flagged is not None:
        q = q.filter(AuditEvent.flagged == f.flagged)
    if not f.include_reads:
        q = q.filter(~AuditEvent.action.endswith(".read"))
    return q.order_by(AuditEvent.occurred_at.desc(), AuditEvent.id.desc())


def _row(e: AuditEvent) -> dict:
    return {c: getattr(e, c) for c in COLUMNS} | {"occurred_at": e.occurred_at.isoformat() + "Z"}


@router.get("/audit", response_class=HTMLResponse)
def audit_page(request: Request):
    """The Audit Log page; everything on it is fetched from the API below."""
    return templates.TemplateResponse(request, "audit.html")


@router.get("/api/audit/events")
def list_audit_events(
    f: AuditFilters = Depends(_filters),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """Reverse-chronological events matching the filters, one page at a time."""
    q = _query(db, f)
    total = q.count()
    events = q.offset(offset).limit(limit).all()
    return {"events": [_row(e) for e in events], "total": total, "limit": limit, "offset": offset}


@router.get("/api/audit/facets")
def audit_facets(db: Session = Depends(get_db)):
    """Distinct values for the page's filter dropdowns."""

    def distinct(col):
        return sorted(v for (v,) in db.query(col).distinct().all() if v)

    return {
        "actors": distinct(AuditEvent.actor_id),
        "actions": distinct(AuditEvent.action),
        "entity_types": distinct(AuditEvent.entity_type),
    }


@router.get("/api/audit/export")
def export_audit_events(
    f: AuditFilters = Depends(_filters),
    format: str = Query("csv", pattern="^(csv|json)$"),
    db: Session = Depends(get_db),
):
    """The filtered events as CSV or JSON (up to 10,000 rows)."""
    events = [_row(e) for e in _query(db, f).limit(EXPORT_LIMIT).all()]
    recorder.record(
        db,
        action="audit.export",
        after={"format": format, "filters": f.model_dump(exclude_none=True), "rows": len(events)},
    )
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    if format == "json":
        return Response(
            json.dumps(events, default=str, indent=2),
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="rally-audit-{stamp}.json"'},
        )
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=COLUMNS)
    writer.writeheader()
    for row in events:
        writer.writerow(
            {k: json.dumps(v) if isinstance(v, dict | list) else v for k, v in row.items()}
        )
    return Response(
        buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="rally-audit-{stamp}.csv"'},
    )


class AlertSettings(BaseModel):
    member_ids: list[int]
    base_url: str | None = None


@router.get("/api/audit/alerts")
def get_alert_settings(db: Session = Depends(get_db)):
    """Who gets Pushover alerts about MCP connections and denials."""
    base = db.get(Setting, alerts.BASE_URL_KEY)
    return {"member_ids": alerts.recipient_ids(db), "base_url": base.value if base else ""}


@router.put("/api/audit/alerts")
def put_alert_settings(payload: AlertSettings, db: Session = Depends(get_db)):
    known = {m.id for m in db.query(FamilyMember).all()}
    unknown = set(payload.member_ids) - known
    if unknown:
        raise HTTPException(status_code=422, detail=f"Unknown family member ids: {sorted(unknown)}")
    for key, value in (
        (alerts.RECIPIENTS_KEY, json.dumps(sorted(set(payload.member_ids)))),
        (alerts.BASE_URL_KEY, (payload.base_url or "").strip()),
    ):
        row = db.get(Setting, key)
        if row:
            row.value = value
        else:
            db.add(Setting(key=key, value=value))
    db.commit()
    return get_alert_settings(db)
