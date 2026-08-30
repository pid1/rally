#!/usr/bin/env python3
"""Migration 029: Move every family member onto the closed color palette.

`family_members.color` used to be free-form hex that nothing validated and no
screen could set, so in practice every member sat on the old `#333333` default:
a near-black that renders four family members as four identical dots on the
calendar. The column is now a closed set of five palette values, validated at
the API boundary.

No schema change — the column already exists. This is a data backfill, and it
has to move *every* member onto a palette value rather than only the ones on
the old default: a closed set with legacy values still in the table is not
closed, and the API would reject the next write of a row Rally itself created.

Colors are handed out by `id ASC`, cycling the palette, which is the same rule
`POST /api/family` uses for a new member. Whatever a hand-set color meant is
deliberately not preserved — the guarantee being bought is that any two members
are distinguishable, and reading intent out of an arbitrary hex is guesswork
that a five-entry palette cannot honor anyway.

A member already on a palette value is left alone. That is what makes this
idempotent, and it is also what stops a container restart from overwriting a
color somebody deliberately chose after the first run.

The palette is duplicated here rather than imported from `rally.member_colors`:
migrations are self-contained by convention, so they keep working against an
old database without the application on the path.

Safe to run multiple times (idempotent).
"""

import os
import sqlite3
from pathlib import Path

# Mirrors rally.member_colors.PALETTE, darkest first. See that module for why
# there are five and why the set is closed.
PALETTE = ("#315277", "#af2c3d", "#8859b1", "#3b8c61", "#a38b43")


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
        if not cursor.fetchone():
            print("✓ Migration: family_members table does not exist yet (idempotent)")
            return True

        cursor.execute("SELECT id, name, color FROM family_members ORDER BY id ASC")
        members = cursor.fetchall()

        if not members:
            print("✓ Migration: no family members to recolor (idempotent)")
            return True

        # Position in the cycle counts *every* member, not just the ones being
        # rewritten, so a member already on a palette color still consumes the
        # slot they hold. Assigning around them would hand their color to
        # somebody else and produce the duplicate this migration exists to avoid.
        stale = [
            (index, member_id, name)
            for index, (member_id, name, color) in enumerate(members)
            if (color or "").lower() not in PALETTE
        ]

        if not stale:
            print("✓ Migration: every family member is already on a palette color (idempotent)")
            return True

        print(f"  Recoloring {len(stale)} of {len(members)} family member(s)...")
        for index, member_id, name in stale:
            color = PALETTE[index % len(PALETTE)]
            cursor.execute("UPDATE family_members SET color = ? WHERE id = ?", (color, member_id))
            print(f"    {name} -> {color}")

        conn.commit()
        print(f"✓ Migration complete: {len(stale)} family member(s) moved onto the palette")
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
