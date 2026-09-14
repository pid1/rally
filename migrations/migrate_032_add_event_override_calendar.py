#!/usr/bin/env python3
"""Migration 032: Add `event_overrides.calendar_id`.

Which calendar an event sits on decides its color, its owner name and — when
nobody was named explicitly — its attendee list, which is what the member
filter matches on. Until now that was a property of the *series* alone, so
"put this one Tuesday on Sam's calendar" had nowhere to be stored.

`calendar_id` is the series' calendar for that one occurrence, overriding it
the same way `title` and `start_utc` already do. NULL means **inherit from the
series**, which is the rule every other nullable column in this table follows
and the reason this migration writes no rows: an existing override keeps NULL
and resolves to exactly the calendar it renders on today. Nothing moves,
recolors, or drops out of a filter as a result of upgrading.

No foreign key, matching `event_attendees`, `member_notification_prefs` and
`shopping_items.store_id`: the schema does not use them anywhere. A stray id
is handled where it is read rather than by the engine — the API refuses to
write one that is not a native calendar, and the expansion falls back to the
series' own calendar when a row points at something that no longer exists, so
a deleted calendar degrades to "on the series' calendar" rather than to an
occurrence nothing can draw.

Safe to run multiple times (idempotent).
"""

import os
import sqlite3
from pathlib import Path


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
            "SELECT name FROM sqlite_master WHERE type='table' AND name='event_overrides'"
        )
        if not cursor.fetchone():
            print(
                "✓ event_overrides does not exist yet; migration 020 will create it with the column"
            )
            return True

        cursor.execute("PRAGMA table_info(event_overrides)")
        columns = [col[1] for col in cursor.fetchall()]

        if "calendar_id" in columns:
            print("✓ Migration: event_overrides.calendar_id already exists (idempotent)")
            return True

        print("  Applying migration...")
        cursor.execute("ALTER TABLE event_overrides ADD COLUMN calendar_id INTEGER")
        conn.commit()
        print("✓ Migration complete: event_overrides.calendar_id added")
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
