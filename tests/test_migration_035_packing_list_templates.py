"""Migration 035 renames the template tables, lets a day exist without one,
and recounts item history — and, after 034 and with 036, agrees with the models.

It runs on every container start, so it has to be a no-op the second time,
and it has to leave a database the ORM can use: a table shaped differently
from ``models.py`` would only fail once somebody wrote to it. The data a
family already has must come through untouched, except the counts
autocomplete ranks by, which are recounted under their new meaning.

Migration 036 adds bag owners and nesting on top, so the two tests that
compare against ``models.py`` run it too (``_migrate_to_models``): after 036
the migrations and the models only agree together.
"""

import importlib.util
import pathlib
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from rally.database import Base
from rally.models import (
    PackingListBag,
    PackingListDay,
    PackingListDayCheck,
    PackingListDayItem,
    PackingListItemHistory,
    PackingListTemplate,
    PackingListTemplateItem,
    PackingListTemplateSchedule,
    Setting,
)

MIGRATIONS = pathlib.Path(__file__).resolve().parents[1] / "migrations"

TABLES = {
    "packing_list_templates",
    "packing_list_bags",
    "packing_list_template_items",
    "packing_list_item_history",
    "packing_list_template_schedules",
    "packing_list_days",
    "packing_list_day_items",
    "packing_list_day_checks",
}

TODAY = datetime.now(UTC).date()
PAST = (TODAY - timedelta(days=3)).isoformat()
OLDER = (TODAY - timedelta(days=10)).isoformat()
COMING = (TODAY + timedelta(days=2)).isoformat()


def _load(name):
    spec = importlib.util.spec_from_file_location(name, MIGRATIONS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    """A database as migration 034 left it, with a family's packing lists in
    it: a template on two past days and one coming up, a day's own edits,
    removals, additions and checks, a schedule, and history."""
    path = tmp_path / "rally.db"
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE settings (key VARCHAR(100) PRIMARY KEY, value TEXT, updated_at DATETIME)"
        )
        conn.execute("INSERT INTO settings VALUES ('local_timezone', 'UTC', 0)")
    monkeypatch.setenv("RALLY_DB_PATH", str(path))
    assert _load("migrate_034_add_packing_lists").migrate() is True
    with sqlite3.connect(path) as conn:
        for statement in (
            "INSERT INTO packing_lists VALUES (1, 'Swim at Nana''s', 'Saturdays', 1, 0, 0)",
            "INSERT INTO packing_list_bags VALUES (1, 'Pool bag', 0, 0)",
            "INSERT INTO packing_list_items VALUES (1, 1, NULL, 1, 'Goggles', NULL, 0, 0, 0)",
            "INSERT INTO packing_list_items VALUES (2, 1, NULL, 1, 'Towel', NULL, 1, 0, 0)",
            "INSERT INTO packing_list_items VALUES (3, 1, NULL, NULL, 'Snacks', NULL, 2, 0, 0)",
            "INSERT INTO packing_list_schedules (id, packing_list_id, recurrence_type, active,"
            " created_at, updated_at) VALUES (1, 1, 'weekly', 1, 0, 0)",
            f"INSERT INTO packing_list_days VALUES (1, 1, '{OLDER}', NULL, NULL, NULL, 0, 0, 0)",
            f"INSERT INTO packing_list_days VALUES (2, 1, '{PAST}', 'Cousins', 1, 2, 1, 0, 0)",
            f"INSERT INTO packing_list_days VALUES (3, 1, '{COMING}', NULL, 1, NULL, 0, 0, 0)",
            # Day 2: towel renamed, snacks removed, a sun hat of its own.
            "INSERT INTO packing_list_day_items (day_id, item_id, name, sort_order, removed,"
            " checked, created_at, updated_at) VALUES (2, 2, 'Beach towel', 0, 0, 0, 0, 0)",
            "INSERT INTO packing_list_day_items (day_id, item_id, name, sort_order, removed,"
            " checked, created_at, updated_at) VALUES (2, 3, 'Snacks', 0, 1, 0, 0, 0)",
            "INSERT INTO packing_list_day_items (day_id, item_id, name, sort_order, removed,"
            " checked, created_at, updated_at) VALUES (2, NULL, 'Sun hat', 0, 0, 1, 0, 0)",
            "INSERT INTO packing_list_day_checks (day_id, item_id, checked_at) VALUES (2, 1, 0)",
            "INSERT INTO packing_list_day_checks (day_id, item_id, checked_at) VALUES (3, 2, 0)",
            # History, with counts from the old "times typed" meaning. Snacks
            # was forgotten (×), so it has no row and must not get one.
            "INSERT INTO packing_list_item_history (name, name_key, times_added, last_added_at)"
            " VALUES ('Goggles', 'goggles', 9, 0)",
            "INSERT INTO packing_list_item_history (name, name_key, times_added, last_added_at)"
            " VALUES ('Towel', 'towel', 9, 0)",
            "INSERT INTO packing_list_item_history (name, name_key, times_added, last_added_at)"
            " VALUES ('Sun hat', 'sun hat', 9, 0)",
            "INSERT INTO packing_list_item_history (name, name_key, times_added, last_added_at)"
            " VALUES ('Floatie', 'floatie', 9, 0)",
        ):
            conn.execute(statement)
    return path


