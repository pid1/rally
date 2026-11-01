"""The data-layer half of the audit log: every read and write leaves a row.

Hooks are registered on SQLAlchemy's ``Session`` class itself, so they apply to
every session Rally ever opens — the request sessions from ``get_db``, each job's
``SessionLocal()``, and the test suite's own in-memory sessions alike. Nothing
in a router has to remember to log.

- **Writes** (``after_flush``): every inserted, updated and deleted row becomes
  one event with a before/after diff of the changed columns, inserted on the
  flush's own connection — so the change and its audit row commit or roll back
  together. Bulk ``UPDATE``/``DELETE`` statements are caught in
  ``do_orm_execute`` and logged the same way.
- **Reads** (``do_orm_execute``): every ORM ``SELECT`` notes which rows of which
  tables it returned on the active ``AuditContext``. They are written as one
  event per table per request when the request ends (or when a job's session
  commits or closes), because writing mid-request would turn every read-only
  transaction into a write.

Secrets never reach a row: ``redact`` blanks credential-like columns, settings
keys and nested JSON keys before anything is written.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, time
from decimal import Decimal

from sqlalchemy import DDL, event, insert, inspect
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import NO_VALUE, get_history
from sqlalchemy.orm.base import PASSIVE_NO_INITIALIZE

from rally.audit import context as audit_context
from rally.audit.context import AuditContext
from rally.models import AuditEvent, Setting
from rally.utils.timezone import now_utc

REDACTED = "[redacted]"
MAX_STRING = 2000
MAX_READ_IDS = 200

# Column names, settings keys and JSON keys that hold secrets.
SECRET_PATTERN = re.compile(
    r"pass(word)?|secret|token|api[_-]?key|apikey|(^|_)jwt($|_assertion)|credential|authorization|"
    r"cookie|pushover_user_key|private",
    re.IGNORECASE,
)

AUDIT_TABLE = AuditEvent.__tablename__

# Append-only, enforced by the database: the application has no code path that
# updates or deletes an audit row, and these make sure nothing else does either.
APPEND_ONLY_TRIGGERS = (
    f"""CREATE TRIGGER IF NOT EXISTS {AUDIT_TABLE}_no_update
    BEFORE UPDATE ON {AUDIT_TABLE}
    BEGIN SELECT RAISE(ABORT, 'audit_events is append-only'); END""",
    f"""CREATE TRIGGER IF NOT EXISTS {AUDIT_TABLE}_no_delete
    BEFORE DELETE ON {AUDIT_TABLE}
    BEGIN SELECT RAISE(ABORT, 'audit_events is append-only'); END""",
)

for _ddl in APPEND_ONLY_TRIGGERS:
    event.listen(AuditEvent.__table__, "after_create", DDL(_ddl).execute_if(dialect="sqlite"))


# --- Serialization and redaction ---------------------------------------------


def _jsonable(value):
    if value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, str):
        return value if len(value) <= MAX_STRING else value[:MAX_STRING] + "…[truncated]"
    if isinstance(value, datetime | date | time):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, bytes):
        return f"<{len(value)} bytes>"
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple | set):
        return [_jsonable(v) for v in value]
    return str(value)


def redact(value, key: str | None = None):
    """Blank anything secret-looking, recursively, including JSON in strings."""
    if key is not None and SECRET_PATTERN.search(key):
        return None if value is None else REDACTED
    if isinstance(value, dict):
        return {k: redact(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    if isinstance(value, str) and value[:1] in "{[":
        try:
            parsed = json.loads(value)
        except ValueError:
            return value
        if isinstance(parsed, dict | list):
            return json.dumps(redact(parsed))
    return value


def _redact_row(obj, values: dict) -> dict:
    values = redact(values)
    # A settings row is a key/value pair: the secret is in the *value*, named
    # by the key ("pushover_app_token", "anthropic_api_key", ...).
    if isinstance(obj, Setting) and SECRET_PATTERN.search(obj.key or ""):
        for side in ("value",):
            if side in values and values[side] is not None:
                values[side] = REDACTED
    return values


# --- Naming ------------------------------------------------------------------


def _label(class_name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", class_name).lower()


def _entity_id(obj) -> str | None:
    # From the instance's own primary key columns: during ``after_flush`` a
    # new row has its id but is not yet in the identity map.
    insp = inspect(obj)
    key = insp.identity or insp.mapper.primary_key_from_instance(obj)
    if not key or all(part is None for part in key):
        return None
    return ",".join(str(part) for part in key)


# --- Writing -----------------------------------------------------------------


def event_row(
    ctx: AuditContext,
    *,
    action: str,
    entity_type: str | None = None,
    entity_id: str | None = None,
    before: dict | None = None,
    after: dict | None = None,
    outcome: str = "success",
    error: str | None = None,
) -> dict:
    """One audit row as a plain dict, stamped with the context's actor."""
    return {
        "id": audit_context.new_request_id(),
        "occurred_at": now_utc().replace(tzinfo=None),
        "actor_type": ctx.actor_type,
        "actor_id": ctx.actor_id,
        "actor_name": ctx.actor_name,
        "source": ctx.source,
        "client_info": _jsonable(redact(ctx.client_info)) or None,
        "request_id": ctx.request_id,
        "action": action,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "before": _jsonable(before),
        "after": _jsonable(after),
        "mcp_tool": ctx.mcp_tool,
        "mcp_arguments": _jsonable(redact(ctx.mcp_arguments)) if ctx.mcp_arguments else None,
        "outcome": outcome,
        "error": (error or None) and error[:MAX_STRING],
        "flagged": ctx.flagged,
    }


