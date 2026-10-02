#!/usr/bin/env python3
"""Migration 035: name templates as templates, and let a day exist without one.

Five steps, in one transaction (SQLite's DDL is transactional), so a run that
fails part-way leaves the database exactly as it was and simply runs again:

1. Rename the tables that belong to a template: ``packing_lists`` becomes
   ``packing_list_templates``, ``packing_list_items`` becomes
   ``packing_list_template_items``, ``packing_list_schedules`` becomes
   ``packing_list_template_schedules``.
2. Rename the columns that point at one: ``packing_list_id`` becomes
   ``packing_list_template_id``; ``item_id`` on a day's own changes and checks
   becomes ``template_item_id``. ``RENAME COLUMN`` needs SQLite 3.25+.
3. Rebuild ``packing_list_days``, because SQLite cannot drop the ``NOT NULL``
   migration 034 put on its template id: a day whose template was deleted has
   none. The rebuild also adds ``name`` and ``description`` (a templateless
   day's own) and ``item_order`` (a day's hand-arranged order).
4. Drop every index named for an old table or column and create it again
   under the name the models give it, with the same columns and uniqueness.
5. Recount item history. ``times_added`` now means how many past days a name
   was on a packing list, so every existing row is set to exactly that,
   reading each past day the way ``rally.packing_lists.resolve_day`` does.
   No row is created (a forgotten suggestion stays forgotten), and the
   counting marker, ``packing_history_counted_through``, is set to yesterday,
   so the app's daily pass carries on from today. The marker is how a second
   run knows the recount is done.

Renames move no data: every day keeps its template, and nothing a family can
see changes except the counts autocomplete ranks by.

Safe to run multiple times (idempotent).
"""

import os
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

TABLE_RENAMES = [
    ("packing_lists", "packing_list_templates"),
    ("packing_list_items", "packing_list_template_items"),
    ("packing_list_schedules", "packing_list_template_schedules"),
]

COLUMN_RENAMES = [
    ("packing_list_template_items", "packing_list_id", "packing_list_template_id"),
    ("packing_list_template_schedules", "packing_list_id", "packing_list_template_id"),
    ("packing_list_day_items", "item_id", "template_item_id"),
    ("packing_list_day_checks", "item_id", "template_item_id"),
]

# Indexes migration 034 created under names that spell an old table or column.
# ``ix_packing_list_days_*`` go with the old table when it is rebuilt.
OLD_INDEXES = [
    "ix_packing_lists_name_nocase",
    "ix_packing_list_items_packing_list_id",
    "ix_packing_list_schedules_packing_list_id",
    "ix_packing_list_day_items_item_id",
    "ix_packing_list_day_items_day_item",
    "ix_packing_list_day_checks_item_id",
    "ix_packing_list_day_checks_day_item",
]

# The names ``rally.models`` gives them.
NEW_INDEXES = [
    "CREATE UNIQUE INDEX IF NOT EXISTS ix_packing_list_templates_name_nocase"
    " ON packing_list_templates (name COLLATE NOCASE)",
    "CREATE INDEX IF NOT EXISTS ix_packing_list_template_items_packing_list_template_id"
    " ON packing_list_template_items (packing_list_template_id)",
    "CREATE UNIQUE INDEX IF NOT EXISTS ix_packing_list_template_schedules_packing_list_template_id"
    " ON packing_list_template_schedules (packing_list_template_id)",
    "CREATE INDEX IF NOT EXISTS ix_packing_list_days_packing_list_template_id"
    " ON packing_list_days (packing_list_template_id)",
    "CREATE INDEX IF NOT EXISTS ix_packing_list_days_date ON packing_list_days (date)",
    "CREATE INDEX IF NOT EXISTS ix_packing_list_days_schedule_id ON packing_list_days (schedule_id)",
    "CREATE UNIQUE INDEX IF NOT EXISTS ix_packing_list_days_packing_list_template_date"
    " ON packing_list_days (packing_list_template_id, date)",
    "CREATE INDEX IF NOT EXISTS ix_packing_list_day_items_template_item_id"
    " ON packing_list_day_items (template_item_id)",
    "CREATE UNIQUE INDEX IF NOT EXISTS ix_packing_list_day_items_day_template_item"
    " ON packing_list_day_items (day_id, template_item_id) WHERE template_item_id IS NOT NULL",
    "CREATE INDEX IF NOT EXISTS ix_packing_list_day_checks_template_item_id"
    " ON packing_list_day_checks (template_item_id)",
    "CREATE UNIQUE INDEX IF NOT EXISTS ix_packing_list_day_checks_day_template_item"
    " ON packing_list_day_checks (day_id, template_item_id)",
]

NEW_DAYS_TABLE = """
    CREATE TABLE packing_list_days_new (
        id INTEGER PRIMARY KEY,
        packing_list_template_id INTEGER,
        name VARCHAR(100),
        description TEXT,
        date VARCHAR(10) NOT NULL,
        label VARCHAR(200),
        schedule_id INTEGER,
        pack_days_before INTEGER,
        label_edited BOOLEAN NOT NULL DEFAULT 0,
        item_order JSON,
        created_at DATETIME NOT NULL,
        updated_at DATETIME NOT NULL
    )
"""

COUNTED_THROUGH_SETTING = "packing_history_counted_through"


def _tables(cursor):
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
    return {row[0] for row in cursor.fetchall()}


def _columns(cursor, table):
    cursor.execute(f"PRAGMA table_info({table})")
    return {row[1]: row for row in cursor.fetchall()}


