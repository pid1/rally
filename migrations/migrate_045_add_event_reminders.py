#!/usr/bin/env python3
"""Migration 045: several reminders per event, of any length (#336).

An event had one reminder, `events.notify_minutes_before`. It now has up to
five, as rows in `event_reminders`, and an occurrence can keep a list of its
own. In **one transaction** (SQLite DDL is transactional, so a failure part-way
leaves the database as it was), this:

- creates `event_reminders` and its unique index, which is the no-duplicates
  rule (over `IFNULL`, because SQLite treats NULLs as distinct);
- adds `event_overrides.own_reminders` (`NOT NULL DEFAULT 0`: every existing
  occurrence reads the series' reminders);
- adds `event_notifications.reminder_key` (`NOT NULL DEFAULT ''`) and rebuilds
  `ix_event_notifications_unique` to include it, so each of an occurrence's
  reminders is sent once;
- copies every `notify_minutes_before` from 0 to 35 days into one series
  reminder that fires at the moment the old one did: a timed event's keeps its
  minutes, an all-day event's becomes days before at a time of day (`1440` →
  1 day before at 00:00, `60` → 1 day before at 23:00);
- skips a negative value or one over 35 days, printing each: a negative one is
  not a reminder, and one over 7 days was never sent;
- backfills `reminder_key` on the `reminder` rows already in
  `event_notifications`, so a reminder sent just before the upgrade is not
  sent again just after it;
- drops `events.notify_minutes_before`.

The copy and the drop are in the same transaction, so the column still being
there means nothing has been copied yet. Safe to run multiple times.
"""

import os
import sqlite3
from pathlib import Path

MINUTES_PER_DAY = 24 * 60
MAX_LEAD_MINUTES = 35 * MINUTES_PER_DAY

CREATE_REMINDERS = """
    CREATE TABLE IF NOT EXISTS event_reminders (
        id INTEGER NOT NULL PRIMARY KEY,
        event_id INTEGER NOT NULL,
        occurrence_date VARCHAR(10),
        minutes_before INTEGER,
        days_before INTEGER,
        at_time VARCHAR(5),
        created_at DATETIME NOT NULL
    )
"""
REMINDER_INDEXES = (
    "CREATE INDEX IF NOT EXISTS ix_event_reminders_event_id ON event_reminders (event_id)",
    "CREATE UNIQUE INDEX IF NOT EXISTS ix_event_reminders_unique ON event_reminders"
    " (event_id, IFNULL(occurrence_date, ''), IFNULL(minutes_before, -1),"
    " IFNULL(days_before, -1), IFNULL(at_time, ''))",
)
NOTIFICATION_INDEX = (
    "CREATE UNIQUE INDEX ix_event_notifications_unique ON event_notifications"
    " (event_id, occurrence_date, family_member_id, kind, reminder_key)"
)


def _columns(cursor, table: str) -> set[str]:
    cursor.execute(f"PRAGMA table_info({table})")
    return {col[1] for col in cursor.fetchall()}


def _table_exists(cursor, table: str) -> bool:
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,))
    return cursor.fetchone() is not None


def _index_columns(cursor, index: str) -> list[str]:
    cursor.execute(f"PRAGMA index_info({index})")
    return [row[2] for row in cursor.fetchall()]


