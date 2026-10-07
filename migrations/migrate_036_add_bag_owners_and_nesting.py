#!/usr/bin/env python3
"""Migration 036: Give packing list bags an owner and a bag they go in (#262).

Adds `packing_list_bags.owner_id` (a family member; NULL is Everyone) and
`packing_list_bags.parent_bag_id` (the bag it goes in; NULL is none): the
household's defaults, set in Manage Bags.

Adds three tables:

* `packing_list_template_bags` — a template's reading of a bag, both fields
  copied whole. Unique on `(packing_list_template_id, bag_id)`.
* `packing_list_day_bags` — one day's reading of a bag, which wins over the
  template's. Unique on `(day_id, bag_id)`.
* `packing_list_day_bag_checks` — a bag grabbed on a day; the row's existence
  is the check, as in `packing_list_day_checks`. Unique on `(day_id, bag_id)`.

Replaces the bag name index: a bag is now unique by its name (ignoring case)
**and** its owner, so two people can each have a "Backpack". The old
`ix_packing_list_bags_name_nocase` is dropped and
`ix_packing_list_bags_name_owner_nocase` is created over
`(name COLLATE NOCASE, IFNULL(owner_id, 0))` — `IFNULL` because SQLite treats
NULLs as distinct, and no owner has to count as one owner. Every existing bag
has no owner and a name unique on its own, so the new index cannot clash.

It writes **no rows**: every existing bag has no owner and goes in nothing. No
item moves. What does show after upgrading is that each bag
already in use appears as a bag to grab at the top of the Everyone group, since
a bag with no owner is Everyone's.

No foreign keys, matching the other packing list tables: SQLite does not
enforce them here, and the API clears or deletes these by hand when a member,
a bag, a template or a day goes.

Safe to run multiple times (idempotent).
"""

import os
import sqlite3
from pathlib import Path

NEW_TABLES = {
    "packing_list_template_bags": [
        """
        CREATE TABLE IF NOT EXISTS packing_list_template_bags (
            id INTEGER PRIMARY KEY,
            packing_list_template_id INTEGER NOT NULL,
            bag_id INTEGER NOT NULL,
            owner_id INTEGER,
            parent_bag_id INTEGER,
            created_at DATETIME NOT NULL,
            updated_at DATETIME NOT NULL
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS ix_packing_list_template_bags_packing_list_template_id
        ON packing_list_template_bags (packing_list_template_id)
        """,
        """
        CREATE INDEX IF NOT EXISTS ix_packing_list_template_bags_bag_id
        ON packing_list_template_bags (bag_id)
        """,
        """
        CREATE UNIQUE INDEX IF NOT EXISTS ix_packing_list_template_bags_template_bag
        ON packing_list_template_bags (packing_list_template_id, bag_id)
        """,
    ],
    "packing_list_day_bags": [
        """
        CREATE TABLE IF NOT EXISTS packing_list_day_bags (
            id INTEGER PRIMARY KEY,
            day_id INTEGER NOT NULL,
            bag_id INTEGER NOT NULL,
            owner_id INTEGER,
            parent_bag_id INTEGER,
            created_at DATETIME NOT NULL,
            updated_at DATETIME NOT NULL
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS ix_packing_list_day_bags_day_id
        ON packing_list_day_bags (day_id)
        """,
        """
        CREATE INDEX IF NOT EXISTS ix_packing_list_day_bags_bag_id
        ON packing_list_day_bags (bag_id)
        """,
        """
        CREATE UNIQUE INDEX IF NOT EXISTS ix_packing_list_day_bags_day_bag
        ON packing_list_day_bags (day_id, bag_id)
        """,
    ],
    "packing_list_day_bag_checks": [
        """
        CREATE TABLE IF NOT EXISTS packing_list_day_bag_checks (
            id INTEGER PRIMARY KEY,
            day_id INTEGER NOT NULL,
            bag_id INTEGER NOT NULL,
            checked_at DATETIME NOT NULL
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS ix_packing_list_day_bag_checks_day_id
        ON packing_list_day_bag_checks (day_id)
        """,
        """
        CREATE INDEX IF NOT EXISTS ix_packing_list_day_bag_checks_bag_id
        ON packing_list_day_bag_checks (bag_id)
        """,
        """
        CREATE UNIQUE INDEX IF NOT EXISTS ix_packing_list_day_bag_checks_day_bag
        ON packing_list_day_bag_checks (day_id, bag_id)
        """,
    ],
}

NEW_COLUMNS = ("owner_id", "parent_bag_id")

# A bag was unique by name; it is now unique by name and owner.
OLD_NAME_INDEX = "ix_packing_list_bags_name_nocase"
NAME_OWNER_INDEX = "ix_packing_list_bags_name_owner_nocase"
NAME_OWNER_INDEX_DDL = (
    f"CREATE UNIQUE INDEX IF NOT EXISTS {NAME_OWNER_INDEX}"
    " ON packing_list_bags (name COLLATE NOCASE, IFNULL(owner_id, 0))"
)


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
            "SELECT name FROM sqlite_master WHERE type='table' AND name='packing_list_bags'"
        )
        if cursor.fetchone() is None:
            print("✓ Migration: packing_list_bags does not exist yet; nothing to do")
            return True

        cursor.execute("PRAGMA table_info(packing_list_bags)")
        columns = {col[1] for col in cursor.fetchall()}
        missing_columns = [c for c in NEW_COLUMNS if c not in columns]

        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name IN ({})".format(
                ", ".join("?" for _ in NEW_TABLES)
            ),
            tuple(NEW_TABLES),
        )
        existing_tables = {row[0] for row in cursor.fetchall()}

        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name IN (?, ?)",
            (OLD_NAME_INDEX, NAME_OWNER_INDEX),
        )
        indexes = {row[0] for row in cursor.fetchall()}
        index_done = NAME_OWNER_INDEX in indexes and OLD_NAME_INDEX not in indexes

        if not missing_columns and set(NEW_TABLES) <= existing_tables and index_done:
            print("✓ Migration: bag owners and nesting already applied (idempotent)")
            return True

        for column in missing_columns:
            print(f"  Adding packing_list_bags.{column}...")
            cursor.execute(f"ALTER TABLE packing_list_bags ADD COLUMN {column} INTEGER")

        # After the column, since the new index covers it.
        if not index_done:
            print("  Making bag names unique per owner...")
            cursor.execute(f"DROP INDEX IF EXISTS {OLD_NAME_INDEX}")
            cursor.execute(NAME_OWNER_INDEX_DDL)

        for table, statements in NEW_TABLES.items():
            if table in existing_tables:
                continue
            print(f"  Creating {table}...")
            for statement in statements:
                cursor.execute(statement)

        conn.commit()
        print("✓ Migration 036 complete")
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
