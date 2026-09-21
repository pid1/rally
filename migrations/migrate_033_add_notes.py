#!/usr/bin/env python3
"""Migration 033: Add the `notes` table.

One free-text note per day, written ahead of the day and read off the
dashboard that morning. See issue #230.

`date` carries a UNIQUE index rather than a plain one, because that index is
the only thing enforcing one note per day — the API's "a date that already has
a note opens it for editing" behavior reads the table first, and two writers
racing would otherwise both pass that check.

The column is `body`, not `note`: `note`/`notes` already means "an annotation
on something else" in `shopping_items.note`, `prep_items.notes` and a schedule
item's `notes` on the dashboard, and this column is the record's whole content.

This migration writes no rows and touches no existing table — the table is new
and starts empty, so there is nothing to back-fill and no upgrade path to get
wrong.

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
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='notes'")
        if cursor.fetchone():
            print("✓ Migration: notes table already exists (idempotent)")
            return True

        print("  Applying migration...")
        cursor.execute("""
            CREATE TABLE notes (
                id INTEGER PRIMARY KEY,
                date VARCHAR(10) NOT NULL UNIQUE,
                body TEXT NOT NULL,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL
            )
        """)
        cursor.execute("CREATE UNIQUE INDEX ix_notes_date ON notes (date)")
        conn.commit()
        print("✓ Migration complete: notes table created")
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
