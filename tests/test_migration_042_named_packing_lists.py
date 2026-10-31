"""Migration 042 makes every packing list a named list that includes its
templates (#281).

It runs on every container start, so it has to be a no-op the second time and
leave a database whose tables, columns and indexes are what the models make.
What it converts: a day kept in sync with a template becomes a list named
after it that includes it, checks and own changes kept; an event's template
becomes a list named after it; and an old `Every day` placement keeps the days
already made but stops making new ones.
"""

import importlib.util
import json
import pathlib
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from rally import packing_lists as logic
from rally.database import Base
from rally.models import (
    EventPackingList,
    PackingListDay,
    PackingListDayEvent,
    PackingListDayTemplate,
)

MIGRATIONS = pathlib.Path(__file__).resolve().parents[1] / "migrations"
TABLES = (
    "packing_list_days",
    "packing_list_day_templates",
    "event_packing_lists",
    "packing_list_day_events",
)
# Relative to the real date, since the migration reads today from the clock
# (in UTC: the fixture sets no timezone).
TODAY = datetime.now(UTC).date()
PAST = (TODAY - timedelta(days=10)).isoformat()
FIRST = (TODAY + timedelta(days=5)).isoformat()
SECOND = (TODAY + timedelta(days=6)).isoformat()
THIRD = (TODAY + timedelta(days=7)).isoformat()
# How SQLAlchemy writes a DateTime, so the ORM can read the rows back.
STAMP = "2026-10-01 12:00:00.000000"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, MIGRATIONS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _migrate():
    return _load("migrate_042_named_packing_lists").migrate()


def _rows(path, sql, params=()):
    with sqlite3.connect(path) as conn:
        return conn.execute(sql, params).fetchall()


