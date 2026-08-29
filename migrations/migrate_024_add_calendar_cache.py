#!/usr/bin/env python3
"""Migration 024: Add the `calendar_cache` table.

Caches expanded occurrences for external calendars so a page load never waits
on a remote feed. Purely additive; the table starts empty and the first sync
fills it.

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
        if cursor.fetchone():
            print("✓ Migration: calendar_cache already exists (idempotent)")
        else:
            print("  Creating calendar_cache...")
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS calendar_cache (
                    id INTEGER PRIMARY KEY,
                    calendar_id INTEGER NOT NULL,
                    occurrences JSON NOT NULL,
                    window_start VARCHAR(10) NOT NULL,
                    window_end VARCHAR(10) NOT NULL,
                    content_hash VARCHAR(64),
                    etag VARCHAR(200),
                    last_modified VARCHAR(100),
                    fetched_at DATETIME NOT NULL,
                    changed_at DATETIME,
                    last_error TEXT,
                    failure_count INTEGER NOT NULL DEFAULT 0,
                    created_at DATETIME NOT NULL,
                    updated_at DATETIME NOT NULL
                )
            """)
            print("✓ Created calendar_cache")

        # One cache row per calendar; the unique index is what makes the sync's
        # get-or-create safe rather than merely tidy.
        cursor.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS ix_calendar_cache_calendar_id
            ON calendar_cache(calendar_id)
        """)

        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='settings'"
        )
        if cursor.fetchone():
            cursor.execute(
                "SELECT 1 FROM settings WHERE key = 'calendar_sync_interval_minutes'"
            )
            if cursor.fetchone():
                print(
                    "✓ Migration: calendar_sync_interval_minutes already present (idempotent)"
                )
            else:
                cursor.execute(
                    "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, datetime('now'))",
                    ("calendar_sync_interval_minutes", "15"),
                )
                print("✓ Seeded setting calendar_sync_interval_minutes = 15")

        conn.commit()
        print("✓ Migration 024 complete")
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