def write_rows(session: Session, rows: list[dict]) -> None:
    """Insert rows on the session's own connection (inside its transaction)."""
    if rows:
        session.connection().execute(insert(AuditEvent.__table__), rows)


def record(session: Session, *, commit: bool = True, **kwargs) -> None:
    """Write one explicit event (auth decisions, exports, enable/disable)."""
    write_rows(session, [event_row(audit_context.current(), **kwargs)])
    if commit:
        session.commit()


def _loaded_values(obj, mapper) -> dict:
    """Column values already in memory — never a lazy load in the middle of a flush."""
    loaded = inspect(obj).dict
    return {attr.key: loaded[attr.key] for attr in mapper.column_attrs if attr.key in loaded}


def _change_rows(session: Session, ctx: AuditContext) -> list[dict]:
    rows = []
    for obj in session.new:
        if isinstance(obj, AuditEvent):
            continue
        mapper = inspect(obj).mapper
        after = _loaded_values(obj, mapper)
        rows.append(
            event_row(
                ctx,
                action=f"{_label(type(obj).__name__)}.create",
                entity_type=mapper.local_table.name,
                entity_id=_entity_id(obj),
                after=_redact_row(obj, after),
            )
        )
    for obj in session.dirty:
        if isinstance(obj, AuditEvent) or not session.is_modified(obj):
            continue
        mapper = inspect(obj).mapper
        before, after = {}, {}
        for attr in mapper.column_attrs:
            hist = get_history(obj, attr.key, passive=PASSIVE_NO_INITIALIZE)
            if not hist.has_changes():
                continue
            old = hist.deleted[0] if hist.deleted else None
            new = hist.added[0] if hist.added else None
            if old is NO_VALUE:
                old = None
            if old == new:
                continue
            before[attr.key], after[attr.key] = old, new
        if not after:
            continue
        rows.append(
            event_row(
                ctx,
                action=f"{_label(type(obj).__name__)}.update",
                entity_type=mapper.local_table.name,
                entity_id=_entity_id(obj),
                before=_redact_row(obj, before),
                after=_redact_row(obj, after),
            )
        )
    for obj in session.deleted:
        if isinstance(obj, AuditEvent):
            continue
        mapper = inspect(obj).mapper
        before = _loaded_values(obj, mapper)
        rows.append(
            event_row(
                ctx,
                action=f"{_label(type(obj).__name__)}.delete",
                entity_type=mapper.local_table.name,
                entity_id=_entity_id(obj),
                before=_redact_row(obj, before),
            )
        )
    return rows


# ``after_flush`` still sees the pre-flush ``new``/``dirty``/``deleted`` sets
# and attribute history, and new rows already have their primary keys, so one
# hook captures all three kinds of change and writes them on the flush's own
# connection.
@event.listens_for(Session, "after_flush")
def _write_changes(session: Session, flush_context) -> None:
    write_rows(session, _change_rows(session, audit_context.current()))


