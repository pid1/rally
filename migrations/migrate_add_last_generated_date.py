#!/usr/bin/env python3
"""Migration: Add last_generated_date column to recurring_todos table.

Tracks the recurrence date of the most recently generated instance for each
recurring todo template. Prevents duplicate instance creation on completion
or deletion, and ensures the next instance advances to the correct period.

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
        # Check if recurring_todos table exists (created by migration 004)
        cursor.execute("PRAGMA table_info(recurring_todos)")
        columns = [col[1] for col in cursor.fetchall()]

        if not columns:
            print("✓ recurring_todos table does not exist yet (migration 004 will create it)")
            print("  No migration needed - table will be created with correct schema.")
            return True

        if "last_generated_date" in columns:
            print("✓ Migration: recurring_todos.last_generated_date already exists (idempotent)")
        else:
            print("  Adding 'last_generated_date' column to recurring_todos table...")
            cursor.execute("ALTER TABLE recurring_todos ADD COLUMN last_generated_date VARCHAR(10)")

            # Backfill: set last_generated_date from existing todo instances.
            # For each recurring todo, find the most recent instance's due_date.
            print("  Backfilling last_generated_date from existing instances...")
            cursor.execute("""
                UPDATE recurring_todos
                SET last_generated_date = (
                    SELECT t.due_date
                    FROM todos t
                    WHERE t.recurring_todo_id = recurring_todos.id
                      AND t.due_date IS NOT NULL
                    ORDER BY t.due_date DESC
                    LIMIT 1
                )
                WHERE EXISTS (
                    SELECT 1 FROM todos t
                    WHERE t.recurring_todo_id = recurring_todos.id
                      AND t.due_date IS NOT NULL
                )
            """)

            conn.commit()
            print("✓ Migration complete: recurring_todos.last_generated_date added and backfilled")

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
