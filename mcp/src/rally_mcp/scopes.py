"""Which scope each tool touches, the unit a connection's permissions are checked in.

A scope is ``<area>:read`` or ``<area>:write`` (``todos:read``,
``calendar:write``, ``shopping:write`` ...).

**Declaring a tool's scope.** Every hand-written tool declares it with one
line, the ``@scoped`` decorator, directly under ``@mcp.tool(...)``::

    @mcp.tool(annotations=READ_ONLY | {"title": "List to-dos"}, ...)
    @scoped("todos:read")
    async def list_todos(...): ...

``SCOPES`` is the resulting table, keyed by tool name. A served tool with no
scope is never listed and cannot be called on any connection (it fails
closed), and ``tests/test_connections.py`` fails until it has one. Generated tools get their scope from Rally's OpenAPI document
(``x-rally-mcp.scope``) instead. Rally keeps its own copy of the hand-written
scopes (``rally.audit.mcp_policy.HAND_WRITTEN_TOOLS``), because Rally enforces
them too; ``tests/test_connections.py`` checks the two agree.
"""

from __future__ import annotations

from collections.abc import Callable

SCOPES: dict[str, str] = {}


def declare(tool_name: str, scope: str) -> None:
    """Record ``tool_name``'s scope; the same name may not be given two."""
    if ":" not in scope:
        raise ValueError(f"{scope!r} is not a scope (want <area>:read|write)")
    existing = SCOPES.get(tool_name)
    if existing is not None and existing != scope:
        raise ValueError(f"{tool_name} is already declared as {existing}, not {scope}")
    SCOPES[tool_name] = scope


def scoped(scope: str) -> Callable:
    """Decorator: declare the decorated tool function's scope, by its name."""

    def register(fn):
        declare(fn.__name__, scope)
        return fn

    return register
