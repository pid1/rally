"""No MCP tool ships unaudited — hand-written or generated.

Every tool rally-mcp serves is called here: the hand-written ones with the
sample arguments below, and every tool generated from Rally's OpenAPI
document with placeholder arguments built from its own input schema. Each
call must send Rally the service secret plus the tool's name and the acting
identity on every Rally request it makes, which is what makes Rally record
the call under the household member and connection. A new hand-written tool
without a sample fails ``test_every_hand_written_tool_has_a_sample``; a new
Rally endpoint is covered automatically.

It also pins the safety annotations: the hand-written tools' are fixed here,
and every generated tool must carry exactly what Rally's policy said.
"""

import asyncio
import json

import pytest
from conftest import EMAIL, SPEC, call_tool

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

GENERATED = {
    op["operationId"]: op["x-rally-mcp"] for ops in SPEC["paths"].values() for op in ops.values()
}


def _tools(server):
    return {t.name: t for t in asyncio.run(server.mcp.list_tools())}


def _placeholder(schema: dict):
    schema = {**schema}
    for key in ("anyOf", "oneOf"):
        if key in schema:
            options = [o for o in schema[key] if o.get("type") != "null"]
            schema = options[0] if options else {"type": "null"}
    if "enum" in schema:
        return schema["enum"][0]
    kind = schema.get("type")
    fmt = schema.get("format", "")
    pattern = schema.get("pattern", "")
    if kind == "integer":
        return max(1, schema.get("minimum", 1))
    if kind == "number":
        return 1
    if kind == "boolean":
        return True
    if kind == "array":
        return []
    if kind == "object":
        return {}
    if fmt == "date" or "\\d{4}-\\d{2}-\\d{2}" in pattern:
        return "2026-10-27"
    if fmt == "date-time":
        return "2026-10-27T10:00:00"
    return "x"


def _arguments(tool) -> dict:
    params = tool.parameters
    props = params.get("properties", {})
    return {name: _placeholder(props[name]) for name in params.get("required", [])}


def test_every_hand_written_tool_has_a_sample(server):
    names = set(_tools(server))
    assert set(SAMPLE_ARGS) <= names
    assert names == set(SAMPLE_ARGS) | set(GENERATED), (
        "a tool is neither hand-written nor in Rally's spec"
    )


def test_generated_tools_cover_rally_beyond_todos_and_events(server):
    names = set(_tools(server))
    for expected in [
        "shopping_list_items",
        "prep_list_items",
        "list_notes",
        "create_meal_plan",
        "packing_list_days",
    ]:
        assert expected in names


def test_annotations_and_schemas(server):
    for name, tool in _tools(server).items():
        assert tool.description and len(tool.description) > 10, name
        assert tool.parameters["type"] == "object", name
        if name in SAMPLE_ARGS:
            assert tool.annotations.destructive_hint is (name in DESTRUCTIVE), name
            assert tool.annotations.read_only_hint is (name in READ_ONLY), name
        else:
            hints = GENERATED[name]["annotations"]
            assert tool.annotations.destructive_hint is hints["destructiveHint"], name
            assert tool.annotations.read_only_hint is hints["readOnlyHint"], name


ALL_TOOLS = sorted(set(SAMPLE_ARGS) | set(GENERATED))


@pytest.mark.parametrize("name", ALL_TOOLS)
def test_every_tool_call_is_attributed(server, rally, name):
    tool = _tools(server)[name]
    arguments = SAMPLE_ARGS.get(name) or _arguments(tool)
    response = call_tool(server.http, name, arguments)
    assert response.status_code == 200, response.text
    body = response.json()
    assert "error" not in body, body
    assert not body["result"].get("isError"), body["result"]
    sent = [r for r in rally.requests if not r.url.path.startswith("/api/mcp/")]
    assert sent, f"{name} made no Rally request"
    for request in sent:
        assert request.headers["X-Rally-Service-Token"] == "svc-secret"
        assert request.headers["X-Rally-Mcp-Tool"] == name
        assert request.headers["X-Rally-Mcp-Email"] == EMAIL
        assert "Cf-Access-Jwt-Assertion" not in request.headers
        if "X-Rally-Mcp-Arguments" in request.headers:
            logged = json.loads(request.headers["X-Rally-Mcp-Arguments"])
            assert "rally" not in logged
