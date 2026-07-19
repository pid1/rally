#!/usr/bin/env python3
"""Migration 016: Add stem_concept_history table.

Creates the stem_concept_history table, which records STEM "concept of the day"
topics that have already been used so the generator can avoid repeating them.

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
        # CHECK: Does the table already exist?
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='stem_concept_history'"
        )
        if cursor.fetchone():
            print("✓ Migration 016: stem_concept_history already exists (idempotent)")
            return True

        # EXECUTE: Create the table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS stem_concept_history (
                id INTEGER NOT NULL PRIMARY KEY,
                title VARCHAR(200) NOT NULL,
                field VARCHAR(50),
                used_on VARCHAR(10) NOT NULL,
                created_at DATETIME NOT NULL
            )
        """)
        conn.commit()
        print("✓ Migration 016 complete: stem_concept_history created")
        return True

    except sqlite3.Error as e:
        print(f"✗ Migration 016 failed: {e}")
        return False
    finally:
        conn.close()


if __name__ == "__main__":
    success = migrate()
    sys.exit(0 if success else 1)
