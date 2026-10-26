"""Nothing reaches a tool without a valid Access JWT, and never while MCP is disabled."""

import json

import httpx
import pytest
from conftest import AUD, EMAIL, make_jwt, mcp_post

LIST_TOOLS = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}


def _reports(rally):
    return [json.loads(r.content) for r in rally.calls("/api/mcp/auth-events")]


def test_a_valid_jwt_lists_the_tools(server, rally):
    response = mcp_post(server.http, LIST_TOOLS)
    assert response.status_code == 200, response.text
    names = {t["name"] for t in response.json()["result"]["tools"]}
    assert {"list_todos", "delete_todo", "list_events", "get_daily_summary"} <= names
    assert not _reports(rally)


@pytest.mark.parametrize(
    "token, reason",
    [
        (None, "Missing"),
        (make_jwt(aud=["someone-else"]), "Audience"),
        (make_jwt(iss="https://evil.cloudflareaccess.com"), "issuer"),
        (make_jwt(exp=1), "expired"),
        (make_jwt(email=None), "no email"),
        ("not-a-jwt", "rejected"),
    ],
)
def test_bad_tokens_are_refused_and_reported(server, rally, token, reason):
    response = mcp_post(server.http, LIST_TOOLS, token=token)
    assert response.status_code == 401
    [report] = _reports(rally)
    assert report["action"] == "mcp.token_validation_failed"
    assert reason.lower() in report["reason"].lower()
    assert report["client_ip"] == "160.79.104.10"
    assert report["cf_ray"] == "8f0000000000-DFW"
    # The report authenticates as rally-mcp, and carries no identity it could not verify.
    sent = rally.calls("/api/mcp/auth-events")[0]
    assert sent.headers["X-Rally-Service-Token"] == "svc-secret"
    assert not any(k.lower().startswith("x-rally-mcp-") for k in sent.headers)


def test_disabled_refuses_everything_and_reports_who(server, rally):
    rally.enabled = False
    response = mcp_post(server.http, LIST_TOOLS)
    assert response.status_code == 403
    [report] = _reports(rally)
    assert report["action"] == "mcp.disabled"
    assert report["email"] == EMAIL


def test_rally_unreachable_fails_closed(server, rally):
    """No answer from Rally about enablement is treated as "disabled"."""

    def down(request):
        raise httpx.ConnectError("rally is down", request=request)

    rally.handler = down
    assert mcp_post(server.http, LIST_TOOLS).status_code == 403


def test_only_the_mcp_path_is_served(server):
    assert server.http.get("/", headers={"Cf-Access-Jwt-Assertion": make_jwt()}).status_code == 404
    assert server.http.get("/healthz").json() == {"ok": True}


def test_audience_constant_matches_fixture():
    assert AUD == "test-aud"
