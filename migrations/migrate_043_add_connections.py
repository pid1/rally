#!/usr/bin/env python3
"""Migration 042: Pre-created MCP connections.

Until now an MCP "connection" was whatever rally-mcp saw arrive on its one
`/mcp` path: a household member on a Claude client, recorded in
`mcp_connections` on first sight. From here on a connection is created in
advance in Rally (the Connections page), with its own endpoint, auth method,
tool permissions and IP allowlist, in the new `connections` table.

`mcp_connections` is kept, with its rows, as what it always recorded: OAuth
sign-ins (one person on one client). It gains `connection_id`, the connection a
sign-in arrived on. That keeps every existing row meaningful — the history of
who signed in from where — instead of guessing a pre-created connection out of
each one.

When the database already has sign-ins, the existing Claude connector is live
on `/mcp`, and it must keep working until it is re-created as a pre-created
connection. So this seeds one `legacy` connection: OAuth, full access, enabled,
allowed from Anthropic's published outbound ranges, which is what the edge
already allowed `/mcp` from. Every existing sign-in is attached to it. A
database with no sign-ins gets no legacy connection: nothing is connected, so
there is nothing to keep working, and `/mcp` is refused until someone creates a
connection.

The Anthropic ranges are copied here rather than imported (migrations are
self-contained); `rally.connections.IP_PRESETS` is the source of truth.

Safe to run multiple times (idempotent).
"""

import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

CONNECTIONS = """CREATE TABLE IF NOT EXISTS connections (
    id VARCHAR(40) NOT NULL,
    name VARCHAR(100) NOT NULL,
    auth_method VARCHAR(10) NOT NULL,
    preset VARCHAR(20) NOT NULL,
    tools JSON,
    ip_allowlist JSON NOT NULL,
    status VARCHAR(10) NOT NULL,
    authorized_by INTEGER,
    legacy BOOLEAN NOT NULL,
    token_hash VARCHAR(64),
    token_hint VARCHAR(16),
    expires_at DATETIME,
    first_used_at DATETIME,
    last_seen_at DATETIME,
    last_ip VARCHAR(64),
    revoked_at DATETIME,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT ck_connections_auth_method CHECK (auth_method IN ('oauth', 'token')),
    CONSTRAINT ck_connections_status CHECK (status IN ('enabled', 'disabled', 'revoked'))
)"""

LEGACY_ID = "legacy"
# Anthropic's outbound IP ranges, from
# https://platform.claude.com/docs/en/api/ip-addresses (checked 2026-11-05).
ANTHROPIC_RANGES = '["160.79.104.0/21", "153.61.208.0/20"]'


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
        cursor.execute(CONNECTIONS)

        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='mcp_connections'"
        )
        if not cursor.fetchone():
            # 041 creates it; a database this old gets it from 041 on the same run.
            conn.commit()
            print("✓ Migration: connections present (no mcp_connections yet)")
            return True

        cursor.execute("PRAGMA table_info(mcp_connections)")
        columns = [col[1] for col in cursor.fetchall()]
        if "connection_id" in columns:
            print("✓ Migration: mcp_connections.connection_id already exists (idempotent)")
        else:
            cursor.execute("ALTER TABLE mcp_connections ADD COLUMN connection_id VARCHAR(40)")
            print("✓ Migration: mcp_connections.connection_id added")

        cursor.execute("SELECT COUNT(*) FROM mcp_connections")
        has_sign_ins = cursor.fetchone()[0] > 0
        cursor.execute("SELECT 1 FROM connections WHERE id = ?", (LEGACY_ID,))
        if has_sign_ins and not cursor.fetchone():
            now = datetime.now(UTC).replace(tzinfo=None).isoformat(sep=" ")
            cursor.execute(
                "INSERT INTO connections (id, name, auth_method, preset, tools, ip_allowlist,"
                " status, authorized_by, legacy, created_at, updated_at)"
                " VALUES (?, ?, 'oauth', 'full', NULL, ?, 'enabled', NULL, 1, ?, ?)",
                (LEGACY_ID, "Claude (legacy /mcp)", ANTHROPIC_RANGES, now, now),
            )
            print("✓ Migration: legacy /mcp connection created for the existing connector")
        if has_sign_ins:
            cursor.execute(
                "UPDATE mcp_connections SET connection_id = ? WHERE connection_id IS NULL",
                (LEGACY_ID,),
            )

        conn.commit()
        print("✓ Migration: connections present")
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
