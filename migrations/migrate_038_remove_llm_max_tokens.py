#!/usr/bin/env python3
"""Migration 038: Remove the LLM max tokens settings keys.

Rally no longer caps LLM output: Anthropic requests use the model's own
maximum, looked up from the Models API when the briefing runs, and local
OpenAI-compatible requests send no max_tokens at all. That leaves three
settings keys nothing reads any more, so they are deleted:

  - llm_anthropic_max_tokens
  - llm_anthropic_max_tokens_mode
  - llm_local_max_tokens

llm_settings_history snapshots are left alone. Older ones still carry
"max_tokens" / "max_tokens_mode" in their JSON; the app ignores those keys,
and the snapshots stay an accurate record of what ran at the time.

Safe to run multiple times (idempotent).
"""

import os
import sqlite3
from pathlib import Path

REMOVED_KEYS = (
    "llm_anthropic_max_tokens",
    "llm_anthropic_max_tokens_mode",
    "llm_local_max_tokens",
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
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='settings'")
        if not cursor.fetchone():
            print("✓ settings does not exist yet; nothing to do")
            return True

        placeholders = ", ".join("?" for _ in REMOVED_KEYS)
        cursor.execute(f"DELETE FROM settings WHERE key IN ({placeholders})", REMOVED_KEYS)
        deleted = cursor.rowcount
        conn.commit()
        if deleted:
            print(f"✓ Migration complete: removed {deleted} LLM max tokens settings row(s)")
        else:
            print("✓ Migration: no LLM max tokens settings rows to remove (idempotent)")
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
