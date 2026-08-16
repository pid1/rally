#!/usr/bin/env python3
"""Migration 025: Add `calendar_cache.sync_tokens`.

Stores one RFC 6578 sync token per server-side CalDAV calendar, so a sync can
ask what changed instead of downloading everything. Purely additive; a NULL
means "no baseline yet" and the next sync captures one.

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
            "SELECT name FROM sqlite_master WHERE type='table' AND name='calendar_cache'"
        )
        if not cursor.fetchone():
            print(
                "✓ calendar_cache does not exist yet; migration 024 will create it with the column"
            )
            return True

        cursor.execute("PRAGMA table_info(calendar_cache)")
        columns = [col[1] for col in cursor.fetchall()]

        if "sync_tokens" in columns:
            print("✓ Migration: calendar_cache.sync_tokens already exists (idempotent)")
            return True

        print("  Applying migration...")
        cursor.execute("ALTER TABLE calendar_cache ADD COLUMN sync_tokens JSON")
        conn.commit()
        print("✓ Migration complete: calendar_cache.sync_tokens added")
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
