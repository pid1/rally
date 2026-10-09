"""Migration 036 gives packing list bags an owner and a bag they go in (#262).

It runs on every container start, so it has to be a no-op the second time,
leave every bag and item exactly where it was, and leave a database the ORM
can use.
"""

import importlib.util
import pathlib
import sqlite3

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from rally.database import Base
from rally.models import (
    PackingListBag,
    PackingListDayBag,
    PackingListDayBagCheck,
    PackingListTemplateBag,
)

MIGRATIONS = pathlib.Path(__file__).resolve().parents[1] / "migrations"

NEW_TABLES = {
    "packing_list_template_bags",
    "packing_list_day_bags",
    "packing_list_day_bag_checks",
}


def _load(name):
    spec = importlib.util.spec_from_file_location(name, MIGRATIONS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    """A database as 034 and 035 left it, with two bags and an item in one."""
    path = tmp_path / "rally.db"
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE settings (key VARCHAR(100) PRIMARY KEY, value TEXT, updated_at DATETIME)"
        )
        conn.execute("INSERT INTO settings VALUES ('local_timezone', 'UTC', 0)")
    monkeypatch.setenv("RALLY_DB_PATH", str(path))
    assert _load("migrate_034_add_packing_lists").migrate() is True
    assert _load("migrate_035_packing_list_templates").migrate() is True
    with sqlite3.connect(path) as conn:
        conn.execute("INSERT INTO packing_list_bags VALUES (1, 'Suitcase', 0, 0)")
        conn.execute("INSERT INTO packing_list_bags VALUES (2, 'Toiletries bag', 0, 0)")
        conn.execute("INSERT INTO packing_list_templates VALUES (1, 'Beach week', NULL, 1, 0, 0)")
        conn.execute(
            "INSERT INTO packing_list_template_items VALUES (1, 1, NULL, 2, 'Toothbrush', NULL, 0, 0, 0)"
        )
    return path


def _migrate():
    return _load("migrate_036_add_bag_owners_and_nesting").migrate()


def _migrate_to_models():
    """036, then every later migration the models already reflect."""
    assert _migrate() is True
    assert _load("migrate_037_add_day_bag_removed_from").migrate() is True
    assert _load("migrate_038_add_event_packing_lists").migrate() is True


def _rows(path, sql):
    with sqlite3.connect(path) as conn:
        return conn.execute(sql).fetchall()


def _columns(path, table):
    with sqlite3.connect(path) as conn:
        return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _indexes(path):
    with sqlite3.connect(path) as conn:
        rows = conn.execute(
            "SELECT name, tbl_name, sql FROM sqlite_master"
            " WHERE type='index' AND tbl_name LIKE 'packing_list%' AND sql IS NOT NULL"
        ).fetchall()
    return {name: (table, " ".join(sql.replace('"', "").split())) for name, table, sql in rows}


def test_adds_the_columns_and_tables(db_path):
    assert _migrate() is True
    assert {"owner_id", "parent_bag_id"} <= _columns(db_path, "packing_list_bags")
    with sqlite3.connect(db_path) as conn:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert NEW_TABLES <= tables


def test_writes_no_rows_and_moves_nothing(db_path):
    assert _migrate() is True
    # Every bag has no owner and goes in nothing; the item stays in its bag.
    assert _rows(db_path, "SELECT id, name, owner_id, parent_bag_id FROM packing_list_bags") == [
        (1, "Suitcase", None, None),
        (2, "Toiletries bag", None, None),
    ]
    assert _rows(db_path, "SELECT bag_id FROM packing_list_template_items") == [(2,)]
    for table in NEW_TABLES:
        assert _rows(db_path, f"SELECT COUNT(*) FROM {table}") == [(0,)]


def test_is_idempotent(db_path):
    assert _migrate() is True
    indexes = _indexes(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE packing_list_bags SET owner_id = 3 WHERE id = 1")
    assert _migrate() is True
    assert _indexes(db_path) == indexes
    assert _rows(db_path, "SELECT owner_id FROM packing_list_bags WHERE id = 1") == [(3,)]


def test_a_missing_database_is_not_an_error(tmp_path, monkeypatch):
    monkeypatch.setenv("RALLY_DB_PATH", str(tmp_path / "absent.db"))
    assert _migrate() is True


def test_agrees_with_the_models(db_path, tmp_path):
    """The new tables, their columns and their indexes are what the models make."""
    _migrate_to_models()
    fresh = tmp_path / "fresh.db"
    engine = create_engine(f"sqlite:///{fresh}")
    Base.metadata.create_all(engine)
    engine.dispose()

    assert _indexes(db_path) == _indexes(fresh)
    for table in NEW_TABLES | {"packing_list_bags"}:
        assert _columns(db_path, table) == _columns(fresh, table), table


@pytest.mark.parametrize(
    "statement",
    [
        "INSERT INTO packing_list_template_bags (packing_list_template_id, bag_id, created_at,"
        " updated_at) VALUES (1, 1, 0, 0)",
        "INSERT INTO packing_list_day_bags (day_id, bag_id, created_at, updated_at)"
        " VALUES (1, 1, 0, 0)",
        "INSERT INTO packing_list_day_bag_checks (day_id, bag_id, checked_at) VALUES (1, 1, 0)",
    ],
)
def test_one_reading_and_one_check_per_bag_per_list(db_path, statement):
    """The unique indexes the API relies on: a second row for the same pair fails."""
    _migrate()
    with sqlite3.connect(db_path) as conn:
        conn.execute(statement)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(statement)


def test_the_orm_can_use_what_it_made(db_path):
    _migrate_to_models()
    engine = create_engine(f"sqlite:///{db_path}")
    with Session(engine) as session:
        suitcase = PackingListBag(name="Duffel", owner_id=4)
        session.add(suitcase)
        session.flush()
        pouch = PackingListBag(name="Pouch", parent_bag_id=suitcase.id)
        session.add(pouch)
        session.flush()
        session.add_all(
            [
                PackingListTemplateBag(packing_list_template_id=1, bag_id=suitcase.id, owner_id=4),
                PackingListDayBag(day_id=1, bag_id=pouch.id, parent_bag_id=None),
                PackingListDayBagCheck(day_id=1, bag_id=suitcase.id),
            ]
        )
        session.commit()
        assert session.query(PackingListBag).filter_by(id=pouch.id).one().parent_bag_id == (
            suitcase.id
        )
        assert session.query(PackingListTemplateBag).one().owner_id == 4
    engine.dispose()


def test_a_bag_is_unique_by_name_and_owner(db_path):
    """Emma and Jake can each have a "Backpack"; two ownerless ones cannot
    exist, and nor can two of one person's — ignoring case either way."""
    _migrate()
    with sqlite3.connect(db_path) as conn:
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
        assert "ix_packing_list_bags_name_nocase" not in names
        add = "INSERT INTO packing_list_bags (name, owner_id, created_at, updated_at) VALUES (?, ?, 0, 0)"
        conn.execute(add, ("Backpack", 3))
        conn.execute(add, ("Backpack", 4))
        for name, owner in (("backpack", 3), ("SUITCASE", None)):
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(add, (name, owner))
