#!/usr/bin/env python3
"""Migration 041: The audit log — `family_members.email` and append-only audit rows.

`family_members.email` is the sign-in address (Cloudflare Access / GitHub
primary email, Tailscale login) that MCP calls and tailnet requests are
attributed by. Additive: every existing member gets NULL, meaning nothing is
attributed to them until an email is entered in Settings.

It also creates `audit_events` (with its indexes and append-only triggers)
and `mcp_connections`, rather than leaving them to `init_db()`: the data layer
refuses to write anything it cannot audit, so a database must never be
without the audit table, however it was built. The DDL matches what
SQLAlchemy emits for the models; the triggers are added to an existing
`audit_events` too, so the append-only guarantee does not depend on how the
table came to be.

Safe to run multiple times (idempotent).
"""

import os
import sqlite3
from pathlib import Path

TABLES = (
    """CREATE TABLE IF NOT EXISTS audit_events (
    id VARCHAR(36) NOT NULL,
    occurred_at DATETIME NOT NULL,
    actor_type VARCHAR(20) NOT NULL,
    actor_id VARCHAR(200) NOT NULL,
    actor_name VARCHAR(200),
    source VARCHAR(10) NOT NULL,
    client_info JSON,
    request_id VARCHAR(36) NOT NULL,
    action VARCHAR(100) NOT NULL,
    entity_type VARCHAR(100),
    entity_id VARCHAR(100),
    "before" JSON,
    "after" JSON,
    mcp_tool VARCHAR(100),
    mcp_arguments JSON,
    outcome VARCHAR(10) NOT NULL,
    error TEXT,
    flagged BOOLEAN NOT NULL,
    PRIMARY KEY (id)
    )""",
    "CREATE INDEX IF NOT EXISTS ix_audit_events_occurred_at ON audit_events (occurred_at)",
    "CREATE INDEX IF NOT EXISTS ix_audit_events_request_id ON audit_events (request_id)",
    "CREATE INDEX IF NOT EXISTS ix_audit_events_actor_id ON audit_events (actor_id)",
    "CREATE INDEX IF NOT EXISTS ix_audit_events_source ON audit_events (source)",
    "CREATE INDEX IF NOT EXISTS ix_audit_events_action ON audit_events (action)",
    "CREATE INDEX IF NOT EXISTS ix_audit_events_entity ON audit_events (entity_type, entity_id)",
    """CREATE TABLE IF NOT EXISTS mcp_connections (
    id VARCHAR(100) NOT NULL,
    family_member_id INTEGER,
    email VARCHAR(200) NOT NULL,
    jwt_subject VARCHAR(200),
    client VARCHAR(200),
    first_ip VARCHAR(64),
    last_ip VARCHAR(64),
    first_seen_at DATETIME NOT NULL,
    last_seen_at DATETIME NOT NULL,
    PRIMARY KEY (id)
    )""",
)

TRIGGERS = (
    """CREATE TRIGGER IF NOT EXISTS audit_events_no_update
    BEFORE UPDATE ON audit_events
    BEGIN SELECT RAISE(ABORT, 'audit_events is append-only'); END""",
    """CREATE TRIGGER IF NOT EXISTS audit_events_no_delete
    BEFORE DELETE ON audit_events
    BEGIN SELECT RAISE(ABORT, 'audit_events is append-only'); END""",
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
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='family_members'"
        )
        if cursor.fetchone():
            cursor.execute("PRAGMA table_info(family_members)")
            columns = [col[1] for col in cursor.fetchall()]
            if "email" in columns:
                print("✓ Migration: family_members.email already exists (idempotent)")
            else:
                cursor.execute("ALTER TABLE family_members ADD COLUMN email VARCHAR(200)")
                print("✓ Migration: family_members.email added")

        for ddl in TABLES + TRIGGERS:
            cursor.execute(ddl)
        print("✓ Migration: audit_events (append-only) and mcp_connections present")

        conn.commit()
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
