"""Migration 038 deletes the LLM max tokens settings keys.

Rally no longer caps LLM output, so the three keys that held a budget are dead.
Migrations run on every container start, so it has to be a no-op the second
time, leave every other setting and every history snapshot alone, and survive
migration 019 running before it on each boot.
"""

import importlib.util
import json
import pathlib
import sqlite3

import pytest

MIGRATIONS = pathlib.Path(__file__).resolve().parents[1] / "migrations"

REMOVED = ("llm_anthropic_max_tokens", "llm_anthropic_max_tokens_mode", "llm_local_max_tokens")


def _load(name):
    spec = importlib.util.spec_from_file_location(name, MIGRATIONS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _migrate():
    return _load("migrate_038_remove_llm_max_tokens").migrate()


def _settings(path):
    with sqlite3.connect(path) as conn:
        return dict(conn.execute("SELECT key, value FROM settings").fetchall())


LEGACY_SNAPSHOT = json.dumps(
    {"provider": "anthropic", "model": "claude-x", "max_tokens": 4000, "max_tokens_mode": "custom"}
)


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    """A database as migration 019 used to leave it."""
    path = tmp_path / "rally.db"
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE settings (key VARCHAR(100) PRIMARY KEY, value TEXT, updated_at DATETIME)"
        )
        conn.execute(
            "CREATE TABLE llm_settings_history (id INTEGER PRIMARY KEY, field_name TEXT,"
            " value TEXT, created_at DATETIME, last_used_at DATETIME)"
        )
        conn.executemany(
            "INSERT INTO settings VALUES (?, ?, 0)",
            [
                ("llm_provider", "anthropic"),
                ("llm_anthropic_model", "claude-x"),
                ("llm_anthropic_max_tokens", "4000"),
                ("llm_anthropic_max_tokens_mode", "custom"),
                ("llm_local_max_tokens", "4000"),
            ],
        )
        conn.execute(
            "INSERT INTO llm_settings_history VALUES (1, 'llm_config', ?, 0, 0)",
            (LEGACY_SNAPSHOT,),
        )
    monkeypatch.setenv("RALLY_DB_PATH", str(path))
    return path


def test_deletes_only_the_removed_keys(db_path):
    assert _migrate() is True
    assert _settings(db_path) == {"llm_provider": "anthropic", "llm_anthropic_model": "claude-x"}


def test_leaves_history_snapshots_alone(db_path):
    assert _migrate() is True
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT value FROM llm_settings_history").fetchall() == [
            (LEGACY_SNAPSHOT,)
        ]


def test_is_idempotent(db_path):
    assert _migrate() is True
    assert _migrate() is True
    assert not set(REMOVED) & set(_settings(db_path))


def test_retired_019_does_not_reseed_the_keys(db_path):
    """Every boot runs 019 then 038; 019 must not put the keys back."""
    assert _migrate() is True
    assert _load("migrate_019_add_llm_max_tokens").migrate() is True
    assert not set(REMOVED) & set(_settings(db_path))


def test_a_database_without_the_table_is_not_an_error(tmp_path, monkeypatch):
    path = tmp_path / "rally.db"
    sqlite3.connect(path).close()
    monkeypatch.setenv("RALLY_DB_PATH", str(path))
    assert _migrate() is True


def test_a_missing_database_is_not_an_error(tmp_path, monkeypatch):
    monkeypatch.setenv("RALLY_DB_PATH", str(tmp_path / "absent.db"))
    assert _migrate() is True
