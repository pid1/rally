#!/usr/bin/env python3
"""Migration to add settings and calendars tables.

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
        # Step 1: Create settings table if it doesn't exist
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='settings'")
        if cursor.fetchone():
            print("✓ Migration: settings table already exists (idempotent check)")
        else:
            print("  Creating 'settings' table...")
            cursor.execute("""
                CREATE TABLE settings (
                    key VARCHAR(100) PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)
            print("✓ Migration: settings table created")

        # Step 2: Create calendars table if it doesn't exist
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='calendars'")
        if cursor.fetchone():
            print("✓ Migration: calendars table already exists (idempotent check)")
        else:
            print("  Creating 'calendars' table...")
            cursor.execute("""
                CREATE TABLE calendars (
                    id INTEGER PRIMARY KEY,
                    label VARCHAR(100) NOT NULL,
                    url TEXT NOT NULL,
                    family_member_id INTEGER NOT NULL,
                    owner_email VARCHAR(200),
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)
            print("✓ Migration: calendars table created")

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
