#!/usr/bin/env python3
"""Migration 015: Add llm_settings_history table for LLM config snapshotting.

Creates the llm_settings_history table (versioned snapshots of the coupled
LLM provider + model configuration, stored as a JSON value), seeds it from
the existing llm_provider / provider-specific model rows in the key-value
settings table, and points the new current_llm_config_history_id settings
key at the seed row. Unlike migration 012, the original settings rows are
preserved — they remain the source of truth read by the generator and the
LLM connectivity test.

Safe to run multiple times (idempotent).
"""

import json
import os
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

POINTER_KEY = "current_llm_config_history_id"


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
            CREATE TABLE IF NOT EXISTS llm_settings_history (
                id INTEGER NOT NULL PRIMARY KEY,
                field_name VARCHAR(50) NOT NULL,
                value TEXT NOT NULL,
                created_at DATETIME NOT NULL,
                last_used_at DATETIME NOT NULL
            )
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS ix_llm_settings_history_field_name
            ON llm_settings_history(field_name)
        """)

        # Settings table may not exist yet on a fresh database
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='settings'")
        if not cursor.fetchone():
            conn.commit()
            print("  Migration 015: llm_settings_history created; no settings table to seed from")
            return True

        # CHECK: Is the config already seeded?
        cursor.execute("SELECT value FROM settings WHERE key = ?", (POINTER_KEY,))
        if cursor.fetchone():
            conn.commit()
            print(f"  Migration 015: {POINTER_KEY} already set (idempotent)")
            return True

        cursor.execute("SELECT value FROM settings WHERE key = 'llm_provider'")
        row = cursor.fetchone()
        if row is None:
            conn.commit()
            print("  Migration 015: no existing llm_provider setting to migrate")
            return True

        provider = row[0]
        model_key = "llm_anthropic_model" if provider == "anthropic" else "llm_local_model"
        cursor.execute("SELECT value FROM settings WHERE key = ?", (model_key,))
        model_row = cursor.fetchone()
        model = model_row[0] if model_row else ""

        # EXECUTE: Seed a coupled snapshot and point the setting at it.
        # The original llm_provider / model settings rows are kept as-is.
        now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")  # SQLAlchemy naive-UTC format
        cursor.execute(
            """
            INSERT INTO llm_settings_history (field_name, value, created_at, last_used_at)
            VALUES (?, ?, ?, ?)
            """,
            ("llm_config", json.dumps({"provider": provider, "model": model}), now, now),
        )
        history_id = cursor.lastrowid
        cursor.execute(
            "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?)",
            (POINTER_KEY, str(history_id), now),
        )
        print(
            f"  Migration 015: seeded llm_config ({provider} / {model or 'no model'}) "
            f"into llm_settings_history row {history_id}"
        )

        conn.commit()
        print("  Migration 015 complete: llm_settings_history ready")
        return True

    except sqlite3.Error as e:
        print(f"  Migration 015 failed: {e}")
        return False
    finally:
        conn.close()


if __name__ == "__main__":
    success = migrate()
    sys.exit(0 if success else 1)
