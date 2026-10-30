"""The runner stamps a database, and refuses one newer than its own migrations.

A database that has been through a migration this code has never heard of has a
schema this code cannot read. Running on crashes part-way through an old
migration at best, so the runner has to stop before it touches anything.
"""

import importlib.util
import pathlib
import re
import sqlite3

import pytest
from sqlalchemy import create_engine

from rally.database import Base

MIGRATIONS = pathlib.Path(__file__).resolve().parents[1] / "migrations"
LATEST = max(
    int(n) for n in re.findall(r'\("(\d{3})_', (MIGRATIONS / "run_migrations.py").read_text())
)


@pytest.fixture
def runner(monkeypatch, tmp_path):
    db_path = tmp_path / "rally.db"
    monkeypatch.setenv("RALLY_DB_PATH", str(db_path))
    monkeypatch.syspath_prepend(str(MIGRATIONS))
    spec = importlib.util.spec_from_file_location(
        "run_migrations", MIGRATIONS / "run_migrations.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, db_path


def _create_schema(db_path):
    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    engine.dispose()


def _user_version(db_path):
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute("PRAGMA user_version").fetchone()[0]
    finally:
        conn.close()


def _set_user_version(db_path, version):
    conn = sqlite3.connect(db_path)
    conn.execute(f"PRAGMA user_version = {version}")
    conn.close()


def test_a_successful_run_stamps_the_highest_migration(runner):
    module, db_path = runner
    _create_schema(db_path)

    assert module.run_migrations() is True
    assert _user_version(db_path) == LATEST


def test_running_again_keeps_the_stamp(runner):
    module, db_path = runner
    _create_schema(db_path)

    assert module.run_migrations() is True
    assert module.run_migrations() is True
    assert _user_version(db_path) == LATEST


def test_a_failed_run_does_not_stamp(runner, monkeypatch):
    module, db_path = runner
    _create_schema(db_path)
    import migrate_040_add_meal_notes

    def broken():
        return False

    monkeypatch.setattr(migrate_040_add_meal_notes, "migrate", broken)

    assert module.run_migrations() is False
    assert _user_version(db_path) == 0


def test_a_newer_database_is_refused_before_anything_runs(runner, capsys):
    module, db_path = runner
    _create_schema(db_path)
    _set_user_version(db_path, LATEST + 1)

    assert module.run_migrations() is False

    out = capsys.readouterr().out
    assert f"has been through migration {LATEST + 1:03d}" in out
    assert f"this code stops at {LATEST:03d}" in out
    assert "[001_add_due_date]" not in out, "no migration may run against it"
    assert _user_version(db_path) == LATEST + 1


def test_no_database_yet_is_not_an_error(runner):
    module, db_path = runner

    assert module.run_migrations() is True
    assert not db_path.exists()
