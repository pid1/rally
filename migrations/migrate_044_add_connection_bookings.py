#!/usr/bin/env python3
"""Migration 044: Connection bookings — who booked an event, and whether the family confirmed it.

The busy-only MCP tools (`/api/busy/...`) let an untrusted agent book an
appointment without seeing the calendar. Each booking is an ordinary native
event plus five columns on `events`:

- `source`: the MCP connection id that booked it. NULL for every event a
  household member made, which is every event that exists before this runs.
- `pending_confirmation`: true until someone in the family confirms the
  booking. `NOT NULL DEFAULT 0`, so every existing event reads as confirmed.
- `confirmation_hash`: SHA-256 of the confirmation id the agent was given.
  The id itself is never stored, so a database read does not reveal it.
  A unique index keeps two bookings from sharing one; SQLite treats NULLs as
  distinct, so the existing events (all NULL) do not collide.
- `contact_name`, `contact_phone`: who to call back, as the caller gave them.

Purely additive and writes no rows.

Safe to run multiple times (idempotent).
"""

import os
import sqlite3
from pathlib import Path

COLUMNS = (
    ("source", "VARCHAR(200)"),
    ("pending_confirmation", "BOOLEAN NOT NULL DEFAULT 0"),
    ("confirmation_hash", "VARCHAR(64)"),
    ("contact_name", "VARCHAR(100)"),
    ("contact_phone", "VARCHAR(32)"),
)
INDEX = (
    "CREATE UNIQUE INDEX IF NOT EXISTS ix_events_confirmation_hash ON events (confirmation_hash)"
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
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='events'")
        if not cursor.fetchone():
            print("✓ Migration: events table does not exist yet (will be created on first run)")
            return True

        cursor.execute("PRAGMA table_info(events)")
        existing = {col[1] for col in cursor.fetchall()}
        added = []
        for name, ddl in COLUMNS:
            if name not in existing:
                cursor.execute(f"ALTER TABLE events ADD COLUMN {name} {ddl}")
                added.append(name)
        cursor.execute(INDEX)
        conn.commit()
        if added:
            print(f"✓ Migration complete: events.{', events.'.join(added)} added")
        else:
            print("✓ Migration: connection booking columns already exist (idempotent)")
        return True

    except sqlite3.Error as e:
        conn.rollback()
        print(f"✗ Migration failed: {e}")
        return False
    finally:
        conn.close()


if __name__ == "__main__":
    import sys

    success = migrate()
    sys.exit(0 if success else 1)
