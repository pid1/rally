#!/usr/bin/env python3
"""Migration to add recurring_todos table and recurring_todo_id column to todos.

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
        dev_path = Path(__file__).parent / "rally.db"
        db_path = str(prod_path) if prod_path.exists() else str(dev_path)

    db_path = Path(db_path)

    if not db_path.exists():
        print(f"✓ Database not found at {db_path}")
        print("  No migration needed - database will be created with correct schema on first run.")
        return True

    print(f"Checking database at {db_path}...")

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    try:
        # Step 1: Create recurring_todos table if it doesn't exist
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='recurring_todos'"
        )
        if cursor.fetchone():
            print("✓ Migration: recurring_todos table already exists (idempotent check)")
        else:
            print("  Creating 'recurring_todos' table...")
            cursor.execute("""
                CREATE TABLE recurring_todos (
                    id INTEGER PRIMARY KEY,
                    title VARCHAR(200) NOT NULL,
                    description TEXT,
                    recurrence_type VARCHAR(20) NOT NULL,
                    recurrence_day INTEGER,
                    assigned_to INTEGER,
                    has_due_date BOOLEAN DEFAULT 0 NOT NULL,
                    active BOOLEAN DEFAULT 1 NOT NULL,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)
            print("✓ Migration: recurring_todos table created")

        # Step 2: Add recurring_todo_id column to todos if it doesn't exist
        cursor.execute("PRAGMA table_info(todos)")
        columns = [col[1] for col in cursor.fetchall()]

        if "recurring_todo_id" in columns:
            print("✓ Migration: todos.recurring_todo_id column already exists (idempotent check)")
        else:
            print("  Adding 'recurring_todo_id' column to todos table...")
            cursor.execute("ALTER TABLE todos ADD COLUMN recurring_todo_id INTEGER")
            print("✓ Migration: todos.recurring_todo_id column added")

        conn.commit()
        return True

    except sqlite3.Error as e:
        print(f"✗ Migration failed: {e}")
        return False
    finally:
        conn.close()


if __name__ == "__main__":
    success = migrate()
    sys.exit(0 if success else 1)
