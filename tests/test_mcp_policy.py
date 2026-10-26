"""What MCP may reach: every /api route except a reasoned denylist.

Pins the policy in ``rally.audit.mcp_policy`` from both ends: the routes the
middleware lets an MCP identity call, and the OpenAPI document rally-mcp
generates its tools from. They must agree, or Claude would see tools that
Rally then refuses (or, worse, Rally would accept calls no tool describes).
"""

import json

import pytest

from rally.audit import mcp_policy
from rally.main import app
from rally.models import Setting

SERVICE_TOKEN = "test-service-token-0123456789"
JON = "jon@example.com"


@pytest.fixture
def mcp_on(db_session, make_member, monkeypatch):
    monkeypatch.setenv("RALLY_MCP_SERVICE_TOKEN", SERVICE_TOKEN)
    member = make_member("Jon", email=JON, pushover_user_key="u-secret-key")
    db_session.add(Setting(key="mcp_enabled", value="true"))
    db_session.commit()
    return member


def as_mcp(**extra):
    return {
        "X-Rally-Service-Token": SERVICE_TOKEN,
        "X-Rally-Mcp-Email": JON,
        "X-Rally-Mcp-Connection-Id": "conn-test",
        **extra,
    }


def service():
    return {"X-Rally-Service-Token": SERVICE_TOKEN}


def test_household_features_are_all_allowed():
    allowed = mcp_policy.allowed_routes(app)
    for route in [
        ("GET", "/api/shopping/items"),
        ("POST", "/api/shopping/items"),
        ("GET", "/api/preparedness/items"),
        ("PUT", "/api/preparedness/items/{item_id}"),
        ("GET", "/api/notes"),
        ("POST", "/api/notes"),
        ("GET", "/api/meal-planner"),
        ("GET", "/api/packing-list-days"),
        ("POST", "/api/recurring-todos"),
        ("PUT", "/api/events/{event_id}"),
        ("DELETE", "/api/events/{event_id}"),
        ("GET", "/api/family"),
    ]:
        assert route in allowed, route


@pytest.mark.parametrize(
    "route",
    [
        ("GET", "/api/settings"),
        ("PUT", "/api/settings"),
        ("GET", "/api/settings/llm/config"),
        ("POST", "/api/settings/test-pushover"),
        ("GET", "/api/calendars"),
        ("GET", "/api/calendars/{cal_id}"),
        ("PUT", "/api/family/{member_id}"),
        ("POST", "/api/family"),
        ("GET", "/api/devices"),
        ("GET", "/api/audit/events"),
        ("PUT", "/api/mcp/settings"),
    ],
)
def test_secrets_identity_and_controls_are_denied(route):
    assert route not in mcp_policy.allowed_routes(app)
    assert mcp_policy.denial(*route)


def test_every_allowed_route_is_a_tool_or_hand_written(client, mcp_on):
    spec = client.get("/api/mcp/openapi", headers=service()).json()
    generated = {(m.upper(), p) for p, ops in spec["paths"].items() for m in ops}
    assert generated | mcp_policy.HAND_WRITTEN == mcp_policy.allowed_routes(app)
    assert not generated & mcp_policy.HAND_WRITTEN


def test_tool_names_are_unique_and_valid(client, mcp_on):
    spec = client.get("/api/mcp/openapi", headers=service()).json()
    names = [op["operationId"] for ops in spec["paths"].values() for op in ops.values()]
    assert len(names) == len(set(names))
    assert all(len(n) <= 64 and n.replace("_", "").isalnum() for n in names)
    assert "shopping_list_items" in names
    assert "prep_list_items" in names


def test_annotations_and_untrusted_flags(client, mcp_on):
    spec = client.get("/api/mcp/openapi", headers=service()).json()
    ops = spec["paths"]
    assert ops["/api/shopping/items"]["get"]["x-rally-mcp"]["annotations"]["readOnlyHint"] is True
    assert (
        ops["/api/notes/{note_id}"]["delete"]["x-rally-mcp"]["annotations"]["destructiveHint"]
        is True
    )
    reset = ops["/api/packing-list-days/{day_id}/reset"]["post"]["x-rally-mcp"]
    assert reset["annotations"]["destructiveHint"] is True
    assert ops["/api/preparedness/review"]["post"]["x-rally-mcp"]["untrusted"] is True
    assert ops["/api/notes"]["get"]["x-rally-mcp"]["untrusted"] is False
    notify = ops["/api/events/{event_id}/notify"]["post"]["x-rally-mcp"]["annotations"]
    assert notify["openWorldHint"] is True


def test_the_spec_needs_the_service_credential(client, mcp_on):
    assert client.get("/api/mcp/openapi").status_code == 403


def test_mcp_can_use_a_household_route(client, db_session, mcp_on):
    created = client.post(
        "/api/notes", json={"date": "2026-10-27", "body": "Pack swim bag"}, headers=as_mcp()
    )
    assert created.status_code in (200, 201), created.text


def test_mcp_is_refused_a_denied_route(client, mcp_on):
    response = client.get("/api/settings", headers=as_mcp())
    assert response.status_code == 403
    assert "not an MCP route" in response.json()["detail"]


def test_mcp_sees_no_pushover_keys(client, mcp_on):
    members = client.get("/api/family", headers=as_mcp()).json()
    assert "u-secret-key" not in json.dumps(members)
    # Rally's own UI still sees them.
    assert "u-secret-key" in client.get("/api/family").text


def test_rally_mcp_fixture_is_current():
    """rally-mcp's tests use a snapshot of this document; it must match Rally."""
    import importlib.util
    import pathlib

    script = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "export_mcp_openapi.py"
    spec = importlib.util.spec_from_file_location("export_mcp_openapi", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.OUT.read_text() == module.render(), (
        "mcp/tests/fixtures/rally-mcp-openapi.json is stale: run "
        "`uv run python scripts/export_mcp_openapi.py`"
    )