def _migrate():
    return _load("migrate_035_packing_list_templates").migrate()


def _migrate_to_models():
    """035, then every later migration the models already reflect."""
    assert _migrate() is True
    assert _load("migrate_036_add_bag_owners_and_nesting").migrate() is True
    assert _load("migrate_037_add_day_bag_removed_from").migrate() is True
    assert _load("migrate_038_add_event_packing_lists").migrate() is True


def _tables(path):
    with sqlite3.connect(path) as conn:
        return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _indexes(path):
    """Every index on a packing list table: name → (table, its SQL, normalized)."""
    with sqlite3.connect(path) as conn:
        rows = conn.execute(
            "SELECT name, tbl_name, sql FROM sqlite_master"
            " WHERE type='index' AND tbl_name LIKE 'packing_list%' AND sql IS NOT NULL"
        ).fetchall()
    return {name: (table, " ".join(sql.replace('"', "").split())) for name, table, sql in rows}


def _rows(path, sql):
    with sqlite3.connect(path) as conn:
        return conn.execute(sql).fetchall()


def test_renames_the_template_tables_and_columns(db_path):
    assert _migrate() is True

    tables = _tables(db_path)
    assert TABLES <= tables
    assert not {"packing_lists", "packing_list_items", "packing_list_schedules"} & tables
    with sqlite3.connect(db_path) as conn:
        columns = {
            table: {row[1]: row for row in conn.execute(f"PRAGMA table_info({table})")}
            for table in TABLES
        }
    assert "packing_list_template_id" in columns["packing_list_template_items"]
    assert "packing_list_template_id" in columns["packing_list_template_schedules"]
    assert "template_item_id" in columns["packing_list_day_items"]
    assert "template_item_id" in columns["packing_list_day_checks"]
    days = columns["packing_list_days"]
    assert {"name", "description", "item_order"} <= set(days)
    assert days["packing_list_template_id"][3] == 0  # notnull dropped


def test_moves_no_data(db_path):
    _migrate()

    assert _rows(
        db_path,
        "SELECT id, packing_list_template_id, date, label, schedule_id,"
        " pack_days_before, label_edited, name, item_order"
        " FROM packing_list_days ORDER BY id",
    ) == [
        (1, 1, OLDER, None, None, None, 0, None, None),
        (2, 1, PAST, "Cousins", 1, 2, 1, None, None),
        (3, 1, COMING, None, 1, None, 0, None, None),
    ]
    assert _rows(
        db_path, "SELECT day_id, template_item_id FROM packing_list_day_checks ORDER BY day_id"
    ) == [(2, 1), (3, 2)]
    assert _rows(
        db_path, "SELECT template_item_id, name, removed FROM packing_list_day_items ORDER BY id"
    ) == [(2, "Beach towel", 0), (3, "Snacks", 1), (None, "Sun hat", 0)]
    assert _rows(db_path, "SELECT name FROM packing_list_templates") == [("Swim at Nana's",)]


def test_recounts_history_as_past_days_on_a_list(db_path):
    _migrate()

    counts = dict(_rows(db_path, "SELECT name_key, times_added FROM packing_list_item_history"))
    # Two past days. The older reads Goggles, Towel, Snacks; the later reads
    # Goggles, Beach towel and its own Sun hat, with Snacks removed. The
    # coming day is not over yet, so it counts for nothing.
    assert counts == {"goggles": 2, "towel": 1, "sun hat": 1, "floatie": 0}
    # Snacks was forgotten; a recount brings nothing back, and nor does a
    # name only a day typed ("Beach towel" was never in history).
    assert _rows(db_path, "SELECT COUNT(*) FROM packing_list_item_history") == [(4,)]
    assert _rows(
        db_path, "SELECT value FROM settings WHERE key = 'packing_history_counted_through'"
    ) == [((TODAY - timedelta(days=1)).isoformat(),)]


def test_is_idempotent(db_path):
    assert _migrate() is True
    snapshot = (
        _tables(db_path),
        _indexes(db_path),
        _rows(db_path, "SELECT * FROM packing_list_item_history ORDER BY id"),
        _rows(db_path, "SELECT * FROM packing_list_days ORDER BY id"),
    )
    # The app counts a day into history after the migration; a second run of
    # the migration must not count it again.
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE packing_list_item_history SET times_added = 40 WHERE name_key = 'towel'"
        )
    assert _migrate() is True
    assert _tables(db_path) == snapshot[0]
    assert _indexes(db_path) == snapshot[1]
    assert (
        dict(_rows(db_path, "SELECT name_key, times_added FROM packing_list_item_history"))["towel"]
        == 40
    )
    assert _rows(db_path, "SELECT * FROM packing_list_days ORDER BY id") == snapshot[3]


