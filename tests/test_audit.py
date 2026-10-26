"""The audit log: every read and write, from every source, with who did it.

Acceptance tests for ``rally.audit``. The route-by-route guarantee (no endpoint
ships without producing an audit event) lives in ``test_audit_coverage.py``;
this file pins what the events *say*: actor, source, action, entity, diffs,
identity, redaction, append-only storage, and the MCP identity and enablement
rules.
"""

import json

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DatabaseError

from rally import notifications
from rally.audit import context as audit_context
from rally.audit import identity
from rally.models import AuditEvent, Calendar, McpConnection, Setting, Todo

SERVICE_TOKEN = "test-service-token-0123456789"
JON = "jon@example.com"


def events(db, **filters):
    db.expire_all()
    q = db.query(AuditEvent)
    for key, value in filters.items():
        q = q.filter(getattr(AuditEvent, key) == value)
    return q.order_by(AuditEvent.occurred_at, AuditEvent.id).all()


def request_events(db, response):
    return events(db, request_id=response.headers["x-request-id"])


@pytest.fixture
def pushes(monkeypatch):
    sent = []

    def fake_send(token, user_key, message, *, title="Rally", device=None):
        sent.append({"token": token, "user": user_key, "title": title, "message": message})

    monkeypatch.setattr(notifications, "send_pushover", fake_send)
    return sent


@pytest.fixture
def mcp_ready(db_session, make_member, monkeypatch, pushes):
    """MCP enabled, a service secret configured, Jon on the alert list."""
    monkeypatch.setenv("RALLY_MCP_SERVICE_TOKEN", SERVICE_TOKEN)
    jon = make_member("Jon", email=JON, pushover_user_key="ujon")
    db_session.add_all(
        [
            Setting(key="mcp_enabled", value="true"),
            Setting(key="pushover_app_token", value="app-token"),
            Setting(key="audit_alert_member_ids", value=json.dumps([jon.id])),
        ]
    )
    db_session.commit()
    return jon


def mcp_headers(**overrides):
    headers = {
        "X-Rally-Service-Token": SERVICE_TOKEN,
        "X-Rally-Mcp-Email": JON,
        "X-Rally-Mcp-Jwt-Sub": "sub-123",
        "X-Rally-Mcp-Connection-Id": "conn-abc",
        "X-Rally-Mcp-Client": "claude.ai",
        "X-Rally-Mcp-Client-Ip": "160.79.104.10",
        "X-Rally-Mcp-Cf-Ray": "8f00000000000000-DFW",
        "X-Rally-Mcp-Tool": "todos_list",
        "X-Rally-Mcp-Arguments": json.dumps({"limit": 5}),
    }
    headers.update(overrides)
    return {k: v for k, v in headers.items() if v is not None}


# --- Writes --------------------------------------------------------------------


def test_create_update_delete_each_leave_a_diff(client, db_session):
    created = client.post("/api/todos", json={"title": "Call the plumber"})
    assert created.status_code == 201
    todo_id = str(created.json()["id"])

    [create] = [e for e in request_events(db_session, created) if e.action == "todo.create"]
    assert create.entity_type == "todos"
    assert create.entity_id == todo_id
    assert create.after["title"] == "Call the plumber"
    assert create.before is None
    assert create.source == "api"
    assert create.outcome == "success"

    updated = client.put(f"/api/todos/{todo_id}", json={"title": "Call the plumber today"})
    [update] = [e for e in request_events(db_session, updated) if e.action == "todo.update"]
    assert update.before["title"] == "Call the plumber"
    assert update.after["title"] == "Call the plumber today"
    # A diff, not the whole row.
    assert "description" not in update.after

    deleted = client.delete(f"/api/todos/{todo_id}")
    [delete] = [e for e in request_events(db_session, deleted) if e.action == "todo.delete"]
    assert delete.before["title"] == "Call the plumber today"
    assert delete.after is None


