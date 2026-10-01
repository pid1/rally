"""Migration 034 creates the checklist tables, and agrees with the models.

It runs on every container start, so it has to be a no-op the second time, and
it has to leave a database the ORM can use: a table the migration shaped
differently from ``models.py`` would only fail once somebody wrote to it.
"""

import importlib.util
import pathlib
import sqlite3

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from rally.models import (
    Checklist,
    ChecklistDay,
    ChecklistDayCheck,
    ChecklistGroup,
    ChecklistItem,
)

TABLES = {
    "checklists",
    "checklist_groups",
    "checklist_items",
    "checklist_days",
    "checklist_day_checks",
}


def _load_migration():
    path = (
        pathlib.Path(__file__).resolve().parents[1] / "migrations" / "migrate_034_add_checklists.py"
    )
    spec = importlib.util.spec_from_file_location("migrate_034", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = tmp_path / "rally.db"
    sqlite3.connect(path).close()
    monkeypatch.setenv("RALLY_DB_PATH", str(path))
    return path


def _tables(path):
    with sqlite3.connect(path) as conn:
        return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def test_creates_every_table_and_is_idempotent(db_path):
    migration = _load_migration()
    assert migration.migrate() is True
    assert TABLES <= _tables(db_path)
    assert migration.migrate() is True


def test_a_missing_database_is_not_an_error(tmp_path, monkeypatch):
    monkeypatch.setenv("RALLY_DB_PATH", str(tmp_path / "absent.db"))
    assert _load_migration().migrate() is True


def test_the_orm_can_use_what_it_made(db_path):
    _load_migration().migrate()
    engine = create_engine(f"sqlite:///{db_path}")
    with Session(engine) as session:
        checklist = Checklist(name="Beach day", pack_timing="day_before")
        session.add(checklist)
        session.flush()
        group = ChecklistGroup(checklist_id=checklist.id, name="Emma")
        session.add(group)
        session.flush()
        item = ChecklistItem(checklist_id=checklist.id, group_id=group.id, name="Towel")
        day = ChecklistDay(checklist_id=checklist.id, date="2026-10-03")
        session.add_all([item, day])
        session.flush()
        session.add(ChecklistDayCheck(day_id=day.id, item_id=item.id))
        session.commit()
    engine.dispose()


@pytest.mark.parametrize(
    "statement",
    [
        "INSERT INTO checklists (name, pack_timing, created_at, updated_at)"
        " VALUES ('BEACH DAY', 'day_of', 0, 0)",
        "INSERT INTO checklists (name, pack_timing, created_at, updated_at)"
        " VALUES ('Other', 'week_before', 0, 0)",
        "INSERT INTO checklist_days (checklist_id, date, created_at, updated_at)"
        " VALUES (1, '2026-10-03', 0, 0)",
        "INSERT INTO checklist_day_checks (day_id, item_id, checked_at) VALUES (1, 1, 0)",
        "INSERT INTO checklist_groups (checklist_id, name, sort_order, created_at, updated_at)"
        " VALUES (1, 'EMMA', 1, 0, 0)",
    ],
)
def test_the_rules_the_api_relies_on_hold_in_the_database(db_path, statement):
    _load_migration().migrate()
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO checklists (id, name, pack_timing, created_at, updated_at)"
            " VALUES (1, 'Beach day', 'day_of', 0, 0)"
        )
        conn.execute(
            "INSERT INTO checklist_groups (checklist_id, name, sort_order, created_at, updated_at)"
            " VALUES (1, 'Emma', 0, 0, 0)"
        )
        conn.execute(
            "INSERT INTO checklist_days (checklist_id, date, created_at, updated_at)"
            " VALUES (1, '2026-10-03', 0, 0)"
        )
        conn.execute(
            "INSERT INTO checklist_day_checks (day_id, item_id, checked_at) VALUES (1, 1, 0)"
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(statement)
