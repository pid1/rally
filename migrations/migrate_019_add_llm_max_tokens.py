#!/usr/bin/env python3
"""Migration 019: Add per-provider LLM max tokens settings (retired).

This used to backfill a 4000-token budget into every llm_settings_history
snapshot and seed the llm_anthropic_max_tokens, llm_local_max_tokens and
llm_anthropic_max_tokens_mode settings keys. Rally no longer has a configurable
output token budget (Anthropic requests use the model's own maximum, local
requests send none), and migration 038 deletes those keys.

Migrations run on every container start, in order, so leaving the seeding in
place would put the keys back on every boot just for 038 to delete them again,
and would stamp a 4000 budget into every new snapshot. It is kept as a no-op so
the numbered sequence stays intact.

Safe to run multiple times (idempotent).
"""

import sys


def migrate():
    """Run the migration. Return True on success, False on failure."""
    print("✓ Migration 019 retired: LLM output token limits were removed (see 038)")
    return True


if __name__ == "__main__":
    success = migrate()
    sys.exit(0 if success else 1)
