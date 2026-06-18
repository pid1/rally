#!/usr/bin/env python3
"""Migration: Add completed_at column to todos.

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
        cursor.execute("PRAGMA table_info(todos)")
        columns = [col[1] for col in cursor.fetchall()]

        if not columns:
            print("✓ todos table does not exist yet")
            print("  No migration needed - table will be created with correct schema.")
            return True

        if "completed_at" in columns:
            print("✓ Migration: todos.completed_at already exists (idempotent)")
        else:
            print("  Adding 'completed_at' column to todos table...")
            cursor.execute("ALTER TABLE todos ADD COLUMN completed_at DATETIME")
            print("✓ Migration: todos.completed_at added")

        print("  Backfilling completed_at from updated_at for completed todos...")
        cursor.execute("""
            UPDATE todos
            SET completed_at = updated_at
            WHERE completed = 1
              AND completed_at IS NULL
        """)
        print(f"✓ Backfilled completed_at for {cursor.rowcount} todos")

        conn.commit()
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