def test_entity_history_is_every_change_to_one_row(client, db_session):
    todo_id = client.post("/api/todos", json={"title": "A"}).json()["id"]
    client.put(f"/api/todos/{todo_id}", json={"title": "B"})
    client.put(f"/api/todos/{todo_id}", json={"completed": True})

    history = client.get(
        "/api/audit/events",
        params={"entity_type": "todos", "entity_id": todo_id, "include_reads": "false"},
    ).json()["events"]
    assert [e["action"] for e in history] == ["todo.update", "todo.update", "todo.create"]


def test_every_request_gets_a_request_event_and_an_id(client, db_session):
    response = client.get("/api/todos")
    rows = request_events(db_session, response)
    [req] = [e for e in rows if e.action == "http.request"]
    assert req.after == {
        "method": "GET",
        "path": "/api/todos",
        "route": "/api/todos",
        "status": 200,
    }
    assert req.client_info["user_agent"]
    assert req.client_info["ip"] == "testclient"


def test_a_failed_change_is_still_logged(client, db_session):
    response = client.put("/api/todos/999999", json={"title": "nope"})
    assert response.status_code == 404
    [req] = [e for e in request_events(db_session, response) if e.action == "http.request"]
    assert req.outcome == "failure"
    assert "not found" in req.error.lower()


def test_a_rolled_back_change_leaves_no_change_event(client, db_session):
    """The change and its audit row commit together, or not at all."""
    todo = Todo(title="Kept")
    db_session.add(todo)
    db_session.flush()
    db_session.rollback()
    assert not events(db_session, action="todo.create")


def test_bulk_updates_are_logged(db_session):
    db_session.add(Todo(title="x"))
    db_session.commit()
    db_session.query(Todo).update({Todo.title: "y"})
    db_session.commit()
    [bulk] = events(db_session, action="todo.bulk_update")
    assert bulk.after["rowcount"] == 1


# --- Reads ---------------------------------------------------------------------


def test_reads_are_logged_with_the_ids_returned(client, db_session, make_todo):
    first = make_todo("one", completed=False, completed_at=None)
    second = make_todo("two", completed=False, completed_at=None)
    response = client.get("/api/todos")
    [read] = [e for e in request_events(db_session, response) if e.action == "todo.read"]
    assert read.entity_type == "todos"
    assert set(read.after["ids"]) >= {str(first.id), str(second.id)}


def test_reading_the_audit_log_is_itself_audited(client, db_session):
    client.post("/api/todos", json={"title": "anything"})
    response = client.get("/api/audit/events")
    assert any(e.action == "audit.read" for e in request_events(db_session, response))


def test_export_is_audited_in_both_formats(client, db_session):
    client.post("/api/todos", json={"title": "anything"})
    csv_resp = client.get("/api/audit/export", params={"format": "csv", "source": "api"})
    assert csv_resp.status_code == 200
    assert csv_resp.text.splitlines()[0].startswith("id,occurred_at")
    json_resp = client.get("/api/audit/export", params={"format": "json"})
    assert isinstance(json_resp.json(), list)
    exports = events(db_session, action="audit.export")
    assert [e.after["format"] for e in exports] == ["csv", "json"]
    assert exports[0].after["filters"]["source"] == "api"


# --- Jobs ------------------------------------------------------------------------


def test_job_writes_are_attributed_to_the_job(db_session):
    with audit_context.job_context("rally.generator"):
        db_session.add(Todo(title="from a job"))
        db_session.commit()
    [create] = events(db_session, action="todo.create")
    assert (create.source, create.actor_type, create.actor_id) == ("job", "job", "rally.generator")


def test_job_reads_are_written_when_the_session_closes(db_session):
    from sqlalchemy.orm import sessionmaker

    from rally.database import AuditedSession

    job_session = sessionmaker(bind=db_session.get_bind(), class_=AuditedSession)()
    with audit_context.job_context("rally.notifications"):
        job_session.query(Todo).all()
        job_session.close()
    [read] = events(db_session, action="todo.read")
    assert read.actor_id == "rally.notifications"


