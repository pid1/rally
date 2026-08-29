#!/usr/bin/env python3
"""Migration 023: Add the `prep_reviews` table.

Stores LLM reviews of the preparedness inventory. Purely additive.

Reviews are snapshotted rather than recomputed on view, following
`dashboard_snapshots`: the call costs real money and several seconds, so a page
load must never trigger one.

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
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='prep_reviews'"
        )
        if cursor.fetchone():
            print("✓ Migration: prep_reviews already exists (idempotent)")
            return True

        print("  Creating prep_reviews...")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS prep_reviews (
                id INTEGER PRIMARY KEY,
                data JSON NOT NULL,
                model VARCHAR(100),
                item_count INTEGER NOT NULL DEFAULT 0,
                created_at DATETIME NOT NULL
            )
        """)
        conn.commit()
        print("✓ Created prep_reviews")
        print("✓ Migration 023 complete")
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
