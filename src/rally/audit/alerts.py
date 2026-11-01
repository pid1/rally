"""Pushover alerts for the events that matter: new MCP connections and denials.

The main threat to a household's data through MCP is exfiltration, not
deletion — deletes are allowed and the nightly backup covers them — so what an
admin needs to hear about is *access*: every MCP connection authorized (rare
enough that each one is worth a buzz) and every MCP request refused.

Recipients are an explicit alert list in Settings (``audit_alert_member_ids``),
each reached at their own Pushover key and optional device, using the
install's Pushover application token. Sending an alert, and any delivery
failure, is itself an audit event. Keys and tokens are never logged: they are
redacted by name, and nothing here puts them in an event.
"""

from __future__ import annotations

import json

from sqlalchemy.orm import Session

from rally import notifications
from rally.audit import recorder
from rally.models import FamilyMember, Setting

RECIPIENTS_KEY = "audit_alert_member_ids"
BASE_URL_KEY = "rally_base_url"


def recipient_ids(db: Session) -> list[int]:
    row = db.get(Setting, RECIPIENTS_KEY)
    if not row:
        return []
    try:
        ids = json.loads(row.value)
    except ValueError:
        return []
    return [int(i) for i in ids if str(i).isdigit()]


def audit_link(db: Session, request_id: str) -> str:
    row = db.get(Setting, BASE_URL_KEY)
    base = (row.value or "").rstrip("/") if row else ""
    return f"{base}/audit?request_id={request_id}" if base else f"/audit?request_id={request_id}"


def send(db: Session, *, title: str, message: str, request_id: str) -> None:
    """Notify every alert-list member; log each delivery and failure."""
    link = audit_link(db, request_id)
    body = f"{message}\n\nAudit log: {link}"
    token = notifications.app_token(db)
    ids = recipient_ids(db)
    members = db.query(FamilyMember).filter(FamilyMember.id.in_(ids)).all() if ids else []
    if not members:
        recorder.record(
            db,
            action="alert.skipped",
            after={"title": title, "reason": "no alert recipients configured"},
        )
        return
    for member in members:
        key = (member.pushover_user_key or "").strip()
        outcome, error = "success", None
        if not token:
            outcome, error = "failure", "No Pushover application token configured"
        elif not key:
            outcome, error = "failure", "Recipient has no Pushover key"
        else:
            try:
                notifications.send_pushover(
                    token,
                    key,
                    body,
                    title=title,
                    device=(member.pushover_device or "").strip() or None,
                )
            except notifications.PushoverError as exc:
                outcome, error = "failure", str(exc)
        recorder.record(
            db,
            action="alert.sent" if outcome == "success" else "alert.failed",
            entity_type="family_members",
            entity_id=str(member.id),
            after={"title": title, "recipient": member.name, "link": link},
            outcome=outcome,
            error=error,
        )