def _history_key(name):
    # The same key ``rally.packing_lists.history_key`` builds. Computed in
    # Python, not SQL: SQLite's lower() only folds ASCII.
    return (name or "").strip().casefold()


def _local_today(cursor):
    cursor.execute("SELECT value FROM settings WHERE key = 'local_timezone'")
    row = cursor.fetchone()
    try:
        zone = ZoneInfo(row[0]) if row and row[0] else ZoneInfo("UTC")
    except ZoneInfoNotFoundError:
        zone = ZoneInfo("UTC")
    return datetime.now(zone).date()


def _rebuild_days(cursor):
    """Make ``packing_list_template_id`` nullable and add the new columns."""
    columns = _columns(cursor, "packing_list_days")
    template = columns.get("packing_list_template_id") or columns.get("packing_list_id")
    if "name" in columns and template[1] == "packing_list_template_id" and not template[3]:
        return False
    source = template[1]
    cursor.execute(NEW_DAYS_TABLE)
    # Every column name here comes from this migration, never from data.
    cursor.execute(
        "INSERT INTO packing_list_days_new (id, packing_list_template_id, date, label,"
        " schedule_id, pack_days_before, label_edited, created_at, updated_at)"
        f" SELECT id, {source}, date, label, schedule_id, pack_days_before, label_edited,"
        " created_at, updated_at FROM packing_list_days"
    )
    cursor.execute("DROP TABLE packing_list_days")
    cursor.execute("ALTER TABLE packing_list_days_new RENAME TO packing_list_days")
    return True


def _past_day_names(cursor, today):
    """Every name on every day before ``today``, once per item on the day,
    the way ``resolve_day`` reads a day."""
    cursor.execute(
        "SELECT id, packing_list_template_id FROM packing_list_days WHERE date < ?",
        (today.isoformat(),),
    )
    days = cursor.fetchall()
    template_items = {}
    names = []
    for day_id, template_id in days:
        cursor.execute(
            "SELECT template_item_id, name, removed FROM packing_list_day_items WHERE day_id = ?",
            (day_id,),
        )
        overlay = cursor.fetchall()
        readings = {row[0]: row for row in overlay if row[0] is not None}
        if template_id is not None:
            if template_id not in template_items:
                cursor.execute(
                    "SELECT id, name FROM packing_list_template_items"
                    " WHERE packing_list_template_id = ?",
                    (template_id,),
                )
                template_items[template_id] = cursor.fetchall()
            for item_id, item_name in template_items[template_id]:
                reading = readings.get(item_id)
                if reading is not None and reading[2]:
                    continue  # Removed on this day
                names.append(reading[1] if reading is not None else item_name)
        names.extend(row[1] for row in overlay if row[0] is None)
    return names


def _recount_history(cursor):
    # Every real database has had a settings table since migration 003. One
    # without it has nowhere to keep the marker; the app's pass then starts
    # at yesterday on its own (``count_packed_days``).
    if "settings" not in _tables(cursor):
        return False
    cursor.execute("SELECT 1 FROM settings WHERE key = ?", (COUNTED_THROUGH_SETTING,))
    if cursor.fetchone():
        return False
    today = _local_today(cursor)
    counts = {}
    for name in _past_day_names(cursor, today):
        key = _history_key(name)
        counts[key] = counts.get(key, 0) + 1
    cursor.execute("SELECT id, name_key FROM packing_list_item_history")
    for row_id, name_key in cursor.fetchall():
        cursor.execute(
            "UPDATE packing_list_item_history SET times_added = ? WHERE id = ?",
            (counts.get(name_key, 0), row_id),
        )
    cursor.execute(
        "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, datetime('now'))",
        (COUNTED_THROUGH_SETTING, (today - timedelta(days=1)).isoformat()),
    )
    return True


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

    # Autocommit off by hand: the whole migration is one explicit transaction.
    conn = sqlite3.connect(db_path, isolation_level=None)
    cursor = conn.cursor()

    try:
        tables = _tables(cursor)
        if "packing_list_days" not in tables:
            print("✓ Migration: no packing list tables yet - nothing to rename")
            return True

        cursor.execute("BEGIN")
        done = []
        for old, new in TABLE_RENAMES:
            if old in tables and new not in tables:
                cursor.execute(f"ALTER TABLE {old} RENAME TO {new}")
                done.append(f"{old} → {new}")
        for table, old, new in COLUMN_RENAMES:
            if old in _columns(cursor, table):
                cursor.execute(f"ALTER TABLE {table} RENAME COLUMN {old} TO {new}")
                done.append(f"{table}.{old} → {new}")
        if _rebuild_days(cursor):
            done.append("packing_list_days rebuilt")
        for name in OLD_INDEXES:
            cursor.execute(f"DROP INDEX IF EXISTS {name}")
        for ddl in NEW_INDEXES:
            cursor.execute(ddl)
        if _recount_history(cursor):
            done.append("item history recounted")
        cursor.execute("COMMIT")

        if done:
            print("✓ Migration complete: " + "; ".join(done))
        else:
            print("✓ Migration: packing list templates already named (idempotent)")
        return True

    except sqlite3.Error as e:
        if conn.in_transaction:
            cursor.execute("ROLLBACK")
        print(f"✗ Migration failed: {e}")
        return False
    finally:
        conn.close()


if __name__ == "__main__":
    import sys

    success = migrate()
    sys.exit(0 if success else 1)
