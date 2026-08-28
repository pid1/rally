#!/usr/bin/env python3
"""Migration 027: Add the `member_notification_prefs` table.

Per-family-member control over each kind of notification Rally sends. Purely
additive, and it writes **no rows**: an absent row means the kind's default, so
upgrading changes nobody's behavior. Shipping the feature is not the same as
turning it on.

The unique index on `(family_member_id, kind)` is what makes the API's
"upsert one preference" safe — one answer per person per kind, not a pile of
them.

No foreign key on `family_member_id`, matching `event_attendees` and
`shopping_items.store_id`: the schema does not use them anywhere. Resolution
always starts from a member row, so a stray orphan can never grant anybody a
notification, and `DELETE /api/family/{id}` clears these rows explicitly.

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
            "SELECT name FROM sqlite_master WHERE type='table' AND name='member_notification_prefs'"
        )
        if cursor.fetchone():
            print("✓ Migration: member_notification_prefs already exists (idempotent)")
            return True

        print("  Creating member_notification_prefs...")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS member_notification_prefs (
                id INTEGER PRIMARY KEY,
                family_member_id INTEGER NOT NULL,
                kind VARCHAR(40) NOT NULL,
                enabled BOOLEAN NOT NULL DEFAULT 1,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL
            )
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS ix_member_notification_prefs_family_member_id
            ON member_notification_prefs(family_member_id)
        """)
        cursor.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS ix_member_notification_prefs_unique
            ON member_notification_prefs(family_member_id, kind)
        """)
        conn.commit()
        print("✓ Created member_notification_prefs")
        print("✓ Migration 027 complete")
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
