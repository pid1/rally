#!/usr/bin/env python3
"""Migration 018: Add followed_teams and sports_event_notices tables.

Creates the two tables backing the sports watchlist: the teams and racing
series the family follows, and the record of which notable upcoming events have
already been announced in a daily summary.

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
        # CHECK: Do both tables already exist?
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name IN ('followed_teams', 'sports_event_notices')"
        )
        existing = {row[0] for row in cursor.fetchall()}
        if {"followed_teams", "sports_event_notices"} <= existing:
            print("✓ Migration 018: sports watchlist tables already exist (idempotent)")
            return True

        # EXECUTE: Create the tables
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS followed_teams (
                id INTEGER NOT NULL PRIMARY KEY,
                provider VARCHAR(20) NOT NULL,
                league VARCHAR(30) NOT NULL,
                team_key VARCHAR(30),
                label VARCHAR(100) NOT NULL,
                radio_station VARCHAR(100),
                active BOOLEAN NOT NULL DEFAULT 1,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS sports_event_notices (
                id INTEGER NOT NULL PRIMARY KEY,
                event_key VARCHAR(80) NOT NULL,
                event_local_date VARCHAR(10) NOT NULL,
                announced_on VARCHAR(10) NOT NULL,
                notability_reason VARCHAR(60),
                created_at DATETIME NOT NULL
            )
        """)
        cursor.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS ix_sports_event_notices_event_key "
            "ON sports_event_notices (event_key)"
        )
        conn.commit()
        print(
            "✓ Migration 018 complete: followed_teams and sports_event_notices created"
        )
        return True

    except sqlite3.Error as e:
        print(f"✗ Migration 018 failed: {e}")
        return False
    finally:
        conn.close()


if __name__ == "__main__":
    success = migrate()
    sys.exit(0 if success else 1)
