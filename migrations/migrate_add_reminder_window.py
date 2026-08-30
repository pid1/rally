#!/usr/bin/env python3
"""Migration script to add remind_days_before column to todos and recurring_todos tables.

Run this once to upgrade existing databases. Safe to run multiple times (idempotent).
"""

import os
import sqlite3
import sys
from pathlib import Path


def migrate():
    # Get database path from environment or use default
    db_path = os.environ.get("RALLY_DB_PATH")

    if not db_path:
        # Try production path first, then development
        prod_path = Path("/data/rally.db")
        dev_path = Path(__file__).parent.parent / "rally.db"
        db_path = str(prod_path) if prod_path.exists() else str(dev_path)

    db_path = Path(db_path)

    if not db_path.exists():
        print(f"✓ Database not found at {db_path}")
        print("  No migration needed - database will be created with correct schema on first run.")
        return  # Exit successfully, not an error

    print(f"Checking database at {db_path}...")

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    try:
        # --- todos table ---
        cursor.execute("PRAGMA table_info(todos)")
        todo_columns = [col[1] for col in cursor.fetchall()]

        if "remind_days_before" in todo_columns:
            print("✓ Migration: todos.remind_days_before column already exists (idempotent check)")
        else:
            print("  Adding 'remind_days_before' column to todos table...")
            cursor.execute("ALTER TABLE todos ADD COLUMN remind_days_before INTEGER")
            conn.commit()
            print("✓ Migration complete: todos.remind_days_before column added")

        # --- recurring_todos table ---
        cursor.execute("PRAGMA table_info(recurring_todos)")
        recurring_columns = [col[1] for col in cursor.fetchall()]

        if "remind_days_before" in recurring_columns:
            print(
                "✓ Migration: recurring_todos.remind_days_before column already exists (idempotent check)"
            )
        else:
            print("  Adding 'remind_days_before' column to recurring_todos table...")
            cursor.execute("ALTER TABLE recurring_todos ADD COLUMN remind_days_before INTEGER")
            conn.commit()
            print("✓ Migration complete: recurring_todos.remind_days_before column added")

        return True

    except sqlite3.Error as e:
        print(f"✗ Migration failed: {e}")
        return False
    finally:
        conn.close()


if __name__ == "__main__":
    success = migrate()
    sys.exit(0 if success else 1)
