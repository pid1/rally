"""Connections: path binding, credentials, IP allowlists and per-tool permissions.

Rally holds the connections and judges each request (``FakeRally.resolve``
stands in for it here); these tests pin rally-mcp's half: that it names the
connection from the URL path alone, believes ``CF-Connecting-IP`` only from
the tunnel, sends the right credential, refuses what Rally refuses, and shows
and runs only the tools a connection may use — even a tool called by name.
"""

import asyncio
import json

import httpx
import pytest
from conftest import EMAIL, SPEC, TOKEN, build_server, make_jwt, mcp_post
from starlette.testclient import TestClient

from rally_mcp import generated
from rally_mcp.scopes import SCOPES, declare, scoped
from rally_mcp.server import TrustedProxies

LIST_TOOLS = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}


def call(name, arguments=None):
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments or {}},
    }


def resolves(rally):
    return [json.loads(r.content) for r in rally.calls("/api/mcp/connections/resolve")]


def reports(rally):
    return [json.loads(r.content) for r in rally.calls("/api/mcp/auth-events")]


def tool_names(response):
    assert response.status_code == 200, response.text
    return {t["name"] for t in response.json()["result"]["tools"]}


# --- Scopes ----------------------------------------------------------------------


def test_every_served_tool_has_a_scope(server):
    scopes = {**SCOPES, **generated.tool_scopes(SPEC)}
    served = {t.name for t in asyncio.run(server.mcp.list_tools(run_middleware=False))}
    assert served <= set(scopes), sorted(served - set(scopes))
    assert all(":" in scopes[name] for name in served)


def test_hand_written_scopes_match_rallys(server):
    """Rally enforces the same scopes, so the two declarations must agree."""
    rally_side = SPEC["x-rally-mcp-hand-written"]
    for name, scope in SCOPES.items():
        assert rally_side.get(name) == scope, name


def test_scoped_declares_by_function_name():
    @scoped("notes:read")
    async def a_tool_for_this_test():
        return None

    assert SCOPES.pop("a_tool_for_this_test") == "notes:read"
    with pytest.raises(ValueError):
        declare("list_todos", "todos:write")
    with pytest.raises(ValueError):
        declare("x", "nonsense")


# --- Path binding ---------------------------------------------------------------


def test_the_path_names_the_connection(server, rally):
    response = mcp_post(server.http, call("list_todos"), path="/mcp/c/c-claude")
    assert response.status_code == 200, response.text
    [asked] = resolves(rally)
    assert asked["connection_id"] == "c-claude"
    assert asked["email"] == EMAIL
    assert asked["grant_id"].startswith("conn_")
    assert asked["via_tunnel"] is True
    for request in rally.calls("/api/todos"):
        assert request.headers["X-Rally-Mcp-Connection-Id"] == "c-claude"


def test_legacy_mcp_is_the_legacy_connection(server, rally):
    mcp_post(server.http, LIST_TOOLS)
    assert resolves(rally)[0]["connection_id"] == "legacy"


def test_an_unknown_connection_is_refused(server, rally):
    response = mcp_post(server.http, LIST_TOOLS, path="/mcp/c/nobody-made-this")
    assert response.status_code == 403
    assert resolves(rally)[0]["connection_id"] == "nobody-made-this"
    assert not rally.calls("/api/todos")


@pytest.mark.parametrize("path", ["/mcp/c/", "/mcp/c/a/b", "/mcp/c/..%2f", "/mcpx", "/mcp/x/c"])
def test_paths_that_name_no_connection_are_not_found(server, rally, path):
    assert mcp_post(server.http, LIST_TOOLS, path=path).status_code == 404
    assert not resolves(rally)


def test_a_disabled_connection_is_refused(server, rally):
    assert mcp_post(server.http, LIST_TOOLS, path="/mcp/c/c-off").status_code == 403


def test_rally_unreachable_for_a_verdict_fails_closed(server, rally):
    handler = rally.handler

    def no_resolve(request):
        if request.url.path == "/api/mcp/connections/resolve":
            raise httpx.ConnectError("down", request=request)
        return handler(request)

    rally.handler = no_resolve
    response = mcp_post(server.http, LIST_TOOLS, path="/mcp/c/c-claude")
    assert response.status_code == 503


# --- Tokens --------------------------------------------------------------------


def test_a_token_connection_works_with_its_bearer_token(server, rally):
    response = mcp_post(
        server.http, LIST_TOOLS, path="/mcp/c/c-token", bearer=TOKEN, client_ip="203.0.113.7"
    )
    assert tool_names(response) == {"list_events"}
    [asked] = resolves(rally)
    assert asked["token"] == TOKEN
    assert asked["email"] is None

    rally.requests.clear()
    result = mcp_post(
        server.http,
        call("list_events", {"start": "2026-10-27"}),
        path="/mcp/c/c-token",
        bearer=TOKEN,
        client_ip="203.0.113.7",
    )
    assert result.status_code == 200
    [sent] = rally.calls("/api/events")
    # Rally checks the token again on every call; there is no person to name.
    assert sent.headers["X-Rally-Mcp-Connection-Id"] == "c-token"
    assert sent.headers["X-Rally-Mcp-Connection-Token"] == TOKEN
    assert "X-Rally-Mcp-Email" not in sent.headers


