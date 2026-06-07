#!/usr/bin/env python3
"""Migration 011: Add rating and review columns to dinner_plans.

Safe to run multiple times (idempotent).
"""

import os
import sqlite3
import sys
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
        print(f"  Database not found at {db_path}")
        print("  No migration needed - database will be created with correct schema.")
        return True

    print(f"Checking database at {db_path}...")

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    try:
        # CHECK: Do the columns already exist?
        cursor.execute("PRAGMA table_info(dinner_plans)")
        columns = [col[1] for col in cursor.fetchall()]

        has_rating = "rating" in columns
        has_review = "review" in columns

        if has_rating and has_review:
            print(
                "  Migration 011: dinner_plans.rating and dinner_plans.review already exist (idempotent)"
            )
            return True

        # EXECUTE: Add missing columns
        if not has_rating:
            print("  Adding dinner_plans.rating column...")
            cursor.execute("ALTER TABLE dinner_plans ADD COLUMN rating INTEGER")
            print("  dinner_plans.rating added")

        if not has_review:
            print("  Adding dinner_plans.review column...")
            cursor.execute("ALTER TABLE dinner_plans ADD COLUMN review TEXT")
            print("  dinner_plans.review added")

        conn.commit()
        print("  Migration 011 complete: meal review columns added")
        return True

    except sqlite3.Error as e:
        print(f"  Migration 011 failed: {e}")
        return False
    finally:
        conn.close()


if __name__ == "__main__":
    success = migrate()
    sys.exit(0 if success else 1)
