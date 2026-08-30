#!/usr/bin/env python3
"""Migration 026: Add `shopping_items.sort_order`.

Backs hand-arranged ordering on the shopping list. The column is per-store: it
positions an item among the other items at its own store, which is why the
backfill numbers each store group separately.

The backfill preserves what is currently on screen. Before this column the list
read `completed ASC, created_at DESC`, so numbering each store's rows in that
same order means nobody's list visibly moves the first time they load the page
after upgrading — the ordering becomes editable rather than becoming different.

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
            "SELECT name FROM sqlite_master WHERE type='table' AND name='shopping_items'"
        )
        if not cursor.fetchone():
            print(
                "✓ shopping_items does not exist yet; migration 017 will create it with the column"
            )
            return True

        cursor.execute("PRAGMA table_info(shopping_items)")
        columns = [col[1] for col in cursor.fetchall()]

        if "sort_order" in columns:
            print("✓ Migration: shopping_items.sort_order already exists (idempotent)")
            return True

        print("  Applying migration...")
        cursor.execute(
            "ALTER TABLE shopping_items ADD COLUMN sort_order INTEGER NOT NULL DEFAULT 0"
        )

        # Number each store group in the order the list already reads. `id ASC`
        # is the final tiebreak rather than `id DESC`: rows batched in by a seed
        # or an import share a created_at to the second, and for those ties the
        # old list showed whatever SQLite emitted — rowid order, preserved
        # through a stable sort in the browser. Ranking them the same way is
        # what makes "nothing moves" true instead of merely nearly true.
        cursor.execute("""
            SELECT id, store_id
            FROM shopping_items
            ORDER BY
                store_id IS NULL, store_id,
                completed ASC, created_at DESC, id ASC
            """)
        rows = cursor.fetchall()

        position = 0
        previous_store = object()  # A sentinel: NULL is a real store_id here.
        updates = []
        for item_id, store_id in rows:
            if store_id != previous_store:
                previous_store = store_id
                position = 0
            updates.append((position, item_id))
            position += 1

        cursor.executemany("UPDATE shopping_items SET sort_order = ? WHERE id = ?", updates)
        conn.commit()
        print(f"✓ Migration complete: shopping_items.sort_order added ({len(updates)} rows ranked)")
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
