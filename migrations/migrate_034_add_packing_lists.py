#!/usr/bin/env python3
"""Migration 034: Add the packing list tables.

Reusable packing lists, put on days and checked off there. See issue #249.

* ``packing_lists`` — the packing list template: its name, description, and how many
  days ahead it is packed.
* ``packing_list_bags`` — the household's bags (Pool bag, Car), shared by every
  packing list. An item in no bag has ``bag_id IS NULL``.
* ``packing_list_items`` — the template's items, each with an optional owner (a
  family member) and an optional bag, the two ways the page groups a list.
* ``packing_list_item_history`` — every item name ever used, for autocomplete.
* ``packing_list_schedules`` — a template put on days by a repeating rule, at most
  one per template. Its recurrence columns are named as on
  ``recurring_todos`` because ``rally.recurrence`` reads them by name.
* ``packing_list_days`` — a template put on a date, by hand or by a schedule, with
  its own label and lead time when it has them.
* ``packing_list_day_items`` — a day's own changes: items added to that day, and
  that day's edits or removals of a template item.
* ``packing_list_day_checks`` — which template items are checked on which day.

The unique indexes carry rules the API relies on: one template per name and
one bag per name (case-insensitively), one schedule per template, one copy of
a template per day, one check per item per day, one reading of a template item
per day, and one history row per item name.

Purely additive and writes no rows.

Safe to run multiple times (idempotent).
"""

import os
import sqlite3
from pathlib import Path

TABLES = {
    "packing_lists": """
        CREATE TABLE packing_lists (
            id INTEGER PRIMARY KEY,
            name VARCHAR(100) NOT NULL,
            description TEXT,
            pack_days_before INTEGER NOT NULL DEFAULT 0,
            created_at DATETIME NOT NULL,
            updated_at DATETIME NOT NULL,
            CONSTRAINT ck_packing_list_pack_days_before CHECK (pack_days_before >= 0)
        )
    """,
    "packing_list_bags": """
        CREATE TABLE packing_list_bags (
            id INTEGER PRIMARY KEY,
            name VARCHAR(100) NOT NULL,
            created_at DATETIME NOT NULL,
            updated_at DATETIME NOT NULL
        )
    """,
    "packing_list_items": """
        CREATE TABLE packing_list_items (
            id INTEGER PRIMARY KEY,
            packing_list_id INTEGER NOT NULL,
            owner_id INTEGER,
            bag_id INTEGER,
            name VARCHAR(200) NOT NULL,
            note TEXT,
            sort_order INTEGER NOT NULL DEFAULT 0,
            created_at DATETIME NOT NULL,
            updated_at DATETIME NOT NULL
        )
    """,
    "packing_list_item_history": """
        CREATE TABLE packing_list_item_history (
            id INTEGER PRIMARY KEY,
            name VARCHAR(200) NOT NULL,
            name_key VARCHAR(200) NOT NULL,
            owner_id INTEGER,
            bag_id INTEGER,
            times_added INTEGER NOT NULL DEFAULT 1,
            last_added_at DATETIME NOT NULL
        )
    """,
    "packing_list_schedules": """
        CREATE TABLE packing_list_schedules (
            id INTEGER PRIMARY KEY,
            packing_list_id INTEGER NOT NULL,
            recurrence_type VARCHAR(20) NOT NULL,
            recurrence_day INTEGER,
            custom_rule JSON,
            start_date VARCHAR(10),
            end_date VARCHAR(10),
            label VARCHAR(200),
            last_generated_date VARCHAR(10),
            active BOOLEAN NOT NULL DEFAULT 1,
            created_at DATETIME NOT NULL,
            updated_at DATETIME NOT NULL
        )
    """,
    "packing_list_days": """
        CREATE TABLE packing_list_days (
            id INTEGER PRIMARY KEY,
            packing_list_id INTEGER NOT NULL,
            date VARCHAR(10) NOT NULL,
            label VARCHAR(200),
            schedule_id INTEGER,
            pack_days_before INTEGER,
            label_edited BOOLEAN NOT NULL DEFAULT 0,
            created_at DATETIME NOT NULL,
            updated_at DATETIME NOT NULL
        )
    """,
    "packing_list_day_items": """
        CREATE TABLE packing_list_day_items (
            id INTEGER PRIMARY KEY,
            day_id INTEGER NOT NULL,
            item_id INTEGER,
            owner_id INTEGER,
            bag_id INTEGER,
            name VARCHAR(200) NOT NULL,
            note TEXT,
            sort_order INTEGER NOT NULL DEFAULT 0,
            removed BOOLEAN NOT NULL DEFAULT 0,
            checked BOOLEAN NOT NULL DEFAULT 0,
            created_at DATETIME NOT NULL,
            updated_at DATETIME NOT NULL
        )
    """,
    "packing_list_day_checks": """
        CREATE TABLE packing_list_day_checks (
            id INTEGER PRIMARY KEY,
            day_id INTEGER NOT NULL,
            item_id INTEGER NOT NULL,
            checked_at DATETIME NOT NULL
        )
    """,
}