# --- Tailnet identity -------------------------------------------------------------


def test_unknown_tailnet_identity_is_served_and_flagged(client, db_session):
    response = client.get("/api/todos")
    assert response.status_code == 200
    [req] = [e for e in request_events(db_session, response) if e.action == "http.request"]
    assert req.actor_id == "unknown"
    assert req.flagged is True


def test_tailscale_serve_headers_from_a_trusted_proxy(client, db_session, make_member, monkeypatch):
    monkeypatch.setenv("RALLY_TRUSTED_PROXIES", "testclient")
    make_member("Sarah", email="sarah@example.com")
    response = client.get(
        "/api/todos",
        headers={
            "Tailscale-User-Login": "sarah@example.com",
            "Tailscale-User-Name": "Sarah F",
        },
    )
    [req] = [e for e in request_events(db_session, response) if e.action == "http.request"]
    assert req.actor_id == "sarah@example.com"
    assert req.actor_name == "Sarah"
    assert req.flagged is False
    assert req.client_info["identity"]["via"] == "tailscale-serve"


def test_tailscale_headers_from_anywhere_else_are_ignored(client, db_session):
    response = client.get("/api/todos", headers={"Tailscale-User-Login": "mallory@example.com"})
    [req] = [e for e in request_events(db_session, response) if e.action == "http.request"]
    assert req.actor_id == "unknown"


def test_tailscale_whois_identity(client, db_session, monkeypatch):
    monkeypatch.setattr(
        identity,
        "whois",
        lambda addr: {
            "login": "jon@example.com",
            "display_name": "Jon",
            "node_name": "jons-mbp",
            "node_id": "nABC",
            "tailnet_ip": addr,
            "os": "macOS",
            "device": "jons-mbp",
        },
    )
    response = client.get("/api/todos")
    [req] = [e for e in request_events(db_session, response) if e.action == "http.request"]
    assert req.actor_id == "jon@example.com"
    assert req.client_info["identity"]["node_name"] == "jons-mbp"


def test_a_tagged_device_is_recorded_by_its_node_name(client, db_session, monkeypatch):
    monkeypatch.setattr(
        identity,
        "whois",
        lambda addr: {
            "login": "tagged-devices",
            "display_name": "Tagged Devices",
            "node_name": "jons-laptop.example-tailnet.ts.net.",
            "node_id": "nEXAMPLE",
            "tailnet_ip": addr,
            "os": "macOS",
            "device": "jons-laptop",
        },
    )
    response = client.get("/api/todos")
    [req] = [e for e in request_events(db_session, response) if e.action == "http.request"]
    assert req.actor_id == "device:jons-laptop"
    assert req.actor_name == "jons-laptop"
    assert req.flagged is False


# --- MCP identity ---------------------------------------------------------------


def test_mcp_call_is_attributed_to_the_household_member(client, db_session, mcp_ready):
    response = client.get("/api/todos", headers=mcp_headers())
    assert response.status_code == 200
    rows = request_events(db_session, response)
    [req] = [e for e in rows if e.action == "http.request"]
    assert req.source == "mcp"
    assert req.actor_type == "mcp_client"
    assert req.actor_id == JON
    assert req.actor_name == "Jon"
    assert req.mcp_tool == "todos_list"
    assert req.mcp_arguments == {"limit": 5}
    assert req.client_info["connection_id"] == "conn-abc"
    assert req.client_info["mcp_client"] == "claude.ai"
    assert req.client_info["client_ip"] == "160.79.104.10"
    assert req.client_info["cf_ray"] == "8f00000000000000-DFW"
    assert req.client_info["jwt_subject"] == "sub-123"


