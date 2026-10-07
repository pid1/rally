"""Migration 037 records why a bag goes in nothing on a day (#266).

It runs on every container start, so it has to be a no-op the second time,
write no rows — a reading made before it ran has no record, and a resync must
not guess one — and leave a database the ORM can use.
"""

import importlib.util
import pathlib
import sqlite3

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from rally.database import Base
from rally.models import PackingListDayBag

MIGRATIONS = pathlib.Path(__file__).resolve().parents[1] / "migrations"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, MIGRATIONS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _migrate():
    return _load("migrate_037_add_day_bag_removed_from").migrate()


def _columns(path, table):
    with sqlite3.connect(path) as conn:
        return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _rows(path, sql):
    with sqlite3.connect(path) as conn:
        return conn.execute(sql).fetchall()


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    """A database as 034–036 left it, with one day's reading of a bag that a
    removal un-nested before this migration existed."""
    path = tmp_path / "rally.db"
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE settings (key VARCHAR(100) PRIMARY KEY, value TEXT, updated_at DATETIME)"
        )
        conn.execute("INSERT INTO settings VALUES ('local_timezone', 'UTC', 0)")
    monkeypatch.setenv("RALLY_DB_PATH", str(path))
    for name in (
        "migrate_034_add_packing_lists",
        "migrate_035_packing_list_templates",
        "migrate_036_add_bag_owners_and_nesting",
    ):
        assert _load(name).migrate() is True
    with sqlite3.connect(path) as conn:
        conn.execute(
            "INSERT INTO packing_list_day_bags (day_id, bag_id, owner_id, parent_bag_id,"
            " created_at, updated_at) VALUES (1, 2, 4, NULL, 0, 0)"
        )
    return path


def test_adds_the_column_and_writes_no_record(db_path):
    assert _migrate() is True
    assert "removed_from_bag_id" in _columns(db_path, "packing_list_day_bags")
    assert _rows(
        db_path,
        "SELECT day_id, bag_id, owner_id, parent_bag_id, removed_from_bag_id"
        " FROM packing_list_day_bags",
    ) == [(1, 2, 4, None, None)]


def test_is_idempotent(db_path):
    assert _migrate() is True
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE packing_list_day_bags SET removed_from_bag_id = 1")
    assert _migrate() is True
    assert _rows(db_path, "SELECT removed_from_bag_id FROM packing_list_day_bags") == [(1,)]


def test_a_database_without_the_table_is_not_an_error(tmp_path, monkeypatch):
    path = tmp_path / "rally.db"
    sqlite3.connect(path).close()
    monkeypatch.setenv("RALLY_DB_PATH", str(path))
    assert _migrate() is True


def test_a_missing_database_is_not_an_error(tmp_path, monkeypatch):
    monkeypatch.setenv("RALLY_DB_PATH", str(tmp_path / "absent.db"))
    assert _migrate() is True


def test_agrees_with_the_model(db_path, tmp_path):
    _migrate()
    fresh = tmp_path / "fresh.db"
    engine = create_engine(f"sqlite:///{fresh}")
    Base.metadata.create_all(engine)
    engine.dispose()
    assert _columns(db_path, "packing_list_day_bags") == _columns(fresh, "packing_list_day_bags")


def test_the_orm_can_use_what_it_made(db_path):
    _migrate()
    engine = create_engine(f"sqlite:///{db_path}")
    with Session(engine) as session:
        session.add(PackingListDayBag(day_id=2, bag_id=3, removed_from_bag_id=1))
        session.commit()
        assert session.query(PackingListDayBag).filter_by(day_id=2).one().removed_from_bag_id == 1
    engine.dispose()