def test_a_wrong_token_is_refused(server, rally):
    response = mcp_post(
        server.http, LIST_TOOLS, path="/mcp/c/c-token", bearer="rly_wrong", client_ip="203.0.113.7"
    )
    assert response.status_code == 401
    assert response.headers["www-authenticate"].startswith("Bearer")


def test_no_credential_at_all_is_refused_before_rally_is_asked(server, rally):
    response = mcp_post(server.http, LIST_TOOLS, token=None, path="/mcp/c/c-token")
    assert response.status_code == 401
    assert not resolves(rally)
    [report] = reports(rally)
    assert report["connection_id"] == "c-token"


def test_with_an_access_jwt_the_authorization_header_is_not_a_token(server, rally):
    """Behind Access, Authorization carries Access's own OAuth token, not ours."""
    mcp_post(server.http, LIST_TOOLS, path="/mcp/c/c-claude", token=make_jwt(), bearer="x")
    assert resolves(rally)[0]["token"] is None


# --- IP allowlist ------------------------------------------------------------------


def test_an_address_off_the_allowlist_is_refused(server, rally):
    response = mcp_post(server.http, LIST_TOOLS, path="/mcp/c/c-claude", client_ip="198.51.100.9")
    assert response.status_code == 403
    assert resolves(rally)[0]["client_ip"] == "198.51.100.9"


def test_a_spoofed_cf_connecting_ip_on_a_direct_request_is_ignored(rally):
    """Not from cloudflared: the header is ignored and the peer's own address used."""
    _, app = build_server(rally, trusted=())
    with TestClient(app) as http:
        response = mcp_post(http, LIST_TOOLS, path="/mcp/c/c-claude", client_ip="160.79.104.10")
    assert response.status_code == 403
    [asked] = resolves(rally)
    assert asked["client_ip"] == "testclient"
    assert asked["via_tunnel"] is False


def test_trusted_proxies_by_name_and_range():
    calls = []

    def resolver(name, port):
        calls.append(name)
        return [(2, 1, 6, "", ("172.20.0.3", 0))]

    trusted = TrustedProxies(["rally-mcp-cloudflared", "10.9.0.0/16"], resolver=resolver)
    assert trusted.trusts_sync("172.20.0.3")
    assert trusted.trusts_sync("10.9.1.2")
    assert not trusted.trusts_sync("172.20.0.4")
    assert not trusted.trusts_sync(None)
    assert calls.count("rally-mcp-cloudflared") == 1  # cached

    def broken(name, port):
        raise OSError("no such host")

    assert not TrustedProxies(["rally-mcp-cloudflared"], resolver=broken).trusts_sync("1.2.3.4")
    assert not TrustedProxies([]).trusts_sync("172.20.0.3")


# --- Permissions ---------------------------------------------------------------------


def test_tools_list_is_filtered_to_the_connection(server):
    names = tool_names(mcp_post(server.http, LIST_TOOLS, path="/mcp/c/c-shopping"))
    assert names == {"shopping_list_items"}
    everything = tool_names(mcp_post(server.http, LIST_TOOLS, path="/mcp/c/c-claude"))
    assert {"list_todos", "shopping_list_items", "delete_todo"} <= everything


@pytest.mark.parametrize("name", ["delete_todo", "list_todos", "shopping_create_item", "nope"])
def test_a_tool_it_cannot_see_cannot_be_called_by_name(server, rally, name):
    response = mcp_post(server.http, call(name, {"todo_id": 10}), path="/mcp/c/c-shopping")
    assert response.status_code == 200
    body = response.json()
    refused = body.get("error") or body["result"]
    assert "error" in body or refused.get("isError"), body
    # Nothing reached Rally's API, and Rally heard about the attempt.
    assert not [r for r in rally.requests if not r.url.path.startswith("/api/mcp/")]
    [report] = reports(rally)
    assert report["action"] == "mcp.permission_denied"
    assert report["tool"] == name
    assert report["connection_id"] == "c-shopping"
    assert report["email"] == EMAIL


def test_a_permitted_tool_runs(server, rally):
    response = mcp_post(server.http, call("shopping_list_items"), path="/mcp/c/c-shopping")
    assert response.status_code == 200
    assert not response.json()["result"].get("isError")
    [sent] = rally.calls("/api/shopping/items")
    assert sent.headers["X-Rally-Mcp-Tool"] == "shopping_list_items"
    assert sent.headers["X-Rally-Mcp-Connection-Id"] == "c-shopping"


def test_a_scope_rally_did_not_grant_is_refused_even_if_listed(server, rally):
    """Rally's tool list and its scopes must both allow a tool."""
    rally.connections["c-shopping"]["tools"] = ["shopping_list_items", "list_todos"]
    original = rally.resolve

    def narrower(body):
        verdict = original(body)
        verdict["connection"]["scopes"] = ["shopping:read"]
        return verdict

    rally.resolve = narrower
    names = tool_names(mcp_post(server.http, LIST_TOOLS, path="/mcp/c/c-shopping"))
    assert names == {"shopping_list_items"}
