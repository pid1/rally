"""Pre-created MCP connections: the page's API, presets, and Rally's own enforcement.

rally-mcp filters tools by connection, but Rally does not rely on it: the
audit middleware checks the connection, its credential, its IP allowlist and
the tool's permission on every MCP request, so a compromised rally-mcp still
cannot exceed what a connection allows. These tests pin both the Connections
API (create, change, revoke, the token shown once) and that enforcement,
including the audit events and alerts each refusal leaves.
"""

import json
from datetime import timedelta

import pytest

from rally import connections, notifications
from rally.audit import context as audit_context
from rally.audit import mcp_policy, recorder
from rally.main import app
from rally.models import AuditEvent, Connection, McpConnection, Setting
from rally.utils.timezone import now_utc

SERVICE_TOKEN = "test-service-token-0123456789"
JON = "jon@example.com"
EMMA = "emma@example.com"
CLAUDE_IP = "160.79.104.10"
BUSY_TOOLS = {"check_availability", "find_free_slots", "book_appointment"}


def events(db, **filters):
    db.expire_all()
    q = db.query(AuditEvent)
    for key, value in filters.items():
        q = q.filter(getattr(AuditEvent, key) == value)
    return q.order_by(AuditEvent.occurred_at, AuditEvent.id).all()


@pytest.fixture
def pushes(monkeypatch):
    sent = []

    def fake_send(token, user_key, message, *, title="Rally", device=None):
        sent.append({"title": title, "message": message})

    monkeypatch.setattr(notifications, "send_pushover", fake_send)
    return sent


@pytest.fixture
def household(db_session, make_member, monkeypatch, pushes):
    """MCP on, a service secret, Jon and Emma, Jon on the alert list."""
    monkeypatch.setenv("RALLY_MCP_SERVICE_TOKEN", SERVICE_TOKEN)
    jon = make_member("Jon", email=JON, pushover_user_key="ujon")
    emma = make_member("Emma", email=EMMA)
    db_session.add_all(
        [
            Setting(key="mcp_enabled", value="true"),
            Setting(key="pushover_app_token", value="app-token"),
            Setting(key="audit_alert_member_ids", value=json.dumps([jon.id])),
        ]
    )
    db_session.commit()
    return {"jon": jon, "emma": emma}


def add_connection(db, **overrides) -> Connection:
    values = {
        "id": "c-claude",
        "name": "Claude",
        "auth_method": "oauth",
        "preset": "full",
        "ip_allowlist": ["160.79.104.0/21"],
        "status": "enabled",
    }
    values.update(overrides)
    conn = Connection(**values)
    db.add(conn)
    db.commit()
    return conn


def add_token_connection(db, member, token="rly_phone-token", **overrides) -> Connection:
    values = {
        "id": "c-phone",
        "name": "Phone agent",
        "auth_method": "token",
        "preset": "busy",
        "ip_allowlist": ["203.0.113.0/24"],
        "authorized_by": member.id,
        "token_hash": connections.hash_token(token),
        "token_hint": token[:8],
    }
    return add_connection(db, **{**values, **overrides})


def service():
    return {"X-Rally-Service-Token": SERVICE_TOKEN}


def as_mcp(tool="list_todos", connection="c-claude", email=JON, ip=CLAUDE_IP, **extra):
    headers = {
        "X-Rally-Service-Token": SERVICE_TOKEN,
        "X-Rally-Mcp-Connection-Id": connection,
        "X-Rally-Mcp-Grant-Id": "conn_grant1",
        "X-Rally-Mcp-Email": email,
        "X-Rally-Mcp-Client": "claude.ai",
        "X-Rally-Mcp-Client-Ip": ip,
        "X-Rally-Mcp-Tool": tool,
        **extra,
    }
    return {k: v for k, v in headers.items() if v is not None}


def resolve(http, **body):
    payload = {"connection_id": "c-claude", "email": JON, "client_ip": CLAUDE_IP, **body}
    response = http.post("/api/mcp/connections/resolve", json=payload, headers=service())
    assert response.status_code == 200, response.text
    return response.json()


