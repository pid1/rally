"""The MCP surface Claude sees, end to end through FastMCP's in-memory client."""

import asyncio

import pytest
from conftest import SETTINGS, brief
from fastmcp import Client
from fastmcp.exceptions import ToolError

from rally_phone.calls import Calls
from rally_phone.server import build


@pytest.fixture
def server(client):
    return build(lambda: Calls(client, SETTINGS))


def run(coro):
    return asyncio.run(coro)


async def _tools(server):
    async with Client(server) as c:
        return {t.name: t for t in await c.list_tools()}


async def _call(server, name, args):
    async with Client(server) as c:
        return await c.call_tool(name, args)


def test_tool_list_and_hints(server):
    tools = run(_tools(server))
    assert set(tools) == {
        "place_call",
        "schedule_call",
        "get_call",
        "list_recent_calls",
        "cancel_scheduled_call",
    }
    for name in ("place_call", "schedule_call"):
        a = tools[name].annotations
        assert (
            a.read_only_hint is False and a.idempotent_hint is False and a.open_world_hint is True
        )
    assert tools["get_call"].annotations.read_only_hint is True
    # The brief schema is strict: unknown fields are rejected.
    schema = tools["place_call"].input_schema
    assert "brief" in schema["required"]


def test_place_call_through_mcp(server, api):
    api.route(
        "POST",
        "/v1/convai/twilio/outbound-call",
        {"success": True, "message": "ok", "conversation_id": "conv_9", "callSid": "CA9"},
    )
    res = run(_call(server, "place_call", {"brief": brief()}))
    assert res.structured_content["call_id"] == "conv_9"


def test_bad_brief_is_a_tool_error_and_dials_nothing(server, api):
    with pytest.raises(ToolError, match="nothing was dialled"):
        run(_call(server, "place_call", {"brief": brief(goal="Account 123456789")}))
    with pytest.raises(ToolError):
        run(_call(server, "place_call", {"brief": brief(to_number="5125550123")}))
    with pytest.raises(ToolError):
        run(_call(server, "place_call", {}))
    assert api.requests == []


def test_unconfigured_server_says_how_to_set_up():
    server = build()
    with pytest.raises(ToolError, match="setup.py"):
        run(_call(server, "list_recent_calls", {}))
