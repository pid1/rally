"""Migration 034 creates the packing list tables, and agrees with the models.

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
    PackingList,
    PackingListBag,
    PackingListDay,
    PackingListDayCheck,
    PackingListDayItem,
    PackingListItem,
    PackingListItemHistory,
    PackingListSchedule,
)

TABLES = {
    "packing_lists",
    "packing_list_bags",
    "packing_list_items",
    "packing_list_item_history",
    "packing_list_schedules",
    "packing_list_days",
    "packing_list_day_items",
    "packing_list_day_checks",
}


def _load_migration():
    path = (
        pathlib.Path(__file__).resolve().parents[1]
        / "migrations"
        / "migrate_034_add_packing_lists.py"
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
        packing_list = PackingList(name="Beach day", pack_days_before=1)
        bag = PackingListBag(name="Pool bag")
        session.add_all([packing_list, bag])
        session.flush()
        item = PackingListItem(
            packing_list_id=packing_list.id, owner_id=1, bag_id=bag.id, name="Towel"
        )
        schedule = PackingListSchedule(
            packing_list_id=packing_list.id,
            recurrence_type="custom",
            custom_rule={"freq": "weekly", "interval": 1, "weekdays": [5]},
        )
        session.add_all([item, schedule])
        session.flush()
        day = PackingListDay(
            packing_list_id=packing_list.id,
            date="2026-10-03",
            schedule_id=schedule.id,
            pack_days_before=2,
        )
        session.add(day)
        session.flush()
        session.add_all(
            [
                PackingListDayCheck(day_id=day.id, item_id=item.id),
                PackingListDayItem(day_id=day.id, name="Sun hat", owner_id=2, bag_id=bag.id),
                PackingListItemHistory(name="Towel", name_key="towel", bag_id=bag.id),
            ]
        )
        session.commit()
        assert session.query(PackingListSchedule).one().active is True
    engine.dispose()


@pytest.mark.parametrize(
    "statement",
    [
        "INSERT INTO packing_lists (name, pack_days_before, created_at, updated_at)"
        " VALUES ('BEACH DAY', 0, 0, 0)",
        "INSERT INTO packing_lists (name, pack_days_before, created_at, updated_at)"
        " VALUES ('Other', -1, 0, 0)",
        "INSERT INTO packing_list_bags (name, created_at, updated_at) VALUES ('POOL BAG', 0, 0)",
        "INSERT INTO packing_list_days (packing_list_id, date, created_at, updated_at)"
        " VALUES (1, '2026-10-03', 0, 0)",
        "INSERT INTO packing_list_day_checks (day_id, item_id, checked_at) VALUES (1, 1, 0)",
        "INSERT INTO packing_list_schedules (packing_list_id, recurrence_type, active,"
        " created_at, updated_at) VALUES (1, 'weekly', 1, 0, 0)",
        "INSERT INTO packing_list_day_items (day_id, item_id, name, sort_order, removed, checked,"
        " created_at, updated_at) VALUES (1, 7, 'Goggles', 0, 1, 0, 0, 0)",
        "INSERT INTO packing_list_item_history (name, name_key, times_added, last_added_at)"
        " VALUES ('TOWEL', 'towel', 1, 0)",
    ],
)
def test_the_rules_the_api_relies_on_hold_in_the_database(db_path, statement):
    _load_migration().migrate()
    with sqlite3.connect(db_path) as conn:
        for setup in (
            "INSERT INTO packing_lists (id, name, pack_days_before, created_at, updated_at)"
            " VALUES (1, 'Beach day', 0, 0, 0)",
            "INSERT INTO packing_list_bags (name, created_at, updated_at) VALUES ('Pool bag', 0, 0)",
            "INSERT INTO packing_list_days (packing_list_id, date, created_at, updated_at)"
            " VALUES (1, '2026-10-03', 0, 0)",
            "INSERT INTO packing_list_day_checks (day_id, item_id, checked_at) VALUES (1, 1, 0)",
            "INSERT INTO packing_list_schedules (packing_list_id, recurrence_type, active,"
            " created_at, updated_at) VALUES (1, 'daily', 1, 0, 0)",
            "INSERT INTO packing_list_day_items (day_id, item_id, name, sort_order, removed, checked,"
            " created_at, updated_at) VALUES (1, 7, 'Goggles', 0, 0, 0, 0, 0)",
            "INSERT INTO packing_list_item_history (name, name_key, times_added, last_added_at)"
            " VALUES ('Towel', 'towel', 1, 0)",
        ):
            conn.execute(setup)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(statement)


def test_a_day_may_add_any_number_of_its_own_items(db_path):
    """The (day_id, item_id) index is partial: a day's own items have no item_id."""
    _load_migration().migrate()
    with sqlite3.connect(db_path) as conn:
        for name in ("Sun hat", "Floatie"):
            conn.execute(
                "INSERT INTO packing_list_day_items (day_id, name, sort_order, removed, checked,"
                " created_at, updated_at) VALUES (1, ?, 0, 0, 0, 0, 0)",
                (name,),
            )
