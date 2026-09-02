#!/usr/bin/env python3
"""Migration: Add native calendaring — Rally-owned events, and Pushover profiles.

Creates the four tables the calendar feature stores (`events`,
`event_attendees`, `event_overrides`, `event_notifications`), adds the
per-family-member Pushover columns, and seeds one native calendar per existing
family member so there is somewhere to write on first boot.

Purely additive: no existing row is modified and nothing is dropped. Native
calendars are rows in the existing `calendars` table with `cal_type='native'`
and no URL, so accepting them needs no schema change — `cal_type` is a bare
VARCHAR with no CHECK constraint.

Safe to run multiple times (idempotent).
"""

import os
import sqlite3
from pathlib import Path


def _columns(cursor, table):
    # nosemgrep: python.lang.security.audit.formatted-sql-query.formatted-sql-query,python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query
    # `table` is a literal passed by this migration, never user input, and
    # PRAGMA does not accept a bound parameter for the table name.
    cursor.execute(f"PRAGMA table_info({table})")
    return [col[1] for col in cursor.fetchall()]


def _table_exists(cursor, table):
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,))
    return cursor.fetchone() is not None


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

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    try:
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY,
                calendar_id INTEGER NOT NULL,
                uid VARCHAR(200) NOT NULL,
                title VARCHAR(200) NOT NULL,
                description TEXT,
                location VARCHAR(200),
                all_day BOOLEAN DEFAULT 0,
                start_utc DATETIME NOT NULL,
                end_utc DATETIME NOT NULL,
                start_date VARCHAR(10) NOT NULL,
                end_date VARCHAR(10) NOT NULL,
                tzid VARCHAR(64) DEFAULT 'UTC',
                rrule TEXT,
                series_end_date VARCHAR(10),
                notify_minutes_before INTEGER,
                created_at DATETIME,
                updated_at DATETIME
            )
            """
        )
        cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS ix_events_uid ON events(uid)")
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS ix_events_calendar_start ON events(calendar_id, start_date)"
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS event_attendees (
                id INTEGER PRIMARY KEY,
                event_id INTEGER NOT NULL,
                family_member_id INTEGER NOT NULL,
                created_at DATETIME
            )
            """
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS ix_event_attendees_event_id ON event_attendees(event_id)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS ix_event_attendees_family_member_id "
            "ON event_attendees(family_member_id)"
        )
        cursor.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS ix_event_attendees_unique "
            "ON event_attendees(event_id, family_member_id)"
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS event_overrides (
                id INTEGER PRIMARY KEY,
                event_id INTEGER NOT NULL,
                occurrence_date VARCHAR(10) NOT NULL,
                cancelled BOOLEAN DEFAULT 0,
                title VARCHAR(200),
                description TEXT,
                location VARCHAR(200),
                all_day BOOLEAN,
                start_utc DATETIME,
                end_utc DATETIME,
                start_date VARCHAR(10),
                end_date VARCHAR(10),
                created_at DATETIME,
                updated_at DATETIME
            )
            """
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS ix_event_overrides_event_id ON event_overrides(event_id)"
        )
        cursor.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS ix_event_overrides_unique "
            "ON event_overrides(event_id, occurrence_date)"
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS event_notifications (
                id INTEGER PRIMARY KEY,
                event_id INTEGER NOT NULL,
                occurrence_date VARCHAR(10) NOT NULL,
                family_member_id INTEGER NOT NULL,
                kind VARCHAR(20) NOT NULL,
                status VARCHAR(20) DEFAULT 'sent',
                detail VARCHAR(200),
                created_at DATETIME
            )
            """
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS ix_event_notifications_event_id "
            "ON event_notifications(event_id)"
        )
        # This index IS the send-once guarantee for reminders.
        cursor.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS ix_event_notifications_unique "
            "ON event_notifications(event_id, occurrence_date, family_member_id, kind)"
        )
        print("✓ Migration: event tables present")

        member_columns = _columns(cursor, "family_members")
        if "pushover_user_key" not in member_columns:
            cursor.execute("ALTER TABLE family_members ADD COLUMN pushover_user_key VARCHAR(64)")
            print("✓ Migration: family_members.pushover_user_key added")
        else:
            print("✓ Migration: family_members.pushover_user_key already exists (idempotent)")

        if "pushover_device" not in member_columns:
            cursor.execute("ALTER TABLE family_members ADD COLUMN pushover_device VARCHAR(64)")
            print("✓ Migration: family_members.pushover_device added")
        else:
            print("✓ Migration: family_members.pushover_device already exists (idempotent)")

        # Seed one native calendar per family member that has none, so the
        # feature has somewhere to write the moment it is deployed.
        if _table_exists(cursor, "calendars") and _table_exists(cursor, "family_members"):
            cursor.execute(
                """
                SELECT fm.id, fm.name FROM family_members fm
                WHERE NOT EXISTS (
                    SELECT 1 FROM calendars c
                    WHERE c.family_member_id = fm.id AND c.cal_type = 'native'
                )
                """
            )
            pending = cursor.fetchall()
            for member_id, name in pending:
                cursor.execute(
                    """
                    INSERT INTO calendars
                        (label, url, family_member_id, cal_type, created_at, updated_at)
                    VALUES (?, '', ?, 'native', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                    """,
                    (f"{name}'s Calendar", member_id),
                )
            if pending:
                print(f"✓ Migration: seeded {len(pending)} native calendar(s)")
            else:
                print("✓ Migration: native calendars already seeded (idempotent)")

        conn.commit()
        print("✓ Migration complete: native calendaring")
        return True

    except sqlite3.Error as e:
        print(f"✗ Migration failed: {e}")
        return False
    finally:
        conn.close()


if __name__ == "__main__":
    import sys

    success = migrate()
    sys.exit(0 if success else 1)
