"""Who is acting, for every audit event written while they act.

Each HTTP request gets an ``AuditContext`` from the audit middleware, and the
data-layer listeners read it to stamp every event they write. Anything that
touches the database outside a request (the 4 AM generator, the notification
loop, the CLI) gets a job context instead, so there is never a write without an
actor — at worst the actor is the job's own name.

The context lives in a ``ContextVar``. FastAPI runs sync endpoints in a worker
thread with a copy of the caller's context, and the copy holds the *same*
``AuditContext`` object, so reads recorded in the worker are visible to the
middleware that flushes them.
"""

from __future__ import annotations

import sys
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field

SOURCE_WEB = "web"
SOURCE_API = "api"
SOURCE_MCP = "mcp"
SOURCE_JOB = "job"

ACTOR_USER = "user"
ACTOR_MCP = "mcp_client"
ACTOR_SYSTEM = "system"
ACTOR_JOB = "job"

UNKNOWN_ACTOR = "unknown"


def new_request_id() -> str:
    """A time-sortable id, so request ids and event ids sort with the clock."""
    return str(uuid.uuid7())


@dataclass
class AuditContext:
    request_id: str
    source: str
    actor_type: str
    actor_id: str = UNKNOWN_ACTOR
    actor_name: str | None = None
    # True when we could not tell who this was. The request is still served on
    # the tailnet; the event is flagged so the Audit Log page can call it out.
    flagged: bool = False
    client_info: dict = field(default_factory=dict)
    mcp_tool: str | None = None
    mcp_arguments: dict | None = None
    # entity_type -> {"ids": set, "count": int}; flushed once per request so a
    # page that lists forty todos is one read event, not forty.
    reads: dict[str, dict] = field(default_factory=dict)
    # Set by the audit middleware. Only a request has someone to flush its
    # reads when it ends; a job's reads stay with its session instead.
    is_request: bool = False
    # Sessions this context read through, so the middleware can flush reads
    # with the same database binding the request used.
    sessions: list = field(default_factory=list)

    def note_read(self, entity_type: str, ids: list[str]) -> None:
        bucket = self.reads.setdefault(entity_type, {"ids": set(), "count": 0})
        bucket["count"] += len(ids)
        bucket["ids"].update(ids)


_current: ContextVar[AuditContext | None] = ContextVar("rally_audit_context", default=None)
_process_job: AuditContext | None = None


def _job_name() -> str:
    argv0 = sys.argv[0] if sys.argv else ""
    if "-m" in sys.orig_argv:
        idx = sys.orig_argv.index("-m")
        if idx + 1 < len(sys.orig_argv):
            return sys.orig_argv[idx + 1]
    return argv0.rsplit("/", 1)[-1] or "rally"


def current() -> AuditContext:
    """The active context, or this process's job context outside a request."""
    ctx = _current.get()
    if ctx is not None:
        return ctx
    global _process_job
    if _process_job is None:
        _process_job = AuditContext(
            request_id=new_request_id(),
            source=SOURCE_JOB,
            actor_type=ACTOR_JOB,
            actor_id=_job_name(),
        )
    return _process_job


def activate(ctx: AuditContext):
    return _current.set(ctx)


def reset(token) -> None:
    _current.reset(token)


@contextmanager
def job_context(name: str, *, actor_type: str = ACTOR_JOB) -> Iterator[AuditContext]:
    """Attribute everything inside the block to a named job (or ``system``)."""
    ctx = AuditContext(
        request_id=new_request_id(),
        source=SOURCE_JOB,
        actor_type=actor_type,
        actor_id=name,
    )
    token = _current.set(ctx)
    try:
        yield ctx
    finally:
        _current.reset(token)
