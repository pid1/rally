"""Migration 040 adds `dinner_plans.notes`.

Purely additive: it runs on every container start, so it has to be a no-op
the second time, and it must leave every existing meal's `plan` text (and
every other column) exactly as it was.
"""

import importlib.util
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
    return _load("migrate_040_add_meal_notes").migrate()


def _columns(path, table):
    with sqlite3.connect(path) as conn:
        return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _rows(path, sql):
    with sqlite3.connect(path) as conn:
        return conn.execute(sql).fetchall()


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    """A database as migrations up through 039 left it, with one existing meal."""
    path = tmp_path / "rally.db"
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE dinner_plans ("
            " id INTEGER PRIMARY KEY,"
            " date VARCHAR(10),"
            " meal_type VARCHAR(20) DEFAULT 'Dinner',"
            " plan TEXT,"
            " attendee_ids TEXT,"
            " cook_id INTEGER,"
            " rating INTEGER,"
            " review TEXT,"
            " created_at DATETIME,"
            " updated_at DATETIME"
            ")"
        )
        conn.execute(
            "INSERT INTO dinner_plans (date, meal_type, plan, rating, review, created_at,"
            " updated_at) VALUES ('2026-05-01', 'Dinner', 'Tacos', 5, 'Great', 0, 0)"
        )
    monkeypatch.setenv("RALLY_DB_PATH", str(path))
    return path


def test_adds_the_column_and_writes_no_record(db_path):
    assert _migrate() is True
    assert "notes" in _columns(db_path, "dinner_plans")
    assert _rows(db_path, "SELECT plan, notes FROM dinner_plans") == [("Tacos", None)]


def test_is_idempotent(db_path):
    assert _migrate() is True
    assert _migrate() is True
    assert _rows(db_path, "SELECT plan, notes FROM dinner_plans") == [("Tacos", None)]


def test_missing_database_is_a_noop(tmp_path, monkeypatch):
    monkeypatch.setenv("RALLY_DB_PATH", str(tmp_path / "does-not-exist.db"))
    assert _migrate() is True


def test_missing_table_is_a_noop(tmp_path, monkeypatch):
    path = tmp_path / "rally.db"
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE settings (key VARCHAR(100) PRIMARY KEY, value TEXT, updated_at DATETIME)"
        )
    monkeypatch.setenv("RALLY_DB_PATH", str(path))
    assert _migrate() is True