# --- Reads and bulk statements ------------------------------------------------

_reads_key = "rally_audit_reads"


def _table_names(state) -> list[str]:
    names = []
    for mapper in state.all_mappers:
        if mapper.class_ is AuditEvent:
            names.append(AUDIT_TABLE)
        else:
            names.append(mapper.local_table.name)
    return names


@event.listens_for(Session, "do_orm_execute")
def _on_execute(state):
    session = state.session
    if state.is_select:
        tables = _table_names(state)
        if not tables:
            return None
        result = state.invoke_statement().freeze()
        ids: dict[str, list[str]] = {t: [] for t in tables}
        for row in result().all():
            for item in row:
                try:
                    insp = inspect(item)
                except Exception:  # a plain column value, not an entity
                    continue
                if getattr(insp, "mapper", None) is None or not insp.identity:
                    continue
                table = insp.mapper.local_table.name
                ids.setdefault(table, []).append(",".join(str(p) for p in insp.identity))
        ctx = audit_context._current.get()
        if ctx is not None and not ctx.is_request:
            ctx = None
        target = ctx if ctx is not None else _job_reads(session)
        for table, found in ids.items():
            target.note_read(table, found)
        if ctx is not None and session not in ctx.sessions:
            ctx.sessions.append(session)
        return result()

    if state.is_update or state.is_delete:
        tables = _table_names(state)
        result = state.invoke_statement()
        verb = "bulk_update" if state.is_update else "bulk_delete"
        ctx = audit_context.current()
        write_rows(
            session,
            [
                event_row(
                    ctx,
                    action=f"{table.rstrip('s')}.{verb}",
                    entity_type=table,
                    after={
                        "statement": str(state.statement),
                        "rowcount": getattr(result, "rowcount", None),
                    },
                )
                for table in tables
            ],
        )
        return result
    return None


class _JobReads:
    """Reads made outside a request, kept on the session until it commits."""

    def __init__(self) -> None:
        self.reads: dict[str, dict] = {}
        # Attributed to whoever was acting when the reads happened, not to
        # whoever happens to commit the session later.
        self.ctx = audit_context.current()

    note_read = AuditContext.note_read


def _job_reads(session: Session) -> _JobReads:
    holder = session.info.get(_reads_key)
    if holder is None:
        holder = session.info[_reads_key] = _JobReads()
    return holder


def read_rows(ctx: AuditContext, reads: dict[str, dict]) -> list[dict]:
    rows = []
    for table, bucket in sorted(reads.items()):
        ids = sorted(bucket["ids"])
        label = "audit" if table == AUDIT_TABLE else _label_for_table(table)
        rows.append(
            event_row(
                ctx,
                action=f"{label}.read",
                entity_type=table,
                entity_id=ids[0] if len(ids) == 1 else None,
                after={
                    "count": bucket["count"],
                    "ids": ids[:MAX_READ_IDS],
                    "truncated": len(ids) > MAX_READ_IDS,
                },
            )
        )
    return rows


def _label_for_table(table: str) -> str:
    from rally.database import Base

    for mapper in Base.registry.mappers:
        if mapper.local_table.name == table:
            return _label(mapper.class_.__name__)
    return table


@event.listens_for(Session, "before_commit")
def _flush_job_reads(session: Session) -> None:
    holder = session.info.pop(_reads_key, None)
    if holder and holder.reads:
        write_rows(session, read_rows(holder.ctx, holder.reads))


def flush_job_reads(session: Session) -> None:
    """Write reads a job made without committing (called on session close)."""
    holder = session.info.pop(_reads_key, None)
    if not holder or not holder.reads:
        return
    session.rollback()
    write_rows(session, read_rows(holder.ctx, holder.reads))
    session.commit()


def flush_request_reads(ctx: AuditContext) -> None:
    """Write the reads a request made, once, through a session it used."""
    if not ctx.reads or not ctx.sessions:
        ctx.reads.clear()
        return
    session = ctx.sessions[0]
    reads = dict(ctx.reads)
    ctx.reads.clear()
    # Whatever the handler left uncommitted is discarded, exactly as closing
    # the request's session would have — the reads are committed on their own.
    session.rollback()
    write_rows(session, read_rows(ctx, reads))
    session.commit()
