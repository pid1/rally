#!/usr/bin/env python3
"""Migration 037: Add `packing_list_day_bags.removed_from_bag_id`.

Why a bag goes in nothing on one day. Taking a bag off a day un-nests the bags
that went in it, through a day reading whose parent is NULL — the same row
Change bags writes when somebody picks "goes in nothing" by hand. This column
names the bag a removal took the bag out of, so resyncing a day's items with
its template can put back exactly what a removal undid and nothing a person
chose. Any later write of the reading clears it.

Purely additive and no rows are written: every existing reading gets NULL. A
bag removed before this ran has no record, so a resync leaves its inner bags
where they are rather than guessing which readings a removal wrote.

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
            "SELECT name FROM sqlite_master WHERE type='table' AND name='packing_list_day_bags'"
        )
        if not cursor.fetchone():
            print("✓ packing_list_day_bags does not exist yet; nothing to do")
            return True

        cursor.execute("PRAGMA table_info(packing_list_day_bags)")
        columns = [col[1] for col in cursor.fetchall()]

        if "removed_from_bag_id" in columns:
            print(
                "✓ Migration: packing_list_day_bags.removed_from_bag_id already exists (idempotent)"
            )
            return True

        print("  Applying migration...")
        cursor.execute("ALTER TABLE packing_list_day_bags ADD COLUMN removed_from_bag_id INTEGER")
        conn.commit()
        print("✓ Migration complete: packing_list_day_bags.removed_from_bag_id added")
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
