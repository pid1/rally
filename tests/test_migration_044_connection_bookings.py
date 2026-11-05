"""Migration 043 adds the phone booking columns to `events`.

Purely additive: it runs on every container start, so it has to be a no-op the
second time, every existing event must read as a confirmed, family-made event,
and the confirmation hash must be unique without existing NULLs colliding.
"""

import importlib.util
import pathlib
import sqlite3

import pytest

MIGRATIONS = pathlib.Path(__file__).resolve().parents[1] / "migrations"
NEW = {"source", "pending_confirmation", "confirmation_hash", "contact_name", "contact_phone"}


def _migrate():
    spec = importlib.util.spec_from_file_location(
        "migrate_044_add_connection_bookings", MIGRATIONS / "migrate_044_add_connection_bookings.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.migrate()


def _columns(path):
    with sqlite3.connect(path) as conn:
        return {row[1] for row in conn.execute("PRAGMA table_info(events)")}


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    """An `events` table as migrations through 041 left it, with two events."""
    path = tmp_path / "rally.db"
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE events (id INTEGER PRIMARY KEY, calendar_id INTEGER, uid VARCHAR(200),"
            " title VARCHAR(200), description TEXT, location VARCHAR(200), all_day BOOLEAN,"
            " start_utc DATETIME, end_utc DATETIME, start_date VARCHAR(10), end_date VARCHAR(10),"
            " tzid VARCHAR(64), rrule TEXT, series_end_date VARCHAR(10),"
            " notify_minutes_before INTEGER, packing_list_span VARCHAR(10),"
            " created_at DATETIME, updated_at DATETIME)"
        )
        for uid in ("a@rally", "b@rally"):
            conn.execute(
                "INSERT INTO events (calendar_id, uid, title, all_day, start_utc, end_utc,"
                " start_date, end_date, tzid) VALUES (1, ?, 'Soccer', 0, '2026-10-01 14:00',"
                " '2026-10-01 15:00', '2026-10-01', '2026-10-01', 'America/Chicago')",
                (uid,),
            )
    monkeypatch.setenv("RALLY_DB_PATH", str(path))
    return path


def test_adds_the_columns_and_existing_events_read_as_family_made(db_path):
    assert _migrate() is True
    assert NEW <= _columns(db_path)
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT title, source, pending_confirmation, confirmation_hash FROM events"
        ).fetchall()
    assert rows == [("Soccer", None, 0, None), ("Soccer", None, 0, None)]


def test_the_confirmation_hash_is_unique(db_path):
    assert _migrate() is True
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE events SET confirmation_hash = 'h1' WHERE id = 1")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE events SET confirmation_hash = 'h1' WHERE id = 2")


def test_is_idempotent(db_path):
    assert _migrate() is True
    assert _migrate() is True
    assert NEW <= _columns(db_path)


def test_finishes_a_partial_run(db_path):
    with sqlite3.connect(db_path) as conn:
        conn.execute("ALTER TABLE events ADD COLUMN source VARCHAR(200)")
    assert _migrate() is True
    assert NEW <= _columns(db_path)


def test_missing_database_is_a_noop(tmp_path, monkeypatch):
    monkeypatch.setenv("RALLY_DB_PATH", str(tmp_path / "does-not-exist.db"))
    assert _migrate() is True


def test_missing_table_is_a_noop(tmp_path, monkeypatch):
    path = tmp_path / "rally.db"
    sqlite3.connect(path).close()
    monkeypatch.setenv("RALLY_DB_PATH", str(path))
    assert _migrate() is True


def test_matches_the_model():
    """The model declares every column the migration adds."""
    from rally.models import Event

    model = {c.name for c in Event.__table__.columns}
    assert NEW <= model