def all_day_reminder(minutes: int) -> tuple[int, str]:
    """The days before and time of day that land where ``minutes`` before an
    all-day event's midnight start did."""
    days = -(-minutes // MINUTES_PER_DAY)
    at = days * MINUTES_PER_DAY - minutes
    return days, f"{at // 60:02d}:{at % 60:02d}"


def reminder_key(minutes: int, all_day: bool) -> str:
    if all_day:
        days, at = all_day_reminder(minutes)
        return f"d{days}@{at}"
    return f"m{minutes}"


def migrate():
    """Run the migration. Return True on success, False on failure."""
    db_path = os.environ.get("RALLY_DB_PATH")

    if not db_path:
        prod_path = Path("/data/rally.db")
        dev_path = Path(__file__).parent.parent / "rally.db"
        db_path = str(prod_path) if prod_path.exists() else str(dev_path)

    db_path = Path(db_path)

    if not db_path.exists():
        print(f"✓ Database not found at {db_path}")
        print("  No migration needed - database will be created with correct schema.")
        return True

    print(f"Checking database at {db_path}...")

    conn = sqlite3.connect(db_path, isolation_level=None)
    cursor = conn.cursor()

    try:
        if not _table_exists(cursor, "events"):
            print("✓ Migration: events table does not exist yet (will be created on first run)")
            return True

        cursor.execute("BEGIN")
        changed = []

        if not _table_exists(cursor, "event_reminders"):
            changed.append("event_reminders created")
        cursor.execute(CREATE_REMINDERS)
        for statement in REMINDER_INDEXES:
            cursor.execute(statement)

        if _table_exists(cursor, "event_overrides") and "own_reminders" not in _columns(
            cursor, "event_overrides"
        ):
            cursor.execute(
                "ALTER TABLE event_overrides ADD COLUMN own_reminders BOOLEAN NOT NULL DEFAULT 0"
            )
            changed.append("event_overrides.own_reminders added")

        notifications = _table_exists(cursor, "event_notifications")
        if notifications and "reminder_key" not in _columns(cursor, "event_notifications"):
            cursor.execute(
                "ALTER TABLE event_notifications"
                " ADD COLUMN reminder_key VARCHAR(20) NOT NULL DEFAULT ''"
            )
            changed.append("event_notifications.reminder_key added")
        if notifications and "reminder_key" not in _index_columns(
            cursor, "ix_event_notifications_unique"
        ):
            cursor.execute("DROP INDEX IF EXISTS ix_event_notifications_unique")
            cursor.execute(NOTIFICATION_INDEX)
            changed.append("ix_event_notifications_unique rebuilt")

        if "notify_minutes_before" in _columns(cursor, "events"):
            cursor.execute(
                "SELECT id, title, all_day, notify_minutes_before FROM events"
                " WHERE notify_minutes_before IS NOT NULL ORDER BY id"
            )
            copied = 0
            for event_id, title, all_day, minutes in cursor.fetchall():
                if not 0 <= minutes <= MAX_LEAD_MINUTES:
                    print(
                        f"  Skipped the reminder on event {event_id} ({title!r}):"
                        f" {minutes} minutes is outside 0 to 35 days"
                    )
                    continue
                if all_day:
                    days, at = all_day_reminder(minutes)
                    values = (event_id, None, days, at)
                else:
                    values = (event_id, minutes, None, None)
                cursor.execute(
                    "INSERT INTO event_reminders"
                    " (event_id, occurrence_date, minutes_before, days_before, at_time,"
                    " created_at)"
                    " VALUES (?, NULL, ?, ?, ?, CURRENT_TIMESTAMP)",
                    values,
                )
                if notifications:
                    cursor.execute(
                        "UPDATE event_notifications SET reminder_key = ?"
                        " WHERE event_id = ? AND kind = 'reminder' AND reminder_key = ''",
                        (reminder_key(minutes, bool(all_day)), event_id),
                    )
                copied += 1
            cursor.execute("ALTER TABLE events DROP COLUMN notify_minutes_before")
            changed.append(f"{copied} reminder(s) copied, events.notify_minutes_before dropped")

        cursor.execute("COMMIT")
        if changed:
            print(f"✓ Migration complete: {'; '.join(changed)}")
        else:
            print("✓ Migration: event reminders already in place (idempotent)")
        return True

    except sqlite3.Error as e:
        if conn.in_transaction:
            cursor.execute("ROLLBACK")
        print(f"✗ Migration failed: {e}")
        return False
    finally:
        conn.close()


if __name__ == "__main__":
    import sys

    success = migrate()
    sys.exit(0 if success else 1)