def _columns(path, table):
    with sqlite3.connect(path) as conn:
        return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _indexes(path, table):
    with sqlite3.connect(path) as conn:
        rows = conn.execute(
            "SELECT name, sql FROM sqlite_master WHERE type='index' AND tbl_name=? AND sql IS NOT NULL",
            (table,),
        ).fetchall()
    return {name: " ".join(sql.replace('"', "").split()) for name, sql in rows}


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    """The packing list tables as migrations 035–039 left them, with:

    1. a past day of Swim at Nana's, one item checked;
    2. an upcoming day of Beach day with a label, its own lead time, an
       edited item and an item of its own;
    3. a templateless one-off, Fall concert;
    4–6. Beach day on each date of a three-day `Every day` event, linked;
    and a date of that event taken off by hand (a NULL-day link).
    """
    path = tmp_path / "rally.db"
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE settings (id INTEGER PRIMARY KEY, key VARCHAR, value TEXT,
                updated_at DATETIME);
            CREATE TABLE events (id INTEGER PRIMARY KEY, title VARCHAR(200),
                start_date VARCHAR(10), end_date VARCHAR(10), packing_list_span VARCHAR(10));
            CREATE TABLE event_overrides (id INTEGER PRIMARY KEY, event_id INTEGER,
                occurrence_date VARCHAR(10), start_date VARCHAR(10), end_date VARCHAR(10));
            CREATE TABLE packing_list_templates (id INTEGER PRIMARY KEY, name VARCHAR(100),
                description TEXT, pack_days_before INTEGER);
            CREATE TABLE packing_list_template_items (id INTEGER PRIMARY KEY,
                packing_list_template_id INTEGER, name VARCHAR(200), sort_order INTEGER);
            CREATE TABLE packing_list_days (
                id INTEGER PRIMARY KEY,
                packing_list_template_id INTEGER,
                name VARCHAR(100),
                description TEXT,
                date VARCHAR(10) NOT NULL,
                label VARCHAR(200),
                schedule_id INTEGER,
                pack_days_before INTEGER,
                label_edited BOOLEAN NOT NULL DEFAULT 0,
                item_order JSON,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL
            );
            CREATE INDEX ix_packing_list_days_packing_list_template_id
                ON packing_list_days (packing_list_template_id);
            CREATE INDEX ix_packing_list_days_date ON packing_list_days (date);
            CREATE INDEX ix_packing_list_days_schedule_id ON packing_list_days (schedule_id);
            CREATE UNIQUE INDEX ix_packing_list_days_packing_list_template_date
                ON packing_list_days (packing_list_template_id, date);
            CREATE TABLE packing_list_day_items (id INTEGER PRIMARY KEY, day_id INTEGER,
                template_item_id INTEGER, name VARCHAR(200), removed BOOLEAN, checked BOOLEAN);
            CREATE TABLE packing_list_day_checks (id INTEGER PRIMARY KEY, day_id INTEGER,
                template_item_id INTEGER);
            CREATE TABLE event_packing_lists (
                id INTEGER PRIMARY KEY,
                event_id INTEGER NOT NULL,
                packing_list_template_id INTEGER NOT NULL,
                occurrence_date VARCHAR(10),
                removed BOOLEAN NOT NULL DEFAULT 0,
                created_at DATETIME NOT NULL
            );
            CREATE INDEX ix_event_packing_lists_event_id ON event_packing_lists (event_id);
            CREATE INDEX ix_event_packing_lists_packing_list_template_id
                ON event_packing_lists (packing_list_template_id);
            CREATE UNIQUE INDEX ix_event_packing_lists_event_template_occurrence
                ON event_packing_lists
                (event_id, packing_list_template_id, IFNULL(occurrence_date, ''));
            CREATE TABLE packing_list_day_events (
                id INTEGER PRIMARY KEY,
                day_id INTEGER,
                event_id INTEGER NOT NULL,
                occurrence_date VARCHAR(10) NOT NULL,
                packing_list_template_id INTEGER NOT NULL,
                date VARCHAR(10) NOT NULL,
                created_at DATETIME NOT NULL
            );
            CREATE INDEX ix_packing_list_day_events_day_id ON packing_list_day_events (day_id);
            CREATE INDEX ix_packing_list_day_events_event_id ON packing_list_day_events (event_id);
            CREATE INDEX ix_packing_list_day_events_packing_list_template_id
                ON packing_list_day_events (packing_list_template_id);
            CREATE UNIQUE INDEX ix_packing_list_day_events_unique ON packing_list_day_events
                (event_id, occurrence_date, packing_list_template_id, date);
            """
        )
        conn.executemany(
            "INSERT INTO packing_list_templates (id, name, description, pack_days_before)"
            " VALUES (?, ?, ?, ?)",
            [(1, "Swim at Nana's", "Saturdays", 1), (2, "Beach day", None, 0)],
        )
        conn.executemany(
            "INSERT INTO packing_list_template_items (id, packing_list_template_id, name,"
            " sort_order) VALUES (?, ?, ?, ?)",
            [(1, 1, "Goggles", 0), (2, 2, "Sunscreen", 0), (3, 2, "Umbrella", 1)],
        )
        days = [
            (1, 1, None, PAST, None, None),
            (2, 2, None, FIRST, "Cousins", 3),
            (3, None, "Fall concert", FIRST, "Lawn seats", 1),
            (4, 2, None, SECOND, None, None),
            (5, 2, None, THIRD, None, None),
        ]
        days = [(*row, STAMP, STAMP) for row in days]
        conn.executemany(
            "INSERT INTO packing_list_days (id, packing_list_template_id, name, date, label,"
            " pack_days_before, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            days,
        )
        # Day 6: a day an `Every day` link put on the event's third date.
        conn.execute(
            "INSERT INTO packing_list_days (id, packing_list_template_id, date, created_at,"
            " updated_at) VALUES (6, 1, ?, ?, ?)",
            (SECOND, STAMP, STAMP),
        )
        conn.execute("INSERT INTO packing_list_day_checks (day_id, template_item_id) VALUES (1, 1)")
        conn.executemany(
            "INSERT INTO packing_list_day_items (day_id, template_item_id, name, removed, checked)"
            " VALUES (?, ?, ?, 0, 0)",
            [(2, 3, "Big umbrella"), (2, None, "Frisbee")],
        )
        conn.execute(
            "INSERT INTO events (id, title, start_date, end_date, packing_list_span)"
            " VALUES (1, 'Beach weekend', ?, ?, 'every')",
            (SECOND, THIRD),
        )
        conn.execute(
            "INSERT INTO event_packing_lists (event_id, packing_list_template_id, created_at)"
            " VALUES (1, 2, ?)",
            (STAMP,),
        )
        conn.executemany(
            "INSERT INTO packing_list_day_events (day_id, event_id, occurrence_date,"
            " packing_list_template_id, date, created_at) VALUES (?, 1, ?, 2, ?, ?)",
            [(4, SECOND, SECOND, STAMP), (5, SECOND, THIRD, STAMP)],
        )
        # A date of the event taken off by hand while the others kept it.
        conn.execute(
            "INSERT INTO packing_list_day_events (day_id, event_id, occurrence_date,"
            " packing_list_template_id, date, created_at) VALUES (NULL, 1, ?, 2, ?, ?)",
            (SECOND, "2099-01-01", STAMP),
        )
    monkeypatch.setenv("RALLY_DB_PATH", str(path))
    return path


def test_a_day_kept_in_sync_becomes_a_list_named_after_its_template(db_path):
    assert _migrate() is True
    assert "packing_list_template_id" not in _columns(db_path, "packing_list_days")
    assert _rows(db_path, "SELECT id, name FROM packing_list_days ORDER BY id") == [
        (1, "Swim at Nana's"),
        (2, "Beach day"),
        (3, "Fall concert"),
        (4, "Beach day"),
        (5, "Beach day"),
        (6, "Swim at Nana's"),
    ]
    assert _rows(
        db_path,
        "SELECT day_id, packing_list_template_id, position FROM packing_list_day_templates"
        " ORDER BY day_id",
    ) == [(1, 1, 0), (2, 2, 0), (4, 2, 0), (5, 2, 0), (6, 1, 0)]


def test_moves_nothing_a_day_had(db_path):
    assert _migrate() is True
    assert _rows(db_path, "SELECT label, pack_days_before FROM packing_list_days WHERE id = 2") == [
        ("Cousins", 3)
    ]
    assert _rows(db_path, "SELECT day_id, template_item_id FROM packing_list_day_checks") == [
        (1, 1)
    ]
    assert _rows(
        db_path, "SELECT day_id, template_item_id, name FROM packing_list_day_items ORDER BY id"
    ) == [(2, 3, "Big umbrella"), (2, None, "Frisbee")]


def test_an_events_template_becomes_a_list_named_after_it(db_path):
    assert _migrate() is True
    rows = _rows(
        db_path, "SELECT event_id, list_key, name, template_ids, removed FROM event_packing_lists"
    )
    assert [(e, k, n, json.loads(t), r) for e, k, n, t, r in rows] == [
        (1, "beach day", "Beach day", [2], 0)
    ]


def test_every_day_keeps_its_days_but_makes_no_new_ones(db_path):
    """The first day's link stays; later dates' links go (their days stay, as
    ordinary lists that no longer move with the event), and a date taken off
    by hand has nothing left to record."""
    assert _migrate() is True
    assert _rows(
        db_path, "SELECT day_id, occurrence_date, list_key, date FROM packing_list_day_events"
    ) == [(4, SECOND, "beach day", SECOND)]
    assert {row[0] for row in _rows(db_path, "SELECT id FROM packing_list_days")} >= {4, 5}


def test_the_same_template_may_now_be_on_one_date_twice(db_path):
    assert _migrate() is True
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO packing_list_days (name, date, label_edited, created_at, updated_at)"
            " VALUES ('Dad Goes to South Carolina', ?, 0, 0, 0)",
            (FIRST,),
        )
        day_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            "INSERT INTO packing_list_day_templates (day_id, packing_list_template_id, position,"
            " created_at) VALUES (?, 2, 0, 0)",
            (day_id,),
        )
        # But once per list.
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO packing_list_day_templates (day_id, packing_list_template_id,"
                " position, created_at) VALUES (?, 2, 1, 0)",
                (day_id,),
            )


def test_is_idempotent(db_path):
    assert _migrate() is True
    first = {table: _rows(db_path, f"SELECT * FROM {table} ORDER BY id") for table in TABLES}
    assert _migrate() is True
    assert {table: _rows(db_path, f"SELECT * FROM {table} ORDER BY id") for table in TABLES} == (
        first
    )


def test_the_earlier_migrations_leave_it_alone(db_path):
    """034, 035 and 039 run on every start too, before this one: once it has
    run, they must not look for the columns it removed."""
    assert _migrate() is True
    for name in (
        "migrate_034_add_packing_lists",
        "migrate_035_packing_list_templates",
        "migrate_039_add_event_packing_lists",
    ):
        assert _load(name).migrate() is True, name
    assert "packing_list_template_id" not in _columns(db_path, "packing_list_days")


def test_agrees_with_the_models(db_path, tmp_path):
    assert _migrate() is True
    fresh = tmp_path / "fresh.db"
    engine = create_engine(f"sqlite:///{fresh}")
    Base.metadata.create_all(
        engine,
        tables=[
            PackingListDay.__table__,
            PackingListDayTemplate.__table__,
            EventPackingList.__table__,
            PackingListDayEvent.__table__,
        ],
    )
    engine.dispose()
    for table in TABLES:
        assert _columns(db_path, table) == _columns(fresh, table), table
        assert _indexes(db_path, table) == _indexes(fresh, table), table


def test_the_orm_reads_what_it_made(db_path):
    assert _migrate() is True
    engine = create_engine(f"sqlite:///{db_path}")
    with Session(engine) as session:
        day = session.get(PackingListDay, 2)
        assert (day.name, logic.day_template_ids(session, day.id)) == ("Beach day", [2])
        link = session.query(PackingListDayEvent).one()
        assert link.list_key == "beach day"
    engine.dispose()


def test_a_missing_database_is_not_an_error(tmp_path, monkeypatch):
    monkeypatch.setenv("RALLY_DB_PATH", str(tmp_path / "absent.db"))
    assert _migrate() is True


def test_no_packing_tables_is_not_an_error(tmp_path, monkeypatch):
    path = tmp_path / "rally.db"
    sqlite3.connect(path).close()
    monkeypatch.setenv("RALLY_DB_PATH", str(path))
    assert _migrate() is True


def test_a_failure_part_way_changes_nothing(db_path, monkeypatch):
    """One transaction: an error after the days were rebuilt leaves them as
    they were."""
    migration = _load("migrate_042_named_packing_lists")
    monkeypatch.setattr(migration, "EVENT_LISTS_INDEXES", ("CREATE INDEX broken ON nope (x)",))
    assert migration.migrate() is False
    assert "packing_list_template_id" in _columns(db_path, "packing_list_days")
    assert "packing_list_template_id" in _columns(db_path, "event_packing_lists")
