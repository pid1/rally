#!/usr/bin/env python3
"""Migration to add meal_type column to dinner_plans table.

Existing records are migrated to type 'Dinner'.
Run this once to upgrade existing databases. Safe to run multiple times (idempotent).
"""

import os
import sqlite3
import sys
from pathlib import Path


def migrate():
    db_path = os.environ.get("RALLY_DB_PATH")

    if not db_path:
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
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='dinner_plans'")
        if not cursor.fetchone():
            print("✓ Migration: dinner_plans table does not exist yet (will be created on first run)")
            return True

        cursor.execute("PRAGMA table_info(dinner_plans)")
        columns = [col[1] for col in cursor.fetchall()]

        if "meal_type" not in columns:
            print("  Adding 'meal_type' column to dinner_plans table...")
            cursor.execute(
                "ALTER TABLE dinner_plans ADD COLUMN meal_type VARCHAR(20) NOT NULL DEFAULT 'Dinner'"
            )
            print("✓ Migration: dinner_plans.meal_type column added (existing records set to 'Dinner')")
        else:
            print("✓ Migration: dinner_plans.meal_type column already exists (idempotent check)")

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
