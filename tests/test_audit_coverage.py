"""No endpoint ships unaudited.

Enumerates every route the app registers — every method of every path, API
and pages alike — calls it, and requires that the call left an audit event
under the response's ``X-Request-Id``. A new endpoint is covered the moment it
is registered; one that somehow bypasses the audit layer fails here.

Calls are made with placeholder path parameters and an empty body, so most
writes answer 404 or 422 — which is the point: a refused or failed request
must be on the record too. The network is cut off for the duration, so routes
that talk to the outside world (LLM tests, CalDAV, Pushover) fail fast
instead of reaching out.

The MCP tools have the same guarantee in ``mcp/tests/test_tool_coverage.py``.
"""

import re
import socket

import pytest
from fastapi.testclient import TestClient

from rally.audit.middleware import MCP_ALLOWED_ROUTES, MCP_SERVICE_ROUTES, iter_routes
from rally.database import get_db
from rally.main import app
from rally.models import AuditEvent

PARAM = re.compile(r"\{[^}]+\}")
WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def _endpoints() -> list[tuple[str, str]]:
    found = set()
    for route in iter_routes(app.router.routes):
        path = getattr(route, "path", None)
        methods = getattr(route, "methods", None)
        if not path or not methods or path.startswith("/static"):
            continue
        for method in methods - {"HEAD"}:
            found.add((method, path))
    return sorted(found)


ENDPOINTS = _endpoints()


def test_the_route_list_is_not_empty():
    # Guards the guard: a broken enumeration would make every test below pass
    # vacuously.
    assert len(ENDPOINTS) > 90
    assert ("GET", "/api/todos") in ENDPOINTS
    assert ("PUT", "/api/todos/{todo_id}") in ENDPOINTS


def test_every_mcp_route_exists():
    """The MCP allowlist names only real routes, so a rename cannot silently widen it."""
    assert (MCP_ALLOWED_ROUTES | MCP_SERVICE_ROUTES) <= set(ENDPOINTS)


@pytest.fixture
def raw_client(db_session, monkeypatch):
    def no_network(*args, **kwargs):
        raise OSError("network disabled in the audit coverage test")

    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.setattr(socket, "create_connection", no_network)
    monkeypatch.setattr(socket, "getaddrinfo", no_network)

    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    try:
        yield TestClient(app, raise_server_exceptions=False)
    finally:
        app.dependency_overrides.clear()


@pytest.mark.parametrize("method, path", ENDPOINTS, ids=[f"{m} {p}" for m, p in ENDPOINTS])
def test_every_endpoint_is_audited(raw_client, db_session, method, path):
    url = PARAM.sub("1", path)
    kwargs = {"json": {}} if method in WRITE_METHODS else {}
    response = raw_client.request(method, url, follow_redirects=False, **kwargs)

    db_session.expire_all()
    request_id = response.headers.get("x-request-id")
    if request_id is None:
        # An unhandled exception: Starlette's outermost error handler writes
        # the 500, so the header is lost — but the event must still be there.
        assert response.status_code == 500, f"{method} {path} answered without an X-Request-Id"
        req = (
            db_session.query(AuditEvent)
            .filter(AuditEvent.action == "http.request")
            .order_by(AuditEvent.occurred_at.desc())
            .first()
        )
        assert req is not None and req.error, f"{method} {path} crashed without an audit event"
    else:
        rows = db_session.query(AuditEvent).filter(AuditEvent.request_id == request_id).all()
        [req] = [e for e in rows if e.action == "http.request"]
    assert req.after["method"] == method
    assert req.after["route"] == path
    assert req.after["status"] == response.status_code
    expected = "success" if response.status_code < 400 else None
    if expected:
        assert req.outcome == "success"
    else:
        assert req.outcome in {"failure", "denied"}
