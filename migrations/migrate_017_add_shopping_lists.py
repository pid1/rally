#!/usr/bin/env python3
"""Migration: Add the shopping list tables.

Creates three new tables:

- shopping_stores        User-defined stores items are grouped under.
- shopping_items         The live list. Completed rows are purged at 30 days.
- shopping_item_history  Permanent, deduplicated autocomplete vocabulary with a
                         use counter. Deliberately outlives the purge.

All three are new tables rather than ALTERs, so idempotency comes free from
CREATE TABLE IF NOT EXISTS.

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
            "SELECT name FROM sqlite_master WHERE type='table' AND name IN "
            "('shopping_stores', 'shopping_items', 'shopping_item_history')"
        )
        existing = {row[0] for row in cursor.fetchall()}
        if len(existing) == 3:
            print("✓ Migration: shopping list tables already exist (idempotent)")
            return True

        print("  Applying migration...")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS shopping_stores (
                id INTEGER PRIMARY KEY,
                name VARCHAR(100) NOT NULL,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS shopping_items (
                id INTEGER PRIMARY KEY,
                name VARCHAR(200) NOT NULL,
                note TEXT,
                store_id INTEGER,
                completed BOOLEAN NOT NULL DEFAULT 0,
                completed_at DATETIME,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS shopping_item_history (
                id INTEGER PRIMARY KEY,
                name_key VARCHAR(200) NOT NULL,
                name VARCHAR(200) NOT NULL,
                store_id INTEGER,
                times_added INTEGER NOT NULL DEFAULT 1,
                last_added_at DATETIME NOT NULL,
                created_at DATETIME NOT NULL
            )
        """)

        # Store names are unique case-insensitively.
        cursor.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS ix_shopping_stores_name_nocase
            ON shopping_stores (name COLLATE NOCASE)
        """)

        # name_key already stores a casefolded value, so a plain unique index is
        # correct here — COLLATE NOCASE would be redundant and misleading.
        cursor.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS ix_shopping_item_history_name_key
            ON shopping_item_history (name_key)
        """)

        conn.commit()
        print("✓ Migration complete: shopping list tables added")
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
