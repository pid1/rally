#!/usr/bin/env python3
"""Migration: Add CalDAV support columns to calendars table.

Adds cal_type (ics/caldav_google/caldav_apple), username, and password columns
to support Google and Apple CalDAV via app-specific passwords.

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
        cursor.execute("PRAGMA table_info(calendars)")
        columns = [col[1] for col in cursor.fetchall()]

        if not columns:
            print("✓ calendars table does not exist yet")
            print("  No migration needed - table will be created with correct schema.")
            return True

        added = []

        if "cal_type" not in columns:
            cursor.execute("ALTER TABLE calendars ADD COLUMN cal_type VARCHAR(20) DEFAULT 'ics'")
            added.append("cal_type")

        if "username" not in columns:
            cursor.execute("ALTER TABLE calendars ADD COLUMN username VARCHAR(200)")
            added.append("username")

        if "password" not in columns:
            cursor.execute("ALTER TABLE calendars ADD COLUMN password TEXT")
            added.append("password")

        if added:
            conn.commit()
            print(f"✓ Migration complete: calendars columns added: {', '.join(added)}")
        else:
            print("✓ Migration: calendars CalDAV columns already exist (idempotent)")

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
