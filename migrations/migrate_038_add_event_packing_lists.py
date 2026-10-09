#!/usr/bin/env python3
"""Migration 038: Packing lists on calendar events.

Adds:

- ``events.packing_list_span``: where a multi-day occurrence's packing lists
  go, ``first`` (its start date) or ``every`` (each date it covers). NULL reads
  as ``first``.
- ``event_packing_lists``: the templates an event brings, and what one
  occurrence adds or leaves out. The unique index is over
  ``IFNULL(occurrence_date, '')`` because SQLite treats NULLs as distinct and
  the series' own row has to count once.
- ``packing_list_day_events``: the days an event put a list on or adopted,
  which is what makes a day move and go with its event. Unique per event,
  occurrence, template and date.

Purely additive and writes **no rows**: no event brings a list and no day
belongs to an event, so nothing on either page changes until somebody adds a
packing list to an event.

Safe to run multiple times (idempotent).
"""

import os
import sqlite3
from pathlib import Path

STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS event_packing_lists (
        id INTEGER PRIMARY KEY,
        event_id INTEGER NOT NULL,
        packing_list_template_id INTEGER NOT NULL,
        occurrence_date VARCHAR(10),
        removed BOOLEAN NOT NULL DEFAULT 0,
        created_at DATETIME NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_event_packing_lists_event_id ON event_packing_lists (event_id)",
    """
    CREATE INDEX IF NOT EXISTS ix_event_packing_lists_packing_list_template_id
    ON event_packing_lists (packing_list_template_id)
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS ix_event_packing_lists_event_template_occurrence
    ON event_packing_lists (event_id, packing_list_template_id, IFNULL(occurrence_date, ''))
    """,
    """
    CREATE TABLE IF NOT EXISTS packing_list_day_events (
        id INTEGER PRIMARY KEY,
        day_id INTEGER,
        event_id INTEGER NOT NULL,
        occurrence_date VARCHAR(10) NOT NULL,
        packing_list_template_id INTEGER NOT NULL,
        date VARCHAR(10) NOT NULL,
        created_at DATETIME NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_packing_list_day_events_day_id ON packing_list_day_events (day_id)",
    """
    CREATE INDEX IF NOT EXISTS ix_packing_list_day_events_event_id
    ON packing_list_day_events (event_id)
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_packing_list_day_events_packing_list_template_id
    ON packing_list_day_events (packing_list_template_id)
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS ix_packing_list_day_events_unique
    ON packing_list_day_events (event_id, occurrence_date, packing_list_template_id, date)
    """,
)


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
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='events'")
        if not cursor.fetchone():
            # init_db creates events with the column; the new tables stand alone.
            print("✓ events does not exist yet; no column to add")
        else:
            cursor.execute("PRAGMA table_info(events)")
            columns = [col[1] for col in cursor.fetchall()]
            if "packing_list_span" in columns:
                print("✓ events.packing_list_span already exists (idempotent)")
            else:
                cursor.execute("ALTER TABLE events ADD COLUMN packing_list_span VARCHAR(10)")
                print("  Added events.packing_list_span")

        for statement in STATEMENTS:
            cursor.execute(statement)
        conn.commit()
        print("✓ Migration complete: event_packing_lists and packing_list_day_events ready")
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
