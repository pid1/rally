"""Migration 042 adds `connections` and links existing sign-ins to a legacy connection.

It runs on every container start, so it must be a no-op the second time. A
database that already has MCP sign-ins keeps its Claude connector working on
`/mcp` through one seeded `legacy` connection (OAuth, full access, Anthropic's
ranges); one with no sign-ins gets no connection at all, so `/mcp` stays shut.
"""

import importlib.util
import json
import pathlib
import sqlite3

import pytest

MIGRATIONS = pathlib.Path(__file__).resolve().parents[1] / "migrations"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, MIGRATIONS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _migrate():
    return _load("migrate_043_add_connections").migrate()


def _rows(path, sql):
    with sqlite3.connect(path) as conn:
        return conn.execute(sql).fetchall()


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    """A database as migration 041 left it."""
    path = tmp_path / "rally.db"
    monkeypatch.setenv("RALLY_DB_PATH", str(path))
    sqlite3.connect(path).close()
    assert _load("migrate_041_add_audit_log").migrate() is True
    return path


def _add_sign_in(path, grant="conn_abc"):
    with sqlite3.connect(path) as conn:
        conn.execute(
            "INSERT INTO mcp_connections (id, family_member_id, email, client, first_ip,"
            " last_ip, first_seen_at, last_seen_at) VALUES (?, 1, 'jon@example.com',"
            " 'claude.ai', '160.79.104.10', '160.79.104.10', '2026-10-30 12:00:00',"
            " '2026-11-01 12:00:00')",
            (grant,),
        )


def test_existing_sign_ins_get_a_legacy_connection(db_path):
    _add_sign_in(db_path)
    assert _migrate() is True
    [legacy] = _rows(
        db_path,
        "SELECT id, auth_method, preset, status, legacy, ip_allowlist, authorized_by"
        " FROM connections",
    )
    assert legacy[:5] == ("legacy", "oauth", "full", "enabled", 1)
    assert json.loads(legacy[5]) == ["160.79.104.0/21", "153.61.208.0/20"]
    assert legacy[6] is None
    # The sign-in is kept as it was, now attached to the legacy connection.
    assert _rows(db_path, "SELECT id, email, client, connection_id FROM mcp_connections") == [
        ("conn_abc", "jon@example.com", "claude.ai", "legacy")
    ]


def test_no_sign_ins_means_no_connection(db_path):
    assert _migrate() is True
    assert _rows(db_path, "SELECT COUNT(*) FROM connections") == [(0,)]
    with sqlite3.connect(db_path) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(mcp_connections)")}
    assert "connection_id" in columns


def test_is_idempotent_and_never_reopens_a_revoked_legacy(db_path):
    _add_sign_in(db_path)
    assert _migrate() is True
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE connections SET status = 'revoked' WHERE id = 'legacy'")
    assert _migrate() is True
    assert _rows(db_path, "SELECT id, status FROM connections") == [("legacy", "revoked")]


def test_matches_the_model(db_path):
    """The migration's table is the one SQLAlchemy would create."""
    from sqlalchemy import create_engine, inspect

    from rally.models import Connection

    _add_sign_in(db_path)
    assert _migrate() is True
    engine = create_engine(f"sqlite:///{db_path}")
    columns = {c["name"] for c in inspect(engine).get_columns("connections")}
    engine.dispose()
    assert columns == {c.name for c in Connection.__table__.columns}


def test_missing_database_is_a_noop(tmp_path, monkeypatch):
    monkeypatch.setenv("RALLY_DB_PATH", str(tmp_path / "does-not-exist.db"))
    assert _migrate() is True
