#!/usr/bin/env python3
"""Migration to add attendee_ids and cook_id to dinner_plans, and drop UNIQUE on date.

Run this once to upgrade existing databases. Safe to run multiple times (idempotent).

SQLite does not support DROP CONSTRAINT, so removing the UNIQUE constraint on
`date` requires recreating the table.
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
        return True

    print(f"Checking database at {db_path}...")

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    try:
        # Check if dinner_plans table exists at all
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='dinner_plans'")
        if not cursor.fetchone():
            print(
                "✓ Migration: dinner_plans table does not exist yet (will be created on first run)"
            )
            return True

        # Step 1: Add attendee_ids column if missing
        cursor.execute("PRAGMA table_info(dinner_plans)")
        columns = [col[1] for col in cursor.fetchall()]

        if "attendee_ids" not in columns:
            print("  Adding 'attendee_ids' column to dinner_plans table...")
            cursor.execute("ALTER TABLE dinner_plans ADD COLUMN attendee_ids TEXT")
            print("✓ Migration: dinner_plans.attendee_ids column added")
        else:
            print("✓ Migration: dinner_plans.attendee_ids column already exists (idempotent check)")

        if "cook_id" not in columns:
            print("  Adding 'cook_id' column to dinner_plans table...")
            cursor.execute("ALTER TABLE dinner_plans ADD COLUMN cook_id INTEGER")
            print("✓ Migration: dinner_plans.cook_id column added")
        else:
            print("✓ Migration: dinner_plans.cook_id column already exists (idempotent check)")

        # Step 2: Drop UNIQUE constraint on date by recreating the table
        # Check if the UNIQUE constraint still exists by inspecting the CREATE TABLE SQL
        cursor.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='dinner_plans'")
        create_sql = cursor.fetchone()[0]

        if "UNIQUE" in create_sql.upper():
            print("  Removing UNIQUE constraint on dinner_plans.date (recreating table)...")

            # Refresh column info after potential additions above
            cursor.execute("PRAGMA table_info(dinner_plans)")
            current_columns = [col[1] for col in cursor.fetchall()]
            col_list = ", ".join(current_columns)

            cursor.execute("""
                CREATE TABLE dinner_plans_new (
                    id INTEGER PRIMARY KEY,
                    date VARCHAR(10) NOT NULL,
                    plan TEXT NOT NULL,
                    attendee_ids TEXT,
                    cook_id INTEGER,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)

            cursor.execute(f"""
                INSERT INTO dinner_plans_new ({col_list})
                SELECT {col_list} FROM dinner_plans
            """)

            cursor.execute("DROP TABLE dinner_plans")
            cursor.execute("ALTER TABLE dinner_plans_new RENAME TO dinner_plans")
            print("✓ Migration: UNIQUE constraint on dinner_plans.date removed")
        else:
            print(
                "✓ Migration: dinner_plans.date UNIQUE constraint already removed (idempotent check)"
            )

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