INDEXES = [
    "CREATE UNIQUE INDEX IF NOT EXISTS ix_packing_lists_name_nocase"
    " ON packing_lists (name COLLATE NOCASE)",
    "CREATE UNIQUE INDEX IF NOT EXISTS ix_packing_list_bags_name_nocase"
    " ON packing_list_bags (name COLLATE NOCASE)",
    "CREATE INDEX IF NOT EXISTS ix_packing_list_items_packing_list_id ON packing_list_items (packing_list_id)",
    "CREATE UNIQUE INDEX IF NOT EXISTS ix_packing_list_item_history_name_key"
    " ON packing_list_item_history (name_key)",
    "CREATE UNIQUE INDEX IF NOT EXISTS ix_packing_list_schedules_packing_list_id"
    " ON packing_list_schedules (packing_list_id)",
    "CREATE INDEX IF NOT EXISTS ix_packing_list_days_packing_list_id ON packing_list_days (packing_list_id)",
    "CREATE INDEX IF NOT EXISTS ix_packing_list_days_date ON packing_list_days (date)",
    "CREATE INDEX IF NOT EXISTS ix_packing_list_days_schedule_id ON packing_list_days (schedule_id)",
    "CREATE UNIQUE INDEX IF NOT EXISTS ix_packing_list_days_packing_list_date"
    " ON packing_list_days (packing_list_id, date)",
    "CREATE INDEX IF NOT EXISTS ix_packing_list_day_items_day_id ON packing_list_day_items (day_id)",
    "CREATE INDEX IF NOT EXISTS ix_packing_list_day_items_item_id ON packing_list_day_items (item_id)",
    "CREATE UNIQUE INDEX IF NOT EXISTS ix_packing_list_day_items_day_item"
    " ON packing_list_day_items (day_id, item_id) WHERE item_id IS NOT NULL",
    "CREATE INDEX IF NOT EXISTS ix_packing_list_day_checks_day_id ON packing_list_day_checks (day_id)",
    "CREATE INDEX IF NOT EXISTS ix_packing_list_day_checks_item_id ON packing_list_day_checks (item_id)",
    "CREATE UNIQUE INDEX IF NOT EXISTS ix_packing_list_day_checks_day_item"
    " ON packing_list_day_checks (day_id, item_id)",
]


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
        existing = {row[0] for row in cursor.fetchall()}

        created = []
        for name, ddl in TABLES.items():
            if name not in existing:
                cursor.execute(ddl)
                created.append(name)
        # Indexes are checked one by one rather than assumed from the tables:
        # a run interrupted between the two would otherwise never add them.
        for ddl in INDEXES:
            cursor.execute(ddl)
        conn.commit()

        if created:
            print(f"✓ Migration complete: created {', '.join(created)}")
        else:
            print("✓ Migration: packing list tables already exist (idempotent)")
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
