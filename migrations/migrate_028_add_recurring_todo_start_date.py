#!/usr/bin/env python3
"""Migration 028: Add `recurring_todos.start_date`.

The day the first instance of a series is due, with the cadence counted from
there — "replace the smoke detector battery every 12 months, starting 1 January
2027". A wall-calendar date stored as text, exactly like `last_generated_date`
and `todos.due_date`, so no timezone is ever applied to it twice.

Purely additive and no rows are written: NULL means "start from today", which
is what every existing template already does.

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
            "SELECT name FROM sqlite_master WHERE type='table' AND name='recurring_todos'"
        )
        if not cursor.fetchone():
            print(
                "✓ recurring_todos does not exist yet; migration 004 will create it with the column"
            )
            return True

        cursor.execute("PRAGMA table_info(recurring_todos)")
        columns = [col[1] for col in cursor.fetchall()]

        if "start_date" in columns:
            print("✓ Migration: recurring_todos.start_date already exists (idempotent)")
            return True

        print("  Applying migration...")
        cursor.execute("ALTER TABLE recurring_todos ADD COLUMN start_date VARCHAR(10)")
        conn.commit()
        print("✓ Migration complete: recurring_todos.start_date added")
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
