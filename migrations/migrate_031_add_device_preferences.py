#!/usr/bin/env python3
"""Migration 031: Add the `devices` and `member_preferences` tables.

Per-family-member behavioral settings, answered once per device — what a screen
*does* when one person opens it, as opposed to `member_notification_prefs`
(migration 027), which decides who hears about what. The first setting is the
calendar's landing view.

`devices` holds one row per browser Rally has heard from. The id is a token the
browser generated and keeps in its own storage, which is why it is TEXT rather
than an autoincrementing integer: the browser has to be able to mint it offline
and keep using the same one, and a server-assigned id cannot do that without a
round trip before the first paint. `label` is the browser's own coarse guess
("iPhone", "Mac") and exists to be corrected — a list of raw tokens answers no
question anybody has.

`member_preferences` is keyed on the **pair**: a person on a device. The unique
index over `(family_member_id, device_id, pref_key)` is what makes the API's
"upsert one answer" safe — one answer per person per device per setting, not a
pile of them.

Purely additive, and it writes **no rows**: an absent row means the setting's
default, which is always `auto` — Rally's own rule, the behavior that predates
this table (a phone-width screen opens the calendar on the day, a wider one on
the month). So upgrading moves nobody's screen. Shipping the feature is not the
same as turning it on.

No foreign keys, matching `member_notification_prefs`, `event_attendees` and
`shopping_items.store_id`: the schema does not use them anywhere. Resolution
always starts from a member row and a device row, so a stray orphan can never
change anybody's screen; deleting a member and forgetting a device each clear
these rows explicitly.

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
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name IN ('devices', 'member_preferences')"
        )
        existing = {row[0] for row in cursor.fetchall()}

        if {"devices", "member_preferences"} <= existing:
            print("✓ Migration: devices and member_preferences already exist (idempotent)")
            return True

        if "devices" not in existing:
            print("  Creating devices...")
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS devices (
                    id VARCHAR(64) PRIMARY KEY,
                    label VARCHAR(80),
                    created_at DATETIME NOT NULL,
                    last_seen_at DATETIME NOT NULL
                )
            """)
            print("✓ Created devices")

        if "member_preferences" not in existing:
            print("  Creating member_preferences...")
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS member_preferences (
                    id INTEGER PRIMARY KEY,
                    family_member_id INTEGER NOT NULL,
                    device_id VARCHAR(64) NOT NULL,
                    pref_key VARCHAR(40) NOT NULL,
                    value VARCHAR(40) NOT NULL,
                    created_at DATETIME NOT NULL,
                    updated_at DATETIME NOT NULL
                )
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS ix_member_preferences_family_member_id
                ON member_preferences(family_member_id)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS ix_member_preferences_device_id
                ON member_preferences(device_id)
            """)
            cursor.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS ix_member_preferences_unique
                ON member_preferences(family_member_id, device_id, pref_key)
            """)
            print("✓ Created member_preferences")

        conn.commit()
        print("✓ Migration 031 complete")
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
