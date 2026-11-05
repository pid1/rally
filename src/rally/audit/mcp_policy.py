"""What Claude may do in Rally through MCP: everything, minus a short list of exceptions.

Every ``/api`` route is an MCP route unless it is listed in ``DENIED`` below,
with the reason. The same policy produces both halves of the contract:

- the audit middleware refuses an MCP-identified request for any route
  outside ``allowed_routes()`` — so a compromised rally-mcp cannot reach them;
- ``/api/mcp/openapi`` serves rally-mcp the OpenAPI document for exactly the
  allowed routes, from which rally-mcp generates one tool per operation, named,
  annotated and flagged here.

So a new endpoint is a new Claude tool the moment it is registered, unless it
is deliberately denied.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass

from rally.audit.middleware import MCP_SERVICE_ROUTES, iter_routes


@dataclass(frozen=True)
class Rule:
    pattern: str  # regex matched against the route template
    methods: frozenset[str] | None  # None means every method
    reason: str

    def matches(self, method: str, path: str) -> bool:
        return (self.methods is None or method in self.methods) and re.fullmatch(
            self.pattern, path
        ) is not None


WRITES = frozenset({"POST", "PUT", "PATCH", "DELETE"})

# Routes MCP never reaches, and why. Each is about secrets, identity or the
# controls around Claude itself — never about hiding household data.
DENIED = (
    Rule(r"/api/mcp/.*", None, "Claude cannot see or change its own gate"),
    Rule(r"/api/audit/.*", None, "the audit log is for people to read, not Claude (spec: v1)"),
    Rule(r"/api/settings", None, "raw settings hold API keys and the Pushover token in plaintext"),
    Rule(r"/api/settings/llm/.*", None, "LLM settings hold API keys"),
    Rule(r"/api/settings/test-.*", None, "connectivity tests use stored credentials"),
    Rule(r"/api/calendars(/.*)?", None, "calendars hold CalDAV passwords and private feed URLs"),
    Rule(
        r"/api/family(/.*)?",
        WRITES,
        "family members carry Pushover keys and the sign-in emails MCP identity is resolved by",
    ),
    Rule(r"/api/devices(/.*)?", None, "device ids are the browser's own identity tokens"),
    Rule(
        r"/api/connections(/.*)?",
        None,
        "connections are MCP's own credentials, permissions and allowlists",
    ),
)

# Served by rally-mcp's hand-written tools, which mark external calendar
# content as untrusted field by field; not generated again.
HAND_WRITTEN = frozenset(
    {
        ("GET", "/api/todos"),
        ("POST", "/api/todos"),
        ("PUT", "/api/todos/{todo_id}"),
        ("DELETE", "/api/todos/{todo_id}"),
        ("GET", "/api/events"),
        ("POST", "/api/events"),
        ("GET", "/api/dashboard/summary"),
    }
)

# Every tool touches exactly one scope, ``<area>:read`` or ``<area>:write``.
# A connection's permissions are a set of tool names,
# and each tool may only reach routes of its own scope: Rally enforces that
# on every MCP request, so rally-mcp's own filtering is not the only fence.
#
# The hand-written tools' scopes, by tool name. rally-mcp declares the same
# scope next to each tool (``@scoped(...)`` in ``rally_mcp.scopes``) and its
# tests check the two agree through the OpenAPI snapshot. A new hand-written
# tool needs one line here and one there.
HAND_WRITTEN_TOOLS: dict[str, str] = {
    "list_todos": "todos:read",
    "create_todo": "todos:write",
    "update_todo": "todos:write",
    "complete_todo": "todos:write",
    "delete_todo": "todos:write",
    "list_events": "calendar:read",
    "create_event": "calendar:write",
    "get_daily_summary": "dashboard:read",
}

# Routes whose scope is not ``<area>:read|write``, as ``(pattern, scope)``.
# None so far; a route that needs a scope of its own goes here.
ROUTE_SCOPES: tuple[tuple[re.Pattern, str], ...] = ()

# Lookups any tool may make whatever its scope: the
# hand-written tools resolve names to ids through the family list (names and
# colors only for MCP; Rally strips the Pushover keys).
SUPPORT_ROUTES = frozenset({("GET", "/api/family")})

# Each area's scope name, where it differs from the area itself.
SCOPE_AREAS = {
    "todo": "todos",
    "recurring_todo": "todos",
    "event": "calendar",
    "note": "notes",
    "meal": "meals",
    "prep": "preparedness",
    "team": "sports",
    "settings": "briefing",
}

# Responses that carry text from outside the household: ESPN schedules, an
# LLM's review, a calendar feed's error message. rally-mcp wraps the whole
# result in ``untrusted_external_content``.
UNTRUSTED = (
    re.compile(r"/api/followed-teams(/.*)?"),
    re.compile(r"/api/preparedness/review"),
    re.compile(r"/api/events/sync(/.*)?"),
    re.compile(r"/api/dashboard/regenerate"),
)

# Calls that reach outside Rally (send a push, call an LLM or ESPN, sync feeds).
OPEN_WORLD = (
    re.compile(r"/api/events/\{event_id\}/notify"),
    re.compile(r"/api/events/sync"),
    re.compile(r"/api/preparedness/digest/run"),
    re.compile(r"/api/preparedness/review"),
    re.compile(r"/api/dashboard/regenerate"),
    re.compile(r"/api/followed-teams/\{team_id\}/test"),
)

# GETs that change something, so they are not read-only.
NOT_READ_ONLY = (re.compile(r"/api/dashboard/regenerate"),)

# Non-DELETE routes that throw work away.
DESTRUCTIVE = (re.compile(r".*/(reset|remove)"),)

AREAS = {
    "todos": "todo",
    "recurring-todos": "recurring_todo",
    "events": "event",
    "notes": "note",
    "meal-planner": "meal",
    "shopping": "shopping",
    "preparedness": "prep",
    "packing-list-templates": "packing",
    "packing-list-days": "packing",
    "packing-list-bags": "packing",
    "packing-list-items": "packing",
    "packing-list-template-schedules": "packing",
    "followed-teams": "team",
    "dashboard": "dashboard",
    "settings": "settings",
    "notifications": "notifications",
    "preferences": "preferences",
    "family": "family",
}


# What a tool's description says it belongs to, so a Claude choosing among
# a hundred tools knows "Get Item" is the emergency-kit one.
AREA_LABELS = {
    "todo": "To-dos",
    "recurring_todo": "Recurring to-dos",
    "event": "Calendar",
    "note": "Daily notes",
    "meal": "Meal planner",
    "shopping": "Shopping list",
    "prep": "Emergency preparedness",
    "packing": "Packing lists",
    "team": "Followed sports teams",
    "dashboard": "Dashboard",
    "settings": "AI briefing settings",
    "notifications": "Notifications",
    "preferences": "Personal defaults",
    "family": "Family members",
}


def area_of(path: str) -> str:
    segment = path.split("/")[2]
    return AREAS.get(segment, segment.replace("-", "_"))


def route_scope(method: str, path: str) -> str:
    """The scope a route touches: ``<area>:read`` or ``<area>:write``, unless ``ROUTE_SCOPES`` says otherwise."""
    for pattern, scope in ROUTE_SCOPES:
        if pattern.fullmatch(path):
            return scope
    area = area_of(path)
    name = SCOPE_AREAS.get(area, area)
    read = method == "GET" and not any(p.fullmatch(path) for p in NOT_READ_ONLY)
    return f"{name}:{'read' if read else 'write'}"


def denial(method: str, path: str) -> str | None:
    """Why MCP may not use this route, or None when it may."""
    for rule in DENIED:
        if rule.matches(method, path):
            return rule.reason
    return None


def _api_routes(app) -> list[tuple[str, str, object]]:
    found = []
    for route in iter_routes(app.router.routes):
        path = getattr(route, "path", None)
        methods = getattr(route, "methods", None)
        if not path or not methods or not path.startswith("/api/"):
            continue
        for method in sorted(methods - {"HEAD"}):
            found.append((method, path, route))
    return found


def allowed_routes(app) -> set[tuple[str, str]]:
    """Every (method, route template) an MCP identity may call."""
    return {
        (method, path)
        for method, path, _ in _api_routes(app)
        if (method, path) not in MCP_SERVICE_ROUTES and denial(method, path) is None
    }


def tool_name(method: str, path: str, route_name: str) -> str:
    area = area_of(path)
    stem = area.split("_")[0][:4]
    # Prefixed only when the name doesn't already say where it belongs:
    # "list_notes" and "create_meal_plan" stay as they are, while
    # "shopping_list_items" and "prep_list_items" keep two list_items apart.
    if any(word.startswith(stem) for word in route_name.split("_")):
        return route_name[:64]
    return f"{area}_{route_name}"[:64]


def _annotations(method: str, path: str) -> dict:
    read_only = method == "GET" and not any(p.fullmatch(path) for p in NOT_READ_ONLY)
    destructive = method == "DELETE" or any(p.fullmatch(path) for p in DESTRUCTIVE)
    return {
        "readOnlyHint": read_only,
        "destructiveHint": destructive,
        "idempotentHint": method in {"GET", "PUT", "DELETE"},
        "openWorldHint": any(p.fullmatch(path) for p in OPEN_WORLD),
    }


def generated_routes(app) -> list[tuple[str, str, object]]:
    allowed = allowed_routes(app)
    return [
        (method, path, route)
        for method, path, route in _api_routes(app)
        if (method, path) in allowed and (method, path) not in HAND_WRITTEN
    ]


def openapi_for_mcp(app) -> dict:
    """Rally's OpenAPI document, cut down to the routes rally-mcp generates tools for.

    Each operation gets a stable ``operationId`` (the tool name) and an
    ``x-rally-mcp`` block: the tool's annotations and whether its result is
    untrusted. Unreferenced component schemas are left in; they are inert.
    """
    full = copy.deepcopy(app.openapi())
    keep = {(m.lower(), p): r for m, p, r in generated_routes(app)}
    paths = {}
    seen: set[str] = set()
    for path, operations in full.get("paths", {}).items():
        for method, op in operations.items():
            route = keep.get((method, path))
            if route is None:
                continue
            name = tool_name(method.upper(), path, getattr(route, "name", "") or op["operationId"])
            if name in seen:
                name = f"{name}_{method}"[:64]
            seen.add(name)
            op["operationId"] = name
            label = AREA_LABELS.get(area_of(path), area_of(path))
            summary = op.get("summary") or name.replace("_", " ").capitalize()
            details = (op.get("description") or "").strip()
            op["description"] = f"{label}: {summary}." + (f"\n\n{details}" if details else "")
            op["x-rally-mcp"] = {
                "annotations": _annotations(method.upper(), path),
                "untrusted": any(p.fullmatch(path) for p in UNTRUSTED),
                "scope": route_scope(method.upper(), path),
            }
            paths.setdefault(path, {})[method] = op
    full["paths"] = paths
    full["info"] = {**full.get("info", {}), "title": "Rally (MCP)"}
    # The hand-written tools' scopes, for rally-mcp's tests to check its own
    # declarations against.
    full["x-rally-mcp-hand-written"] = dict(sorted(HAND_WRITTEN_TOOLS.items()))
    return full


# --- The tool catalog ----------------------------------------------------------


@dataclass(frozen=True)
class ToolInfo:
    """One tool a connection can be given, as the Connections page lists it."""

    name: str
    scope: str
    label: str  # the area it belongs to, e.g. "Shopping list"
    summary: str
    # The one route a generated tool calls; None for a hand-written tool,
    # which may call any route of its own scope (plus the support lookups).
    route: tuple[str, str] | None = None


SCOPE_LABELS = {
    "todos": "To-dos",
    "calendar": "Calendar",
    "notes": "Daily notes",
    "meals": "Meal planner",
    "shopping": "Shopping list",
    "preparedness": "Emergency preparedness",
    "packing": "Packing lists",
    "sports": "Followed sports teams",
    "dashboard": "Dashboard",
    "briefing": "AI briefing settings",
    "notifications": "Notifications",
    "preferences": "Personal defaults",
    "family": "Family members",
}

_catalogs: dict[int, tuple[int, dict[str, ToolInfo]]] = {}


def _hand_written_summary(name: str) -> str:
    return name.replace("_", " ").capitalize()


def tool_catalog(app) -> dict[str, ToolInfo]:
    """Every tool name a connection can be given, with its scope.

    The generated tools come from the same document rally-mcp builds them
    from, the hand-written ones from ``HAND_WRITTEN_TOOLS``. Cached per app
    (keyed by its route count, so a route added later is picked up).
    """
    key = len(app.router.routes)
    cached = _catalogs.get(id(app))
    if cached and cached[0] == key:
        return cached[1]
    catalog: dict[str, ToolInfo] = {}
    for name, scope in HAND_WRITTEN_TOOLS.items():
        area = scope.split(":")[0]
        catalog[name] = ToolInfo(
            name=name,
            scope=scope,
            label=SCOPE_LABELS.get(area, area),
            summary=_hand_written_summary(name),
        )
    spec = openapi_for_mcp(app)
    for path, operations in spec["paths"].items():
        for method, op in operations.items():
            name = op["operationId"]
            scope = op["x-rally-mcp"]["scope"]
            area = scope.split(":")[0]
            catalog[name] = ToolInfo(
                name=name,
                scope=scope,
                label=SCOPE_LABELS.get(area, area),
                summary=op.get("summary") or _hand_written_summary(name),
                route=(method.upper(), path),
            )
    _catalogs[id(app)] = (key, catalog)
    return catalog


def call_denial(catalog: dict[str, ToolInfo], tool: str, method: str, route: str) -> str | None:
    """Why ``tool`` may not make this request, or None when it may.

    A generated tool calls exactly its own route. A hand-written tool calls
    routes of its own scope, plus the support lookups.
    """
    info = catalog.get(tool)
    if info is None:
        return f"{tool!r} is not a Rally tool"
    if info.route is not None:
        if info.route != (method, route):
            return f"tool {tool} may not call {method} {route}"
        return None
    if (method, route) in SUPPORT_ROUTES:
        return None
    scope = route_scope(method, route)
    if scope != info.scope:
        return f"tool {tool} ({info.scope}) may not call {method} {route} ({scope})"
    return None
