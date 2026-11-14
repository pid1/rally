"""Migration 045 moves an event's one reminder into `event_reminders` (#336).

Every reminder from 0 to 35 days becomes one series reminder that fires when
the old one did, out-of-range ones are skipped, the send-once rows learn which
reminder they were, and `events.notify_minutes_before` is dropped — all in one
transaction, safe to run again.
"""

import importlib.util
import pathlib
import sqlite3

import pytest

MIGRATIONS = pathlib.Path(__file__).resolve().parents[1] / "migrations"


def _module():
    spec = importlib.util.spec_from_file_location(
        "migrate_045_add_event_reminders", MIGRATIONS / "migrate_045_add_event_reminders.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _migrate():
    return _module().migrate()


def _columns(path, table):
    with sqlite3.connect(path) as conn:
        return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


EVENTS = [
    # (id, title, all_day, notify_minutes_before)
    (1, "Dentist", 0, 60),
    (2, "Birthday", 1, 60),
    (3, "Camping", 1, 1440),
    (4, "Book fair", 0, None),
    (5, "Far off", 0, 36 * 1440),
    (6, "Odd", 0, -30),
    (7, "Recital", 0, 0),
]


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    """The event tables as migration 044 left them."""
    path = tmp_path / "rally.db"
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE events (id INTEGER PRIMARY KEY, title VARCHAR(200), all_day BOOLEAN,"
            " notify_minutes_before INTEGER, source VARCHAR(200))"
        )
        conn.execute(
            "CREATE TABLE event_overrides (id INTEGER PRIMARY KEY, event_id INTEGER,"
            " occurrence_date VARCHAR(10), cancelled BOOLEAN)"
        )
        conn.execute(
            "CREATE TABLE event_notifications (id INTEGER PRIMARY KEY, event_id INTEGER,"
            " occurrence_date VARCHAR(10), family_member_id INTEGER, kind VARCHAR(20),"
            " status VARCHAR(20), detail VARCHAR(200), created_at DATETIME)"
        )
        conn.execute(
            "CREATE UNIQUE INDEX ix_event_notifications_unique ON event_notifications"
            " (event_id, occurrence_date, family_member_id, kind)"
        )
        conn.executemany(
            "INSERT INTO events (id, title, all_day, notify_minutes_before) VALUES (?, ?, ?, ?)",
            EVENTS,
        )
        conn.executemany(
            "INSERT INTO event_notifications (event_id, occurrence_date, family_member_id, kind,"
            " status) VALUES (?, '2026-08-11', 1, ?, 'sent')",
            [(1, "reminder"), (1, "created"), (2, "reminder")],
        )
    monkeypatch.setenv("RALLY_DB_PATH", str(path))
    return path


def _reminders(path):
    with sqlite3.connect(path) as conn:
        return conn.execute(
            "SELECT event_id, occurrence_date, minutes_before, days_before, at_time"
            " FROM event_reminders ORDER BY event_id"
        ).fetchall()


def test_copies_each_reminder_to_fire_when_it_did(db_path, capsys):
    assert _migrate() is True
    assert _reminders(db_path) == [
        (1, None, 60, None, None),
        (2, None, None, 1, "23:00"),
        (3, None, None, 1, "00:00"),
        (7, None, 0, None, None),
    ]
    out = capsys.readouterr().out
    assert "event 5 ('Far off')" in out
    assert "event 6 ('Odd')" in out


def test_drops_the_old_column_and_adds_the_new_ones(db_path):
    assert _migrate() is True
    assert "notify_minutes_before" not in _columns(db_path, "events")
    assert "own_reminders" in _columns(db_path, "event_overrides")
    assert "reminder_key" in _columns(db_path, "event_notifications")


def test_backfills_which_reminder_was_sent(db_path):
    assert _migrate() is True
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT event_id, kind, reminder_key FROM event_notifications ORDER BY id"
        ).fetchall()
    assert rows == [(1, "reminder", "m60"), (1, "created", ""), (2, "reminder", "d1@23:00")]


def test_the_send_once_index_includes_the_reminder(db_path):
    assert _migrate() is True
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO event_notifications (event_id, occurrence_date, family_member_id, kind,"
            " status, reminder_key) VALUES (1, '2026-08-11', 1, 'reminder', 'sent', 'm1440')"
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO event_notifications (event_id, occurrence_date, family_member_id,"
                " kind, status, reminder_key) VALUES (1, '2026-08-11', 1, 'reminder', 'sent',"
                " 'm60')"
            )


def test_a_duplicate_reminder_is_refused(db_path):
    assert _migrate() is True
    with sqlite3.connect(db_path) as conn, pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO event_reminders (event_id, minutes_before, created_at)"
            " VALUES (1, 60, CURRENT_TIMESTAMP)"
        )


def test_is_idempotent(db_path):
    assert _migrate() is True
    assert _migrate() is True
    assert len(_reminders(db_path)) == 4


def test_a_failure_part_way_leaves_the_database_as_it_was(db_path, monkeypatch):
    module = _module()
    monkeypatch.setattr(
        module, "NOTIFICATION_INDEX", "CREATE UNIQUE INDEX broken ON no_such_table (x)"
    )
    assert module.migrate() is False
    assert "notify_minutes_before" in _columns(db_path, "events")
    assert "own_reminders" not in _columns(db_path, "event_overrides")
    with sqlite3.connect(db_path) as conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master")}
    assert "event_reminders" not in tables


def test_a_database_without_events_is_left_to_init(tmp_path, monkeypatch):
    path = tmp_path / "rally.db"
    sqlite3.connect(path).close()
    monkeypatch.setenv("RALLY_DB_PATH", str(path))
    assert _migrate() is True


@pytest.mark.parametrize(
    ("minutes", "expected"),
    [(0, (0, "00:00")), (60, (1, "23:00")), (1440, (1, "00:00")), (1500, (2, "23:00"))],
)
def test_all_day_conversion(minutes, expected):
    assert _module().all_day_reminder(minutes) == expected