def test_a_missing_database_is_not_an_error(tmp_path, monkeypatch):
    monkeypatch.setenv("RALLY_DB_PATH", str(tmp_path / "absent.db"))
    assert _migrate() is True


def test_agrees_with_the_models(db_path, tmp_path):
    """Every packing list index the models create exists after 034 + 035
    (+ 036), under the same name, over the same columns, and no other is left."""
    _migrate_to_models()
    fresh = tmp_path / "fresh.db"
    engine = create_engine(f"sqlite:///{fresh}")
    Base.metadata.create_all(engine)
    engine.dispose()

    assert _indexes(db_path) == _indexes(fresh)
    with sqlite3.connect(db_path) as migrated, sqlite3.connect(fresh) as modeled:
        for table in TABLES:
            ours = {row[1] for row in migrated.execute(f"PRAGMA table_info({table})")}
            theirs = {row[1] for row in modeled.execute(f"PRAGMA table_info({table})")}
            assert ours == theirs, table


def test_the_orm_can_use_what_it_made(db_path):
    _migrate_to_models()
    engine = create_engine(f"sqlite:///{db_path}")
    with Session(engine) as session:
        template = PackingListTemplate(name="Beach day", pack_days_before=1)
        bag = PackingListBag(name="Beach tote")
        session.add_all([template, bag])
        session.flush()
        item = PackingListTemplateItem(
            packing_list_template_id=template.id, owner_id=1, bag_id=bag.id, name="Umbrella"
        )
        schedule = PackingListTemplateSchedule(
            packing_list_template_id=template.id,
            recurrence_type="custom",
            custom_rule={"freq": "weekly", "interval": 1, "weekdays": [5]},
        )
        session.add_all([item, schedule])
        session.flush()
        day = PackingListDay(
            packing_list_template_id=template.id,
            date=COMING,
            schedule_id=schedule.id,
            item_order=[f"template:{item.id}"],
        )
        templateless = PackingListDay(
            packing_list_template_id=None, name="Concert", date=COMING, pack_days_before=0
        )
        session.add_all([day, templateless])
        session.flush()
        session.add_all(
            [
                PackingListDayCheck(day_id=day.id, template_item_id=item.id),
                PackingListDayItem(day_id=templateless.id, name="Ear plugs"),
                PackingListItemHistory(name="Umbrella", name_key="umbrella"),
            ]
        )
        session.commit()
        assert session.query(PackingListDay).filter_by(id=day.id).one().item_order == [
            f"template:{item.id}"
        ]
        assert (
            session.query(PackingListItemHistory).filter_by(name_key="umbrella").one().times_added
            == 0
        )
        assert session.query(Setting).filter_by(key="packing_history_counted_through").one()
    engine.dispose()


@pytest.mark.parametrize(
    "statement",
    [
        "INSERT INTO packing_list_templates (name, pack_days_before, created_at, updated_at)"
        " VALUES ('SWIM AT NANA''S', 0, 0, 0)",
        "INSERT INTO packing_list_templates (name, pack_days_before, created_at, updated_at)"
        " VALUES ('Other', -1, 0, 0)",
        f"INSERT INTO packing_list_days (packing_list_template_id, date, created_at, updated_at)"
        f" VALUES (1, '{COMING}', 0, 0)",
        "INSERT INTO packing_list_day_checks (day_id, template_item_id, checked_at) VALUES (2, 1, 0)",
        "INSERT INTO packing_list_template_schedules (packing_list_template_id, recurrence_type,"
        " active, created_at, updated_at) VALUES (1, 'daily', 1, 0, 0)",
        "INSERT INTO packing_list_day_items (day_id, template_item_id, name, sort_order, removed,"
        " checked, created_at, updated_at) VALUES (2, 2, 'Towel', 0, 0, 0, 0, 0)",
    ],
)
def test_the_rules_the_api_relies_on_still_hold(db_path, statement):
    _migrate()
    with sqlite3.connect(db_path) as conn, pytest.raises(sqlite3.IntegrityError):
        conn.execute(statement)


def test_templateless_days_may_share_a_date(db_path):
    _migrate()
    with sqlite3.connect(db_path) as conn:
        for name in ("Concert", "Theme park"):
            conn.execute(
                "INSERT INTO packing_list_days (packing_list_template_id, name, date,"
                " pack_days_before, label_edited, created_at, updated_at)"
                " VALUES (NULL, ?, ?, 0, 0, 0, 0)",
                (name, COMING),
            )
    assert _rows(
        db_path, "SELECT COUNT(*) FROM packing_list_days WHERE packing_list_template_id IS NULL"
    ) == [(2,)]


def test_a_failure_part_way_changes_nothing(db_path, monkeypatch):
    """One transaction: an error after the renames leaves the old names."""
    migration = _load("migrate_035_packing_list_templates")
    monkeypatch.setattr(migration, "NEW_INDEXES", ["CREATE INDEX broken ON no_such_table (x)"])
    assert migration.migrate() is False
    assert {"packing_lists", "packing_list_items", "packing_list_schedules"} <= _tables(db_path)
    assert "packing_list_templates" not in _tables(db_path)