def test_first_sighting_of_a_connection_is_an_authorization_and_an_alert(
    client, db_session, mcp_ready, pushes
):
    client.get("/api/todos", headers=mcp_headers())
    client.get("/api/todos", headers=mcp_headers())
    [authorized] = events(db_session, action="mcp.connection_authorized")
    assert authorized.after["member"] == "Jon"
    assert db_session.get(McpConnection, "conn-abc").email == JON
    assert len(pushes) == 1
    assert "new MCP connection" in pushes[0]["title"]
    assert "/audit?request_id=" in pushes[0]["message"]
    [sent] = events(db_session, action="alert.sent")
    assert sent.after["recipient"] == "Jon"
    # The recipient's key and the app token never appear in the log.
    blob = json.dumps(
        [e.after for e in events(db_session)] + [e.client_info for e in events(db_session)]
    )
    assert "ujon" not in blob and "app-token" not in blob


def test_upstream_request_id_is_kept_end_to_end(client, db_session, mcp_ready):
    rid = audit_context.new_request_id()
    response = client.get("/api/todos", headers=mcp_headers(**{"X-Rally-Mcp-Request-Id": rid}))
    assert response.headers["x-request-id"] == rid


@pytest.mark.parametrize(
    "headers, action",
    [
        (mcp_headers(**{"X-Rally-Service-Token": None}), "mcp.service_auth_failed"),
        (mcp_headers(**{"X-Rally-Service-Token": "forged"}), "mcp.service_auth_failed"),
        (mcp_headers(**{"X-Rally-Mcp-Email": "mallory@example.com"}), "mcp.identity_unresolved"),
        (mcp_headers(**{"X-Rally-Mcp-Email": None}), "mcp.identity_unresolved"),
    ],
)
def test_bad_mcp_claims_are_denied_logged_and_alerted(
    client, db_session, mcp_ready, pushes, headers, action
):
    response = client.get("/api/todos", headers=headers)
    assert response.status_code == 403
    [denied] = events(db_session, action=action)
    assert denied.outcome == "denied"
    assert denied.source == "mcp"
    assert pushes and "denied" in pushes[-1]["title"]
    # Nothing was read on a refused request.
    assert not events(db_session, action="todo.read")


def test_a_forged_mcp_identity_from_the_tailnet_is_refused(
    client, db_session, make_member, monkeypatch
):
    """No service secret configured at all: any MCP claim is refused."""
    monkeypatch.delenv("RALLY_MCP_SERVICE_TOKEN", raising=False)
    make_member("Jon", email=JON)
    response = client.get("/api/todos", headers={"X-Rally-Mcp-Email": JON})
    assert response.status_code == 403
    assert events(db_session, action="mcp.service_auth_failed")


def test_mcp_is_refused_while_disabled(client, db_session, mcp_ready):
    db_session.get(Setting, "mcp_enabled").value = "false"
    db_session.commit()
    response = client.get("/api/todos", headers=mcp_headers())
    assert response.status_code == 403
    assert events(db_session, action="mcp.disabled")


def test_mcp_may_only_use_its_own_routes(client, db_session, mcp_ready):
    response = client.get("/api/settings", headers=mcp_headers())
    assert response.status_code == 403
    assert events(db_session, action="mcp.route_not_allowed")
    response = client.put("/api/mcp/settings", json={"enabled": False}, headers=mcp_headers())
    assert response.status_code == 403


def test_status_needs_the_service_secret(client, db_session, mcp_ready):
    assert client.get("/api/mcp/status").status_code == 403
    ok = client.get("/api/mcp/status", headers={"X-Rally-Service-Token": SERVICE_TOKEN})
    assert ok.json() == {"enabled": True}


def test_rally_mcp_reports_its_own_denials(client, db_session, mcp_ready, pushes):
    response = client.post(
        "/api/mcp/auth-events",
        headers={"X-Rally-Service-Token": SERVICE_TOKEN},
        json={
            "action": "mcp.token_validation_failed",
            "reason": "Access JWT audience mismatch",
            "client_ip": "203.0.113.9",
            "path": "/mcp",
        },
    )
    assert response.status_code == 202
    [denied] = events(db_session, action="mcp.token_validation_failed")
    assert denied.outcome == "denied"
    assert denied.flagged is True
    assert denied.client_info["client_ip"] == "203.0.113.9"
    assert pushes


