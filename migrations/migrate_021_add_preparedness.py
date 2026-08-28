#!/usr/bin/env python3
"""Migration 021: Add preparedness inventory, locations, and refresh notices.

Purely additive — three new tables and two settings rows. Nothing existing is
altered or dropped.

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
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row[0] for row in cursor.fetchall()}

        # --- prep_locations ---
        if "prep_locations" in tables:
            print("✓ Migration: prep_locations already exists (idempotent)")
        else:
            print("  Creating prep_locations...")
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS prep_locations (
                    id INTEGER PRIMARY KEY,
                    name VARCHAR(100) NOT NULL,
                    sort_order INTEGER NOT NULL DEFAULT 0,
                    created_at DATETIME NOT NULL,
                    updated_at DATETIME NOT NULL
                )
            """)
            print("✓ Created prep_locations")

        # Case-insensitive uniqueness on the name, matching shopping_stores.
        cursor.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS ix_prep_locations_name_nocase
            ON prep_locations(name COLLATE NOCASE)
        """)

        # --- prep_items ---
        if "prep_items" in tables:
            print("✓ Migration: prep_items already exists (idempotent)")
        else:
            print("  Creating prep_items...")
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS prep_items (
                    id INTEGER PRIMARY KEY,
                    name VARCHAR(200) NOT NULL,
                    quantity VARCHAR(50),
                    location_id INTEGER,
                    notes TEXT,
                    refresh_mode VARCHAR(10) NOT NULL DEFAULT 'none',
                    refresh_interval_months INTEGER,
                    next_refresh_date VARCHAR(10),
                    remind_days_before INTEGER,
                    last_refreshed_on VARCHAR(10),
                    created_at DATETIME NOT NULL,
                    updated_at DATETIME NOT NULL,
                    CONSTRAINT ck_prep_item_refresh_mode
                        CHECK (refresh_mode IN ('none','date','interval'))
                )
            """)
            print("✓ Created prep_items")

        # The refresh sweep's only query is a range scan on this column.
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS ix_prep_items_next_refresh_date
            ON prep_items(next_refresh_date)
        """)

        # --- prep_refresh_notices ---
        if "prep_refresh_notices" in tables:
            print("✓ Migration: prep_refresh_notices already exists (idempotent)")
        else:
            print("  Creating prep_refresh_notices...")
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS prep_refresh_notices (
                    id INTEGER PRIMARY KEY,
                    notice_key VARCHAR(80) NOT NULL,
                    item_id INTEGER NOT NULL,
                    refresh_date VARCHAR(10) NOT NULL,
                    sent_on VARCHAR(10) NOT NULL,
                    recipients TEXT,
                    created_at DATETIME NOT NULL
                )
            """)
            print("✓ Created prep_refresh_notices")

        # The unique index IS the announce-once guarantee, not merely an
        # optimization: without it a retry could double-announce.
        cursor.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS ix_prep_refresh_notices_key
            ON prep_refresh_notices(notice_key)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS ix_prep_refresh_notices_item
            ON prep_refresh_notices(item_id)
        """)

        # --- settings defaults ---
        # Seeded rather than left to the code default so the values are visible
        # and editable on the settings page from the first load.
        if "settings" in tables:
            for key, value in (
                ("prep_notify_enabled", "true"),
                ("prep_notify_time", "08:00"),
                ("prep_default_remind_days", "14"),
            ):
                cursor.execute("SELECT 1 FROM settings WHERE key = ?", (key,))
                if cursor.fetchone():
                    print(f"✓ Migration: setting {key} already present (idempotent)")
                else:
                    cursor.execute(
                        "INSERT INTO settings (key, value, updated_at) "
                        "VALUES (?, ?, datetime('now'))",
                        (key, value),
                    )
                    print(f"✓ Seeded setting {key} = {value}")

        conn.commit()
        print("✓ Migration 021 complete")
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
