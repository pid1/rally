#!/usr/bin/env python3
"""Migration 022: Seed the `home_location` setting.

Purely additive — one settings row, seeded empty. Nothing reads a missing row
differently from an empty one (``home_location()`` returns "" either way), so
this exists to make the field visible and editable on the settings page from
the first load rather than to change behaviour.

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
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='settings'")
        if not cursor.fetchone():
            print("✓ No settings table yet; nothing to seed")
            return True

        cursor.execute("SELECT 1 FROM settings WHERE key = 'home_location'")
        if cursor.fetchone():
            print("✓ Migration: home_location already present (idempotent)")
            return True

        cursor.execute(
            "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, datetime('now'))",
            ("home_location", ""),
        )
        conn.commit()
        print("✓ Seeded setting home_location (empty)")
        print("✓ Migration 022 complete")
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
