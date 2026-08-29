#!/usr/bin/env python3
"""Migration 030: Five-minute calendar syncs, with a rate-limit backoff.

Three changes, all in service of the same complaint: production reported
"External calendars updated 272 hours ago" while the feeds were in fact two
minutes old.

1. **Delete orphaned `calendar_cache` rows.** `calendar_id` is a plain integer,
   not a foreign key, and the delete endpoint never removed the cache row. A
   row whose calendar is gone is refreshed by nothing, so its `fetched_at`
   stays pinned at the moment of the deletion — which is exactly the 272 hours.
   The live feeds were never actually stale; only the report was.

2. **Add `calendar_cache.retry_after`.** Records when a rate-limited feed may
   be tried again, so syncing five times as often cannot turn one 429 into a
   retry storm.

3. **Move `calendar_sync_interval_minutes` from 15 to 5**, and *only* when it
   is still exactly the "15" that migration 024 seeded. No screen exposes this
   key, so a value that is anything else was set by hand and is left alone.

Safe to run multiple times (idempotent).
"""

import os
import sqlite3
from pathlib import Path

OLD_DEFAULT_INTERVAL = "15"
NEW_DEFAULT_INTERVAL = "5"


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
        if not cursor.fetchone():
            print("✓ calendar_cache does not exist yet; migration 024 will create it")
        else:
            cursor.execute("PRAGMA table_info(calendar_cache)")
            columns = [col[1] for col in cursor.fetchall()]

            if "retry_after" in columns:
                print("✓ calendar_cache.retry_after already exists (idempotent)")
            else:
                cursor.execute(
                    "ALTER TABLE calendar_cache ADD COLUMN retry_after DATETIME"
                )
                print("✓ Added calendar_cache.retry_after")

            # Orphans: a cache row whose calendar has been deleted.
            cursor.execute("""
                DELETE FROM calendar_cache
                WHERE calendar_id NOT IN (SELECT id FROM calendars)
                """)
            if cursor.rowcount > 0:
                print(f"✓ Removed {cursor.rowcount} orphaned calendar_cache row(s)")
            else:
                print("✓ No orphaned calendar_cache rows (idempotent)")

        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='settings'"
        )
        if not cursor.fetchone():
            print("✓ settings does not exist yet; nothing to retune")
        else:
            cursor.execute(
                "SELECT value FROM settings WHERE key = 'calendar_sync_interval_minutes'"
            )
            row = cursor.fetchone()
            if row is None:
                cursor.execute(
                    "INSERT INTO settings (key, value) VALUES (?, ?)",
                    ("calendar_sync_interval_minutes", NEW_DEFAULT_INTERVAL),
                )
                print(
                    f"✓ Seeded calendar_sync_interval_minutes = {NEW_DEFAULT_INTERVAL}"
                )
            elif (row[0] or "").strip() == OLD_DEFAULT_INTERVAL:
                cursor.execute(
                    "UPDATE settings SET value = ? WHERE key = 'calendar_sync_interval_minutes'",
                    (NEW_DEFAULT_INTERVAL,),
                )
                print(
                    f"✓ calendar_sync_interval_minutes {OLD_DEFAULT_INTERVAL} "
                    f"→ {NEW_DEFAULT_INTERVAL}"
                )
            else:
                print(
                    f"✓ calendar_sync_interval_minutes is {row[0]!r}, "
                    "set deliberately - leaving it alone"
                )

        conn.commit()
        print("✓ Migration 030 complete")
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
