#!/usr/bin/env python3
"""Migration 019: Add per-provider LLM max tokens settings and snapshot fields.

Backfills 4000 — the value every install has effectively been running under
the hardcoded LLM_MAX_TOKENS constant — into:

  - llm_settings_history: adds "max_tokens" (4000) and "max_tokens_mode"
    ("custom") to every llm_config row's JSON value that doesn't already
    carry them. Rows whose value doesn't parse as JSON are skipped, not
    rewritten, so a corrupt row can't be silently mangled.
  - settings: seeds llm_anthropic_max_tokens, llm_local_max_tokens (both
    "4000"), and llm_anthropic_max_tokens_mode ("custom") when absent —
    without these the new Max Tokens fields would render empty on first load.

Because the backfilled value matches current behavior exactly, this migration
changes nothing observable by itself.

Safe to run multiple times (idempotent).
"""

import json
import os
import sqlite3
import sys
from pathlib import Path

DEFAULT_MAX_TOKENS = "4000"


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
        # Both tables may not exist yet on a fresh database.
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name IN ('llm_settings_history', 'settings')"
        )
        existing_tables = {row[0] for row in cursor.fetchall()}

        # BACKFILL: llm_settings_history rows missing the new JSON keys.
        rewritten = 0
        skipped = 0
        if "llm_settings_history" in existing_tables:
            cursor.execute(
                "SELECT id, value FROM llm_settings_history WHERE field_name = 'llm_config'"
            )
            rows = cursor.fetchall()
            for row_id, raw_value in rows:
                try:
                    config = json.loads(raw_value)
                except TypeError, ValueError:
                    skipped += 1
                    continue

                changed = False
                if "max_tokens" not in config:
                    config["max_tokens"] = int(DEFAULT_MAX_TOKENS)
                    changed = True
                if "max_tokens_mode" not in config:
                    config["max_tokens_mode"] = "custom"
                    changed = True

                if changed:
                    cursor.execute(
                        "UPDATE llm_settings_history SET value = ? WHERE id = ?",
                        (json.dumps(config), row_id),
                    )
                    rewritten += 1

        # BACKFILL: settings keys the generator and the Settings UI both read.
        seeded = []
        if "settings" in existing_tables:
            seed_keys = {
                "llm_anthropic_max_tokens": DEFAULT_MAX_TOKENS,
                "llm_local_max_tokens": DEFAULT_MAX_TOKENS,
                "llm_anthropic_max_tokens_mode": "custom",
            }
            for key, value in seed_keys.items():
                cursor.execute("SELECT 1 FROM settings WHERE key = ?", (key,))
                if cursor.fetchone():
                    continue
                cursor.execute(
                    "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, datetime('now'))",
                    (key, value),
                )
                seeded.append(key)

        conn.commit()

        if rewritten or seeded:
            print(
                f"✓ Migration 019: backfilled {rewritten} llm_settings_history row(s) "
                f"(skipped {skipped} unparseable), seeded settings keys: {seeded or 'none'}"
            )
        else:
            print("✓ Migration 019: nothing to backfill (idempotent)")
        return True

    except sqlite3.Error as e:
        print(f"✗ Migration 019 failed: {e}")
        return False
    finally:
        conn.close()


if __name__ == "__main__":
    success = migrate()
    sys.exit(0 if success else 1)