# --- Enablement ----------------------------------------------------------------


def test_mcp_is_off_by_default(client):
    assert client.get("/api/mcp/settings").json()["enabled"] is False


def test_enabling_and_disabling_are_audit_events(client, db_session):
    client.put("/api/mcp/settings", json={"enabled": True})
    client.put("/api/mcp/settings", json={"enabled": False})
    assert [e.action for e in events(db_session) if e.action.startswith("mcp.")] == [
        "mcp.enabled",
        "mcp.disabled",
    ]


def test_generic_settings_cannot_flip_mcp(client):
    response = client.put("/api/settings", json={"settings": {"mcp_enabled": "true"}})
    assert response.status_code == 422


# --- Secrets ---------------------------------------------------------------------


def test_credentials_are_redacted(client, db_session, make_member):
    member = make_member("Jon")
    client.post(
        "/api/calendars",
        json={
            "label": "iCloud",
            "url": "https://caldav.icloud.com",
            "family_member_id": member.id,
            "cal_type": "caldav_apple",
            "username": "jon@example.com",
            "password": "app-specific-secret",
        },
    )
    client.put("/api/settings", json={"settings": {"pushover_app_token": "super-secret"}})
    blob = json.dumps([[e.before, e.after, e.client_info] for e in events(db_session)], default=str)
    assert "app-specific-secret" not in blob
    assert "super-secret" not in blob
    [cal] = events(db_session, action="calendar.create")
    assert cal.after["password"] == "[redacted]"
    assert db_session.query(Calendar).one().password == "app-specific-secret"


def test_secret_mcp_arguments_are_redacted(client, db_session, mcp_ready):
    client.get(
        "/api/todos",
        headers=mcp_headers(**{"X-Rally-Mcp-Arguments": json.dumps({"api_key": "k", "q": 1})}),
    )
    [req] = [e for e in events(db_session, action="http.request") if e.source == "mcp"]
    assert req.mcp_arguments == {"api_key": "[redacted]", "q": 1}


# --- Storage --------------------------------------------------------------------


@pytest.mark.parametrize(
    "statement",
    ["UPDATE audit_events SET action = 'x'", "DELETE FROM audit_events"],
)
def test_audit_rows_are_append_only(client, db_session, statement):
    client.post("/api/todos", json={"title": "anything"})
    with pytest.raises(DatabaseError, match="append-only"):
        db_session.execute(text(statement))
    db_session.rollback()


# --- Alert list -------------------------------------------------------------------


def test_alert_list_round_trip_and_validation(client, make_member):
    member = make_member("Jon", pushover_user_key="u1")
    assert client.put("/api/audit/alerts", json={"member_ids": [999]}).status_code == 422
    saved = client.put(
        "/api/audit/alerts", json={"member_ids": [member.id], "base_url": "http://rally.ts.net/"}
    ).json()
    assert saved == {"member_ids": [member.id], "base_url": "http://rally.ts.net/"}


def test_alert_delivery_failures_are_logged(client, db_session, mcp_ready, monkeypatch):
    def broken(*args, **kwargs):
        raise notifications.PushoverError("invalid user key")

    monkeypatch.setattr(notifications, "send_pushover", broken)
    client.get("/api/todos", headers=mcp_headers(**{"X-Rally-Service-Token": "forged"}))
    [failed] = events(db_session, action="alert.failed")
    assert failed.error == "invalid user key"


# --- Member email and summary ---------------------------------------------------


def test_member_email_is_stored_normalized(client):
    created = client.post("/api/family", json={"name": "Jon", "email": " Jon@Example.COM "})
    assert created.json()["email"] == "jon@example.com"


def test_dashboard_summary_as_data(client):
    body = client.get("/api/dashboard/summary").json()
    assert set(body) == {"date", "generated_at", "summary", "daily_note"}
