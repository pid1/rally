#!/usr/bin/env python3
"""Migration: Add custom_rule column to recurring_todos table.

Stores the JSON rule for custom recurrence types (daily-interval,
weekly-interval with weekday selection, monthly-day, monthly-weekday).

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
        cursor.execute("PRAGMA table_info(recurring_todos)")
        columns = [col[1] for col in cursor.fetchall()]

        if not columns:
            print("✓ recurring_todos table does not exist yet (migration 004 will create it)")
            print("  No migration needed - table will be created with correct schema.")
            return True

        if "custom_rule" in columns:
            print("✓ Migration: recurring_todos.custom_rule already exists (idempotent)")
        else:
            print("  Adding 'custom_rule' column to recurring_todos table...")
            cursor.execute("ALTER TABLE recurring_todos ADD COLUMN custom_rule TEXT")
            conn.commit()
            print("✓ Migration complete: recurring_todos.custom_rule added")

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
