"""Migration 038 adds packing lists on calendar events (#270).

It runs on every container start, so it has to be a no-op the second time,
write no rows — no event brings a list until somebody adds one — and leave a
database whose new tables, columns and indexes are what the models make.
"""

import importlib.util
import pathlib
import sqlite3

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from rally.database import Base
from rally.models import Event, EventPackingList, PackingListDayEvent

MIGRATIONS = pathlib.Path(__file__).resolve().parents[1] / "migrations"
TABLES = ("event_packing_lists", "packing_list_day_events")


def _load(name):
    spec = importlib.util.spec_from_file_location(name, MIGRATIONS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _migrate():
    return _load("migrate_038_add_event_packing_lists").migrate()


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
    """A database with an events table as migration 020 made it, and one event."""
    path = tmp_path / "rally.db"
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE events (id INTEGER PRIMARY KEY, calendar_id INTEGER, uid VARCHAR(200),"
            " title VARCHAR(200), start_date VARCHAR(10))"
        )
        conn.execute(
            "INSERT INTO events (calendar_id, uid, title, start_date)"
            " VALUES (1, 'x', 'Swim', '2026-10-10')"
        )
    monkeypatch.setenv("RALLY_DB_PATH", str(path))
    return path


def test_adds_the_column_and_tables(db_path):
    assert _migrate() is True
    assert "packing_list_span" in _columns(db_path, "events")
    for table in TABLES:
        assert _columns(db_path, table)


def test_writes_no_rows(db_path):
    assert _migrate() is True
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT packing_list_span FROM events").fetchall() == [(None,)]
        for table in TABLES:
            assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone() == (0,)


def test_is_idempotent(db_path):
    assert _migrate() is True
    assert _migrate() is True


def test_agrees_with_the_models(db_path, tmp_path):
    assert _migrate() is True
    fresh = tmp_path / "fresh.db"
    engine = create_engine(f"sqlite:///{fresh}")
    Base.metadata.create_all(engine, tables=[Event.__table__, *_model_tables()])
    engine.dispose()
    for table in TABLES:
        assert _columns(db_path, table) == _columns(fresh, table)
        assert _indexes(db_path, table) == _indexes(fresh, table)
    assert "packing_list_span" in _columns(fresh, "events")


def test_leaves_a_database_the_orm_can_use(db_path):
    assert _migrate() is True
    engine = create_engine(f"sqlite:///{db_path}")
    with Session(engine) as session:
        session.add(EventPackingList(event_id=1, packing_list_template_id=1))
        session.add(
            PackingListDayEvent(
                day_id=1,
                event_id=1,
                occurrence_date="2026-10-10",
                packing_list_template_id=1,
                date="2026-10-10",
            )
        )
        session.commit()
    engine.dispose()


def test_the_series_row_is_unique(db_path):
    """``IFNULL`` makes the series' own row (no occurrence date) count once."""
    assert _migrate() is True
    with sqlite3.connect(db_path) as conn:
        insert = (
            "INSERT INTO event_packing_lists (event_id, packing_list_template_id, removed, created_at)"
            " VALUES (1, 1, 0, 0)"
        )
        conn.execute(insert)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(insert)


def test_without_events_still_creates_the_tables(tmp_path, monkeypatch):
    path = tmp_path / "rally.db"
    sqlite3.connect(path).close()
    monkeypatch.setenv("RALLY_DB_PATH", str(path))
    assert _migrate() is True
    for table in TABLES:
        assert _columns(path, table)


def _model_tables():
    return [EventPackingList.__table__, PackingListDayEvent.__table__]
