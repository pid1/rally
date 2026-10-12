#!/usr/bin/env python3
"""Migration 040: Add `dinner_plans.notes`.

Optional detail on a meal — who wants what, substitutions, anything beyond
the meal's name — kept separate from `plan`, which is now the meal's name
alone (`Meal Name` in the UI). An existing meal's `plan` text is left exactly
as it is; nothing is split or moved into `notes`.

Purely additive and writes no rows: every existing meal gets `notes = NULL`,
which reads as "no notes" everywhere the field is shown.

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
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='dinner_plans'")
        if not cursor.fetchone():
            print(
                "✓ Migration: dinner_plans table does not exist yet (will be created on first run)"
            )
            return True

        cursor.execute("PRAGMA table_info(dinner_plans)")
        columns = [col[1] for col in cursor.fetchall()]

        if "notes" in columns:
            print("✓ Migration: dinner_plans.notes already exists (idempotent)")
            return True

        print("  Applying migration...")
        cursor.execute("ALTER TABLE dinner_plans ADD COLUMN notes TEXT")
        conn.commit()
        print("✓ Migration complete: dinner_plans.notes added")
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
