#!/usr/bin/env python3
"""Migration 034: Add the checklist tables.

Reusable packing lists that are put on a day and checked off there. See issue
#249. Five tables:

* ``checklists`` — the reusable list, with when its copy should be packed.
* ``checklist_groups`` — named subgroups of a list's items (usually people).
* ``checklist_items`` — the items, on the list only; ``group_id IS NULL`` is
  the General catch-all.
* ``checklist_days`` — a list put on a date.
* ``checklist_day_checks`` — which items are checked on which day. A day holds
  no copy of the items, which is what makes an edit to the list reach every day
  and a check on one day reach nothing else.

The unique indexes carry rules the API relies on: one name per checklist
(case-insensitively), one name per group within a checklist, one copy of a
checklist per day, and one check per item per day.

Purely additive and writes no rows.

Safe to run multiple times (idempotent).
"""

import os
import sqlite3
from pathlib import Path

TABLES = {
    "checklists": """
        CREATE TABLE checklists (
            id INTEGER PRIMARY KEY,
            name VARCHAR(100) NOT NULL,
            description TEXT,
            pack_timing VARCHAR(10) NOT NULL DEFAULT 'day_of',
            created_at DATETIME NOT NULL,
            updated_at DATETIME NOT NULL,
            CONSTRAINT ck_checklist_pack_timing CHECK (pack_timing IN ('day_of','day_before'))
        )
    """,
    "checklist_groups": """
        CREATE TABLE checklist_groups (
            id INTEGER PRIMARY KEY,
            checklist_id INTEGER NOT NULL,
            name VARCHAR(100) NOT NULL,
            sort_order INTEGER NOT NULL DEFAULT 0,
            created_at DATETIME NOT NULL,
            updated_at DATETIME NOT NULL
        )
    """,
    "checklist_items": """
        CREATE TABLE checklist_items (
            id INTEGER PRIMARY KEY,
            checklist_id INTEGER NOT NULL,
            group_id INTEGER,
            name VARCHAR(200) NOT NULL,
            note TEXT,
            sort_order INTEGER NOT NULL DEFAULT 0,
            created_at DATETIME NOT NULL,
            updated_at DATETIME NOT NULL
        )
    """,
    "checklist_days": """
        CREATE TABLE checklist_days (
            id INTEGER PRIMARY KEY,
            checklist_id INTEGER NOT NULL,
            date VARCHAR(10) NOT NULL,
            label VARCHAR(200),
            created_at DATETIME NOT NULL,
            updated_at DATETIME NOT NULL
        )
    """,
    "checklist_day_checks": """
        CREATE TABLE checklist_day_checks (
            id INTEGER PRIMARY KEY,
            day_id INTEGER NOT NULL,
            item_id INTEGER NOT NULL,
            checked_at DATETIME NOT NULL
        )
    """,
}

INDEXES = [
    "CREATE UNIQUE INDEX IF NOT EXISTS ix_checklists_name_nocase"
    " ON checklists (name COLLATE NOCASE)",
    "CREATE INDEX IF NOT EXISTS ix_checklist_groups_checklist_id ON checklist_groups (checklist_id)",
    "CREATE UNIQUE INDEX IF NOT EXISTS ix_checklist_groups_checklist_name_nocase"
    " ON checklist_groups (checklist_id, name COLLATE NOCASE)",
    "CREATE INDEX IF NOT EXISTS ix_checklist_items_checklist_id ON checklist_items (checklist_id)",
    "CREATE INDEX IF NOT EXISTS ix_checklist_days_checklist_id ON checklist_days (checklist_id)",
    "CREATE INDEX IF NOT EXISTS ix_checklist_days_date ON checklist_days (date)",
    "CREATE UNIQUE INDEX IF NOT EXISTS ix_checklist_days_checklist_date"
    " ON checklist_days (checklist_id, date)",
    "CREATE INDEX IF NOT EXISTS ix_checklist_day_checks_day_id ON checklist_day_checks (day_id)",
    "CREATE INDEX IF NOT EXISTS ix_checklist_day_checks_item_id ON checklist_day_checks (item_id)",
    "CREATE UNIQUE INDEX IF NOT EXISTS ix_checklist_day_checks_day_item"
    " ON checklist_day_checks (day_id, item_id)",
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
            print("✓ Migration: checklist tables already exist (idempotent)")
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
