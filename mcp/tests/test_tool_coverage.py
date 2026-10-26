"""No MCP tool ships unaudited.

Every registered tool must have sample arguments here, and calling it must
send Rally the service secret plus the tool's own name and the acting
identity on *every* Rally request it makes — which is what makes Rally record
the call under the household member and connection. A new tool without an
entry fails ``test_every_tool_has_a_sample``.

It also pins the safety annotations: exactly the destructive tools are marked
destructive, and every tool has a description and a typed input schema.
"""

import asyncio
import json

import pytest
from conftest import EMAIL, call_tool

SAMPLE_ARGS = {
    "list_todos": {},
    "create_todo": {"title": "x"},
    "update_todo": {"todo_id": 10, "title": "y"},
    "complete_todo": {"todo_id": 10},
    "delete_todo": {"todo_id": 10},
    "list_events": {"start": "2026-10-27"},
    "create_event": {"title": "x", "start": "2026-10-27T10:00"},
    "get_daily_summary": {},
}
DESTRUCTIVE = {"delete_todo"}
READ_ONLY = {"list_todos", "list_events", "get_daily_summary"}


def _tools(server):
    return asyncio.run(server.mcp.list_tools())


def test_every_tool_has_a_sample(server):
    assert {t.name for t in _tools(server)} == set(SAMPLE_ARGS)


def test_annotations_and_schemas(server):
    for tool in _tools(server):
        assert tool.description and len(tool.description) > 20, tool.name
        assert tool.parameters["type"] == "object", tool.name
        assert tool.annotations.destructive_hint is (tool.name in DESTRUCTIVE), tool.name
        assert tool.annotations.read_only_hint is (tool.name in READ_ONLY), tool.name


@pytest.mark.parametrize("name", sorted(SAMPLE_ARGS))
def test_every_tool_call_is_attributed(server, rally, name):
    response = call_tool(server.http, name, SAMPLE_ARGS[name])
    assert response.status_code == 200, response.text
    assert not response.json()["result"].get("isError"), response.json()
    sent = [r for r in rally.requests if not r.url.path.startswith("/api/mcp/")]
    assert sent, f"{name} made no Rally request"
    for request in sent:
        assert request.headers["X-Rally-Service-Token"] == "svc-secret"
        assert request.headers["X-Rally-Mcp-Tool"] == name
        assert request.headers["X-Rally-Mcp-Email"] == EMAIL
        if "X-Rally-Mcp-Arguments" in request.headers:
            logged = json.loads(request.headers["X-Rally-Mcp-Arguments"])
            assert set(logged) <= set(SAMPLE_ARGS[name]) | {
                "limit",
                "offset",
                "include_completed_today",
                "all_day",
                "completed",
            }, logged