# --- The Connections API ----------------------------------------------------------


def test_create_an_oauth_connection(client, db_session, household, pushes):
    response = client.post(
        "/api/connections",
        json={
            "name": "Claude",
            "auth_method": "oauth",
            "preset": "full",
            "ip_allowlist": ["160.79.104.0/21", " 153.61.208.0/20 "],
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "disabled"  # off until someone turns it on
    assert body["endpoint_path"] == f"/mcp/c/{body['id']}"
    assert body["ip_allowlist"] == ["160.79.104.0/21", "153.61.208.0/20"]
    assert "token" not in body
    assert set(body["tools"]) == set(mcp_policy.tool_catalog(app))
    [created] = events(db_session, action="connection.created")
    assert created.entity_id == body["id"]
    assert created.after["preset"] == "full"
    assert events(db_session, action="connection.create")  # the data layer's diff
    assert any("connection created" in p["title"] for p in pushes)


def test_a_token_is_shown_once_and_stored_hashed(client, db_session, household, pushes):
    response = client.post(
        "/api/connections",
        json={
            "name": "Phone agent",
            "auth_method": "token",
            "preset": "busy",
            "ip_allowlist": ["203.0.113.0/24"],
            "authorized_by": household["jon"].id,
            "expires_in_days": 30,
            "enabled": True,
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    token = body["token"]
    assert token.startswith("rly_") and len(token) > 40
    assert body["token_hint"] == token[:8]
    assert body["authorized_by"] == "Jon"
    assert body["expires_at"]

    row = db_session.get(Connection, body["id"])
    assert row.token_hash == connections.hash_token(token) != token
    # Never again: not in the list, and nowhere in the audit log.
    assert token not in client.get("/api/connections").text
    blob = json.dumps([[e.before, e.after, e.client_info] for e in events(db_session)])
    assert token not in blob and row.token_hash not in blob
    assert events(db_session, action="connection.token_created")
    assert any("with a token" in p["title"] for p in pushes)


def test_a_token_needs_its_creator_and_the_service_secret(client, household, monkeypatch):
    base = {"name": "Agent", "auth_method": "token", "ip_allowlist": ["203.0.113.0/24"]}
    assert client.post("/api/connections", json=base).status_code == 422
    monkeypatch.delenv("RALLY_MCP_SERVICE_TOKEN")
    response = client.post("/api/connections", json={**base, "authorized_by": household["jon"].id})
    assert response.status_code == 409


@pytest.mark.parametrize(
    "body",
    [
        {"name": "x", "auth_method": "password"},
        {"name": "x", "auth_method": "oauth", "preset": "everything"},
        {"name": "x", "auth_method": "oauth", "ip_allowlist": ["not-an-ip"]},
        {"name": "x", "auth_method": "oauth", "preset": "custom", "tools": ["rm_rf"]},
        {"name": "", "auth_method": "oauth"},
    ],
)
def test_bad_connections_are_refused(client, household, body):
    assert client.post("/api/connections", json=body).status_code == 422


def test_adjusting_a_preset_makes_it_custom(client, db_session, household):
    conn = add_connection(db_session, preset="read_only")
    catalog = mcp_policy.tool_catalog(app)
    read_only = connections.preset_tools("read_only", catalog)
    fewer = sorted(read_only - {"list_todos"})
    body = client.put(f"/api/connections/{conn.id}", json={"tools": fewer}).json()
    assert body["preset"] == "custom"
    assert "list_todos" not in body["tools"]
    # Landing exactly on a preset again names the preset.
    body = client.put(
        f"/api/connections/{conn.id}", json={"preset": "read_only", "tools": sorted(read_only)}
    ).json()
    assert body["preset"] == "read_only"
    [*_, updated] = events(db_session, action="connection.updated")
    assert updated.after["preset"] == "read_only"


def test_enable_disable_and_revoke(client, db_session, household, pushes):
    conn = add_token_connection(db_session, household["jon"], status="disabled")
    assert client.put(f"/api/connections/{conn.id}", json={"status": "enabled"}).json()[
        "status"
    ] == ("enabled")
    assert client.put(f"/api/connections/{conn.id}", json={"status": "revoked"}).status_code == 422
    revoked = client.post(f"/api/connections/{conn.id}/revoke")
    assert revoked.json()["status"] == "revoked"
    db_session.expire_all()
    assert db_session.get(Connection, conn.id).token_hash is None
    assert client.post(f"/api/connections/{conn.id}/revoke").status_code == 409
    assert client.put(f"/api/connections/{conn.id}", json={"status": "enabled"}).status_code == 409
    [event] = events(db_session, action="connection.revoked")
    assert event.after["bearer_cleared"] is True
    assert any("revoked" in p["title"] for p in pushes)


def test_the_public_url_builds_endpoint_urls(client, db_session, household):
    add_connection(db_session)
    assert (
        client.put("/api/connections/public-url", json={"public_url": "http://x"}).status_code
        == 422
    )
    client.put("/api/connections/public-url", json={"public_url": "https://rally-mcp.example.com/"})
    [conn] = client.get("/api/connections").json()["connections"]
    assert conn["endpoint_url"] == "https://rally-mcp.example.com/mcp/c/c-claude"
    assert client.put("/api/settings", json={"settings": {"mcp_public_url": "x"}}).status_code == (
        422
    )


def test_the_page_renders(client):
    response = client.get("/connections")
    assert response.status_code == 200
    assert "New Connection" in response.text


def test_ip_presets_record_their_source():
    anthropic = connections.IP_PRESETS["anthropic"]
    assert anthropic["ranges"] == ["160.79.104.0/21", "153.61.208.0/20"]
    assert anthropic["source"].startswith("https://") and anthropic["checked"]
    elevenlabs = connections.IP_PRESETS["elevenlabs"]
    assert elevenlabs["ranges"] == ["34.67.146.145/32", "34.59.11.47/32"]
    assert elevenlabs["source"].startswith("https://") and elevenlabs["checked"]


# --- MCP can never manage connections ----------------------------------------------


@pytest.mark.parametrize(
    "method, path",
    [
        ("GET", "/api/connections"),
        ("POST", "/api/connections"),
        ("PUT", "/api/connections/{connection_id}"),
        ("POST", "/api/connections/{connection_id}/revoke"),
        ("PUT", "/api/connections/public-url"),
    ],
)
def test_connections_are_denied_to_mcp(method, path):
    assert (method, path) not in mcp_policy.allowed_routes(app)
    assert mcp_policy.denial(method, path)


def test_an_mcp_caller_is_refused_the_connections_api(client, db_session, household):
    add_connection(db_session)
    response = client.post(
        "/api/connections", json={"name": "Mine", "auth_method": "oauth"}, headers=as_mcp()
    )
    assert response.status_code == 403
    assert events(db_session, action="mcp.route_not_allowed")
    assert client.get("/connections", headers=as_mcp()).status_code == 403
    assert db_session.query(Connection).count() == 1


# --- Presets and scopes ------------------------------------------------------------


def test_presets():
    catalog = mcp_policy.tool_catalog(app)
    assert connections.preset_tools("full", catalog) == set(catalog)
    read_only = connections.preset_tools("read_only", catalog)
    assert read_only and all(catalog[t].scope.endswith(":read") for t in read_only)
    assert "delete_todo" not in read_only and "list_todos" in read_only
    assert connections.preset_tools("busy", catalog) == BUSY_TOOLS


def test_every_tool_has_an_area_scope():
    for tool in mcp_policy.tool_catalog(app).values():
        area, kind = tool.scope.split(":")
        assert kind in {"read", "write", "busy"}, tool
        assert area in mcp_policy.SCOPE_LABELS, tool


@pytest.mark.parametrize(
    "method, path, scope",
    [
        ("GET", "/api/todos", "todos:read"),
        ("POST", "/api/recurring-todos", "todos:write"),
        ("GET", "/api/events", "calendar:read"),
        ("PUT", "/api/events/{event_id}", "calendar:write"),
        ("GET", "/api/meal-planner", "meals:read"),
        ("POST", "/api/shopping/items", "shopping:write"),
        ("GET", "/api/preparedness/items", "preparedness:read"),
        ("GET", "/api/packing-list-days", "packing:read"),
        ("GET", "/api/followed-teams", "sports:read"),
        ("GET", "/api/dashboard/regenerate", "dashboard:write"),
        ("GET", "/api/busy/availability", "calendar:busy"),
        ("POST", "/api/busy/appointments", "calendar:busy"),
    ],
)
def test_route_scopes(method, path, scope):
    assert mcp_policy.route_scope(method, path) == scope


def test_call_denial():
    catalog = mcp_policy.tool_catalog(app)
    # A generated tool calls exactly its own route.
    assert (
        mcp_policy.call_denial(catalog, "shopping_list_items", "GET", "/api/shopping/items") is None
    )
    assert mcp_policy.call_denial(catalog, "shopping_list_items", "POST", "/api/shopping/items")
    # A hand-written tool calls its own scope and the family lookup.
    assert mcp_policy.call_denial(catalog, "list_todos", "GET", "/api/todos") is None
    assert mcp_policy.call_denial(catalog, "list_todos", "GET", "/api/family") is None
    assert mcp_policy.call_denial(catalog, "list_todos", "DELETE", "/api/todos/{todo_id}")
    # A busy-only tool reaches only the busy routes.
    assert mcp_policy.call_denial(catalog, "check_availability", "GET", "/api/busy/x") is None
    assert mcp_policy.call_denial(catalog, "check_availability", "GET", "/api/family")
    assert mcp_policy.call_denial(catalog, "check_availability", "GET", "/api/events")
    assert mcp_policy.call_denial(catalog, "made_up", "GET", "/api/todos")


# --- Tokens -----------------------------------------------------------------------


def test_token_hashing(monkeypatch):
    monkeypatch.setenv("RALLY_MCP_SERVICE_TOKEN", "one")
    first = connections.hash_token("rly_abc")
    assert first == connections.hash_token("rly_abc") != connections.hash_token("rly_abd")
    assert len(first) == 64 and "rly_abc" not in first
    monkeypatch.setenv("RALLY_MCP_SERVICE_TOKEN", "two")
    # A new service secret is a new key: every old token stops matching.
    assert connections.hash_token("rly_abc") != first
    conn = Connection(token_hash=first)
    assert not connections.token_matches(conn, "rly_abc")
    monkeypatch.setenv("RALLY_MCP_SERVICE_TOKEN", "one")
    assert connections.token_matches(conn, "rly_abc")
    assert not connections.token_matches(conn, None)


# --- The verdict rally-mcp asks for -------------------------------------------------


def test_resolve_an_oauth_connection_binds_it_to_the_first_member(
    client, db_session, household, pushes
):
    add_connection(db_session, preset="read_only")
    verdict = resolve(client, grant_id="conn_grant1", client="claude.ai")
    assert verdict["allowed"] is True
    assert verdict["member"] == "Jon"
    assert "list_todos" in verdict["connection"]["tools"]
    assert "delete_todo" not in verdict["connection"]["tools"]
    assert "todos:read" in verdict["connection"]["scopes"]
    db_session.expire_all()
    assert db_session.get(Connection, "c-claude").authorized_by == household["jon"].id
    grant = db_session.get(McpConnection, "conn_grant1")
    assert grant.connection_id == "c-claude"
    [authorized] = events(db_session, action="mcp.connection_authorized")
    assert authorized.after["connection"] == "Claude"
    assert any("new MCP connection" in p["title"] for p in pushes)

    # Somebody else's sign-in on Jon's connection is refused.
    other = resolve(client, email=EMMA)
    assert other["allowed"] is False
    assert other["action"] == "mcp.connection_mismatch"


@pytest.mark.parametrize(
    "setup, body, action",
    [
        ({}, {"connection_id": "nobody-made-this"}, "mcp.connection_unknown"),
        ({"status": "disabled"}, {}, "mcp.connection_disabled"),
        ({"status": "revoked"}, {}, "mcp.connection_revoked"),
        ({}, {"client_ip": "198.51.100.9"}, "mcp.ip_rejected"),
        ({}, {"client_ip": None}, "mcp.ip_rejected"),
        ({"ip_allowlist": []}, {}, "mcp.ip_rejected"),
        ({}, {"email": "mallory@example.com"}, "mcp.identity_unresolved"),
        ({}, {"email": None}, "mcp.identity_unresolved"),
    ],
)
def test_resolve_refusals_are_logged_and_alerted(
    client, db_session, household, pushes, setup, body, action
):
    add_connection(db_session, **setup)
    verdict = resolve(client, **body)
    assert verdict["allowed"] is False
    assert verdict["action"] == action
    [denied] = events(db_session, action=action)
    assert denied.outcome == "denied"
    assert pushes, "every refusal alerts"


def test_unknown_legacy_path_is_refused(client, db_session, household):
    verdict = resolve(client, connection_id="legacy")
    assert verdict["action"] == "mcp.connection_unknown"


def test_resolve_while_mcp_is_off(client, db_session, household):
    add_connection(db_session)
    db_session.get(Setting, "mcp_enabled").value = "false"
    db_session.commit()
    assert resolve(client)["action"] == "mcp.disabled"


def test_resolve_needs_the_service_secret(client, household):
    response = client.post("/api/mcp/connections/resolve", json={"connection_id": "c-claude"})
    assert response.status_code == 403


def test_token_first_use_rejection_and_expiry(client, db_session, household, pushes):
    add_token_connection(db_session, household["jon"])
    ok = resolve(
        client,
        connection_id="c-phone",
        email=None,
        token="rly_phone-token",
        client_ip="203.0.113.5",
    )
    assert ok["allowed"] is True
    assert ok["connection"]["tools"] == sorted(BUSY_TOOLS)
    assert ok["connection"]["scopes"] == ["calendar:busy"]
    resolve(
        client,
        connection_id="c-phone",
        email=None,
        token="rly_phone-token",
        client_ip="203.0.113.5",
    )
    [first] = events(db_session, action="connection.token_first_used")
    assert first.after["client_ip"] == "203.0.113.5"
    assert sum("first time" in p["title"] for p in pushes) == 1

    wrong = resolve(
        client, connection_id="c-phone", email=None, token="rly_guess", client_ip="203.0.113.5"
    )
    assert wrong["status"] == 401 and wrong["action"] == "connection.token_rejected"
    assert any("token rejected" in p["title"] for p in pushes)

    db_session.get(Connection, "c-phone").expires_at = now_utc() - timedelta(minutes=1)
    db_session.commit()
    expired = resolve(
        client,
        connection_id="c-phone",
        email=None,
        token="rly_phone-token",
        client_ip="203.0.113.5",
    )
    assert expired["action"] == "connection.token_rejected"
    assert "expired" in expired["reason"]


# --- Rally's own enforcement, on every API call ---------------------------------------


def test_an_allowed_call_names_the_connection(client, db_session, household, monkeypatch):
    add_connection(db_session)
    seen = {}
    original = recorder.flush_request_reads

    def spy(ctx):
        # What a route (Phase 2's busy tools, say) reads mid-request.
        seen["connection_id"] = audit_context.current().connection_id
        ctx.result_items = 2
        return original(ctx)

    monkeypatch.setattr(recorder, "flush_request_reads", spy)
    response = client.get("/api/todos", headers=as_mcp())
    assert response.status_code == 200
    assert seen["connection_id"] == "c-claude"
    [req] = events(db_session, action="http.request")
    assert req.client_info["connection_id"] == "c-claude"
    assert req.client_info["connection_name"] == "Claude"
    assert req.client_info["client_ip"] == CLAUDE_IP
    assert req.mcp_tool == "list_todos"
    assert req.after["result_bytes"] == len(response.content)
    assert req.after["result_items"] == 2


def test_note_result_size_sets_the_current_context():
    with audit_context.job_context("test") as ctx:
        audit_context.note_result_size(7)
        assert ctx.result_items == 7


def test_a_tool_the_connection_may_not_use_is_refused(client, db_session, household, pushes):
    add_connection(db_session, preset="custom", tools=["shopping_list_items"])
    response = client.get("/api/todos", headers=as_mcp(tool="list_todos"))
    assert response.status_code == 403
    [denied] = events(db_session, action="mcp.permission_denied")
    assert "may not use the tool list_todos" in denied.error
    assert not events(db_session, action="todo.read")
    assert any("denied" in p["title"] for p in pushes)


def test_a_tool_on_a_route_not_its_own_is_refused(client, db_session, household):
    add_connection(db_session)
    response = client.get("/api/todos", headers=as_mcp(tool="shopping_list_items"))
    assert response.status_code == 403
    assert events(db_session, action="mcp.permission_denied")


def test_a_request_naming_no_tool_is_refused(client, db_session, household):
    add_connection(db_session)
    assert client.get("/api/todos", headers=as_mcp(tool=None)).status_code == 403


def test_busy_only_cannot_read_the_calendar_even_by_lying_about_the_tool(
    client, db_session, household
):
    add_token_connection(db_session, household["jon"])
    for tool, path in [
        ("check_availability", "/api/events?start=2026-11-01&end=2026-11-02"),
        ("check_availability", "/api/family"),
        ("list_events", "/api/events?start=2026-11-01&end=2026-11-02"),
    ]:
        response = client.get(
            path,
            headers=as_mcp(
                tool=tool,
                connection="c-phone",
                email=None,
                ip="203.0.113.5",
                **{"X-Rally-Mcp-Connection-Token": "rly_phone-token"},
            ),
        )
        assert response.status_code == 403, (tool, path)
    assert len(events(db_session, action="mcp.permission_denied")) == 3


def test_a_token_connection_is_checked_on_every_call(client, db_session, household):
    add_token_connection(db_session, household["jon"], preset="full")
    good = as_mcp(
        connection="c-phone",
        email=None,
        ip="203.0.113.5",
        **{"X-Rally-Mcp-Connection-Token": "rly_phone-token"},
    )
    response = client.get("/api/todos", headers=good)
    assert response.status_code == 200
    [req] = events(db_session, action="http.request")
    assert req.actor_id == "connection:c-phone"
    assert req.actor_name == "Phone agent"
    assert req.client_info["authorized_by"] == "Jon"

    forged = {**good, "X-Rally-Mcp-Connection-Token": "rly_guess"}
    assert client.get("/api/todos", headers=forged).status_code == 401
    assert events(db_session, action="connection.token_rejected")
    off_list = {**good, "X-Rally-Mcp-Client-Ip": "198.51.100.1"}
    assert client.get("/api/todos", headers=off_list).status_code == 403
    assert events(db_session, action="mcp.ip_rejected")


def test_a_compromised_rally_mcp_cannot_widen_a_connection(client, db_session, household):
    """It can claim any tool, but Rally checks the tool against the connection."""
    add_connection(db_session, preset="read_only")
    response = client.delete("/api/todos/1", headers=as_mcp(tool="delete_todo"))
    assert response.status_code == 403
    response = client.delete("/api/todos/1", headers=as_mcp(tool="list_todos"))
    assert response.status_code == 403


def test_an_old_rally_mcp_is_served_on_the_legacy_connection(client, db_session, household):
    add_connection(db_session, id="legacy", name="Claude (legacy /mcp)", legacy=True)
    headers = as_mcp(connection="conn_0123abcd", **{"X-Rally-Mcp-Grant-Id": None})
    headers.pop("X-Rally-Mcp-Grant-Id", None)
    response = client.get("/api/todos", headers=headers)
    assert response.status_code == 200
    assert db_session.get(McpConnection, "conn_0123abcd").connection_id == "legacy"
    # Any household member may use the legacy connection; it is never bound.
    assert (
        client.get("/api/todos", headers=as_mcp(connection="legacy", email=EMMA)).status_code == 200
    )
    db_session.expire_all()
    assert db_session.get(Connection, "legacy").authorized_by is None
