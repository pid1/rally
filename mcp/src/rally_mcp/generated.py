"""Tools for everything else in Rally, generated from the OpenAPI document Rally serves.

Rally decides what Claude may reach (``rally.audit.mcp_policy``) and serves
exactly those routes at ``/api/mcp/openapi``, each with a tool name,
annotations and an untrusted-content flag. rally-mcp turns every operation
into a tool with FastMCP's OpenAPI support, so a new Rally endpoint becomes a
Claude tool with no change here — and a denied one never appears.

Three things wrap the generated tools:

- **Identity on every request.** An httpx event hook removes anything the
  MCP caller sent that Rally treats as identity or credentials (FastMCP
  forwards the incoming request's headers), then sets rally-mcp's own: the
  service secret and who is acting, exactly as the hand-written tools do.
- **Tool tagging.** ``CallTagger`` records which tool is running and its
  arguments, so Rally's audit log names the tool for every request it makes.
- **Untrusted results.** For operations Rally flags, the whole result is
  returned under ``untrusted_external_content`` with the usual warning.
"""

from __future__ import annotations

import json
import time

import httpx
import httpx2
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.middleware import Middleware
from fastmcp.tools.base import ToolResult
from mcp.types import TextContent, ToolAnnotations

from rally_mcp.rally import current_call, identity_headers
from rally_mcp.tools import UNTRUSTED_KEY, UNTRUSTED_NOTE

EXTENSION = "x-rally-mcp"
# Headers an MCP caller must never get to set on a request to Rally.
STRIPPED_PREFIXES = ("x-rally-", "cf-access-", "authorization", "cookie")


def fetch_spec(
    rally_url: str, service_token: str, *, attempts: int = 30, delay: float = 2.0
) -> dict:
    """Rally's MCP OpenAPI document, waiting for Rally to come up if it must."""
    last: Exception | None = None
    for _ in range(attempts):
        try:
            response = httpx.get(
                f"{rally_url}/api/mcp/openapi",
                headers={"X-Rally-Service-Token": service_token},
                timeout=10,
            )
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError) as exc:
            last = exc
            time.sleep(delay)
    raise RuntimeError(f"Could not fetch Rally's MCP OpenAPI document: {last}")


def untrusted_tools(spec: dict) -> set[str]:
    return {
        op["operationId"]
        for ops in spec.get("paths", {}).values()
        for op in ops.values()
        if op.get(EXTENSION, {}).get("untrusted")
    }


def _set_headers(request) -> None:
    for name in list(request.headers.keys()):
        if name.lower().startswith(STRIPPED_PREFIXES):
            del request.headers[name]
    for name, value in identity_headers(current_call.get()).items():
        request.headers[name] = value


def build_client(rally_url: str, service_token: str, transport=None) -> httpx2.AsyncClient:
    async def on_request(request) -> None:
        _set_headers(request)
        request.headers["X-Rally-Service-Token"] = service_token

    return httpx2.AsyncClient(
        base_url=rally_url,
        timeout=30.0,
        event_hooks={"request": [on_request]},
        transport=transport,
    )


def _pass_rally_refusals(component) -> None:
    """Let Rally's own refusals through the generated server's error masking.

    "Not found" or a validation error is what Claude needs to correct the
    call; any other failure stays masked.
    """
    run = component.run

    async def run_with_refusals(arguments):
        try:
            return await run(arguments)
        except httpx2.HTTPStatusError as exc:
            raise ToolError(f"Rally refused this request: {exc}") from exc

    object.__setattr__(component, "run", run_with_refusals)


def _component(route, component) -> None:
    if hasattr(component, "run"):
        _pass_rally_refusals(component)
    meta = (route.extensions or {}).get(EXTENSION, {})
    hints = meta.get("annotations")
    if hints and hasattr(component, "annotations"):
        component.annotations = ToolAnnotations(**hints)
    if meta.get("untrusted") and hasattr(component, "output_schema"):
        # The result is re-wrapped under untrusted_external_content, so it no
        # longer matches Rally's schema; publish none rather than a wrong one.
        component.output_schema = None
        component.description = f"{component.description or ''}\n\n{UNTRUSTED_NOTE}".strip()


def build(spec: dict, client: httpx2.AsyncClient) -> FastMCP:
    server = FastMCP.from_openapi(
        spec,
        client=client,
        name="Rally (generated)",
        mcp_component_fn=_component,
        mask_error_details=True,
    )
    return server


class CallTagger(Middleware):
    """Names the running tool on every Rally request; wraps untrusted results."""

    def __init__(self, untrusted: set[str]) -> None:
        self.untrusted = untrusted

    async def on_call_tool(self, context, call_next) -> ToolResult:
        params = context.message
        call = current_call.get()
        if call is not None:
            call.tool = params.name
            call.arguments = {k: v for k, v in (params.arguments or {}).items() if v is not None}
        result = await call_next(context)
        if params.name not in self.untrusted or result.is_error:
            return result
        payload = result.structured_content
        if payload is None:
            payload = "\n".join(c.text for c in result.content if isinstance(c, TextContent))
        wrapped = {UNTRUSTED_KEY: payload, "note": UNTRUSTED_NOTE}
        return ToolResult(
            content=[TextContent(type="text", text=json.dumps(wrapped, default=str))],
            structured_content=wrapped,
        )
