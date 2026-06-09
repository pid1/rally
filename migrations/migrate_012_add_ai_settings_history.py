#!/usr/bin/env python3
"""Migration 012: Add ai_settings_history table for AI settings snapshotting.

Creates the ai_settings_history table (versioned snapshots of agent_voice and
family_context), seeds it from any existing agent_voice / family_context rows
in the key-value settings table, points the new
current_agent_voice_history_id / current_family_context_history_id settings
keys at the seed rows, and removes the original agent_voice / family_context
settings rows (their values live in ai_settings_history from now on).

Safe to run multiple times (idempotent).
"""

import os
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

FIELDS = ("agent_voice", "family_context")


def migrate():
    """Run the migration. Return True on success, False on failure."""
    db_path = os.environ.get("RALLY_DB_PATH")

    if not db_path:
        prod_path = Path("/data/rally.db")
        dev_path = Path(__file__).parent.parent / "rally.db"
        db_path = str(prod_path) if prod_path.exists() else str(dev_path)

    db_path = Path(db_path)

    if not db_path.exists():
        print(f"  Database not found at {db_path}")
        print("  No migration needed - database will be created with correct schema.")
        return True

    print(f"Checking database at {db_path}...")

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    try:
        # CREATE: history table and field_name index (idempotent)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS ai_settings_history (
                id INTEGER NOT NULL PRIMARY KEY,
                field_name VARCHAR(50) NOT NULL,
                value TEXT NOT NULL,
                created_at DATETIME NOT NULL,
                last_used_at DATETIME NOT NULL
            )
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS ix_ai_settings_history_field_name
            ON ai_settings_history(field_name)
        """)

        # Settings table may not exist yet on a fresh database
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='settings'")
        if not cursor.fetchone():
            conn.commit()
            print("  Migration 012: ai_settings_history created; no settings table to seed from")
            return True

        # Match SQLAlchemy's SQLite datetime format (naive UTC)
        now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")

        for field in FIELDS:
            pointer_key = f"current_{field}_history_id"

            # CHECK: Is this field already migrated?
            cursor.execute("SELECT value FROM settings WHERE key = ?", (pointer_key,))
            if cursor.fetchone():
                print(f"  Migration 012: {pointer_key} already set (idempotent)")
                continue

            cursor.execute("SELECT value FROM settings WHERE key = ?", (field,))
            row = cursor.fetchone()
            if row is None:
                print(f"  Migration 012: no existing {field} setting to migrate")
                continue

            # EXECUTE: Seed history row, point the setting at it, drop the original row
            cursor.execute(
                """
                INSERT INTO ai_settings_history (field_name, value, created_at, last_used_at)
                VALUES (?, ?, ?, ?)
                """,
                (field, row[0], now, now),
            )
            history_id = cursor.lastrowid
            cursor.execute(
                "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?)",
                (pointer_key, str(history_id), now),
            )
            cursor.execute("DELETE FROM settings WHERE key = ?", (field,))
            print(f"  Migration 012: migrated {field} into ai_settings_history row {history_id}")

        conn.commit()
        print("  Migration 012 complete: ai_settings_history ready")
        return True

    except sqlite3.Error as e:
        print(f"  Migration 012 failed: {e}")
        return False
    finally:
        conn.close()


if __name__ == "__main__":
    success = migrate()
    sys.exit(0 if success else 1)
