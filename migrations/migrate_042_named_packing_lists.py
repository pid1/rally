#!/usr/bin/env python3
"""Migration 042: Packing lists with their own name that include templates (#281).

A packing list on a day used to *be* one template kept in sync, named after
it. Now a list has its own name and its own items, and includes any number of
templates, each read live. There is one model, so every existing day becomes a
named list:

1. Add ``packing_list_day_templates`` (a list's included templates, with a
   ``position``; unique per list and template) and its indexes.
2. For every day with a ``packing_list_template_id``, include that template on
   it and give it a name: the template's (a day kept in sync was called what
   its template was called). A templateless day keeps its own name. Then
   **rebuild ``packing_list_days``** without ``packing_list_template_id`` (and
   so without the one-template-per-date index), ``name`` now required. Its
   checks, own items, readings, order, label, lead time and schedule are kept
   as they are, since they never named the template.
3. **Rebuild ``event_packing_lists``** keyed on a list (``list_key``, its name
   casefolded) rather than a template: each row becomes a list named after its
   template that includes it (``name``, ``template_ids``), the shape picking a
   bare template now makes.
4. **Rebuild ``packing_list_day_events``** keyed on that ``list_key``.
5. **"Every day" placements** (#270's ``events.packing_list_span``) are gone:
   an event's lists now go on an occurrence's first day only. The days already
   made are kept, as ordinary lists that no longer move with the event; from
   today on, every link that is not on its occurrence's first day is dropped
   (so no new ones are made), and a date taken off by hand (``day_id`` NULL)
   is dropped too. Past links are left alone. ``events.packing_list_span``
   stays in the table, unread.

All in **one transaction** (SQLite DDL is transactional), so a failure part
way leaves the database as it was. Each step checks before it runs, so a second
run changes nothing.

Safe to run multiple times (idempotent).
"""

import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DAY_TEMPLATES_TABLE = """
    CREATE TABLE IF NOT EXISTS packing_list_day_templates (
        id INTEGER PRIMARY KEY,
        day_id INTEGER NOT NULL,
        packing_list_template_id INTEGER NOT NULL,
        position INTEGER NOT NULL,
        created_at DATETIME NOT NULL
    )
"""

DAY_TEMPLATES_INDEXES = (
    "CREATE INDEX IF NOT EXISTS ix_packing_list_day_templates_day_id"
    " ON packing_list_day_templates (day_id)",
    "CREATE INDEX IF NOT EXISTS ix_packing_list_day_templates_packing_list_template_id"
    " ON packing_list_day_templates (packing_list_template_id)",
    "CREATE UNIQUE INDEX IF NOT EXISTS ix_packing_list_day_templates_day_template"
    " ON packing_list_day_templates (day_id, packing_list_template_id)",
)

NEW_DAYS_TABLE = """
    CREATE TABLE packing_list_days_new (
        id INTEGER PRIMARY KEY,
        name VARCHAR(100) NOT NULL,
        description TEXT,
        date VARCHAR(10) NOT NULL,
        label VARCHAR(200),
        schedule_id INTEGER,
        pack_days_before INTEGER,
        label_edited BOOLEAN NOT NULL,
        item_order JSON,
        created_at DATETIME NOT NULL,
        updated_at DATETIME NOT NULL
    )
"""

DAYS_INDEXES = (
    "CREATE INDEX IF NOT EXISTS ix_packing_list_days_date ON packing_list_days (date)",
    "CREATE INDEX IF NOT EXISTS ix_packing_list_days_schedule_id ON packing_list_days (schedule_id)",
)

NEW_EVENT_LISTS_TABLE = """
    CREATE TABLE event_packing_lists_new (
        id INTEGER PRIMARY KEY,
        event_id INTEGER NOT NULL,
        list_key VARCHAR(100) NOT NULL,
        name VARCHAR(100),
        template_ids JSON,
        occurrence_date VARCHAR(10),
        removed BOOLEAN NOT NULL,
        created_at DATETIME NOT NULL
    )
"""

EVENT_LISTS_INDEXES = (
    "CREATE INDEX IF NOT EXISTS ix_event_packing_lists_event_id ON event_packing_lists (event_id)",
    "CREATE UNIQUE INDEX IF NOT EXISTS ix_event_packing_lists_event_list_occurrence"
    " ON event_packing_lists (event_id, list_key, IFNULL(occurrence_date, ''))",
)

NEW_DAY_EVENTS_TABLE = """
    CREATE TABLE packing_list_day_events_new (
        id INTEGER PRIMARY KEY,
        day_id INTEGER,
        event_id INTEGER NOT NULL,
        occurrence_date VARCHAR(10) NOT NULL,
        list_key VARCHAR(100) NOT NULL,
        date VARCHAR(10) NOT NULL,
        created_at DATETIME NOT NULL
    )
"""

DAY_EVENTS_INDEXES = (
    "CREATE INDEX IF NOT EXISTS ix_packing_list_day_events_day_id"
    " ON packing_list_day_events (day_id)",
    "CREATE INDEX IF NOT EXISTS ix_packing_list_day_events_event_id"
    " ON packing_list_day_events (event_id)",
    "CREATE UNIQUE INDEX IF NOT EXISTS ix_packing_list_day_events_unique"
    " ON packing_list_day_events (event_id, occurrence_date, list_key, date)",
)


def _tables(cursor):
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
    return {row[0] for row in cursor.fetchall()}


def _columns(cursor, table):
    cursor.execute(f"PRAGMA table_info({table})")
    return {row[1] for row in cursor.fetchall()}


def _list_key(name):
    # ``rally.packing_lists.list_key``: trimmed and casefolded, in Python
    # because SQLite's lower() only folds ASCII.
    return (name or "").strip().casefold()


def _local_today(cursor, tables):
    zone = ZoneInfo("UTC")
    if "settings" in tables:
        cursor.execute("SELECT value FROM settings WHERE key = 'local_timezone'")
        row = cursor.fetchone()
        try:
            zone = ZoneInfo(row[0]) if row and row[0] else zone
        except ZoneInfoNotFoundError:
            pass
    return datetime.now(zone).date().isoformat()


def _template_names(cursor, tables):
    if "packing_list_templates" not in tables:
        return {}
    cursor.execute("SELECT id, name FROM packing_list_templates")
    return dict(cursor.fetchall())


def _convert_days(cursor, names):
    """Steps 1 and 2. Returns whether the days table was rebuilt."""
    cursor.execute(DAY_TEMPLATES_TABLE)
    for ddl in DAY_TEMPLATES_INDEXES:
        cursor.execute(ddl)
    if "packing_list_template_id" not in _columns(cursor, "packing_list_days"):
        return False
    cursor.execute(
        "SELECT id, packing_list_template_id, name, created_at FROM packing_list_days ORDER BY id"
    )
    for day_id, template_id, name, created_at in cursor.fetchall():
        if template_id is not None and template_id in names:
            cursor.execute(
                "INSERT OR IGNORE INTO packing_list_day_templates"
                " (day_id, packing_list_template_id, position, created_at) VALUES (?, ?, 0, ?)",
                (day_id, template_id, created_at),
            )
        if template_id is not None:
            name = names.get(template_id) or name
        cursor.execute(
            "UPDATE packing_list_days SET name = ? WHERE id = ?",
            (name or "Packing list", day_id),
        )
    cursor.execute(NEW_DAYS_TABLE)
    cursor.execute(
        "INSERT INTO packing_list_days_new (id, name, description, date, label, schedule_id,"
        " pack_days_before, label_edited, item_order, created_at, updated_at)"
        " SELECT id, name, description, date, label, schedule_id, pack_days_before,"
        " IFNULL(label_edited, 0), item_order, created_at, updated_at FROM packing_list_days"
    )
    cursor.execute("DROP TABLE packing_list_days")
    cursor.execute("ALTER TABLE packing_list_days_new RENAME TO packing_list_days")
    for ddl in DAYS_INDEXES:
        cursor.execute(ddl)
    return True


def _convert_event_lists(cursor, names):
    """Step 3. Returns whether the table was rebuilt."""
    if "packing_list_template_id" not in _columns(cursor, "event_packing_lists"):
        return False
    cursor.execute(
        "SELECT id, event_id, packing_list_template_id, occurrence_date, removed, created_at"
        " FROM event_packing_lists ORDER BY id"
    )
    rows = cursor.fetchall()
    cursor.execute(NEW_EVENT_LISTS_TABLE)
    for row_id, event_id, template_id, occurrence_date, removed, created_at in rows:
        name = names.get(template_id)
        if name is None:
            continue  # A template that is gone brings nothing.
        cursor.execute(
            "INSERT OR IGNORE INTO event_packing_lists_new (id, event_id, list_key, name,"
            " template_ids, occurrence_date, removed, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                row_id,
                event_id,
                _list_key(name),
                None if removed else name,
                None if removed else json.dumps([template_id]),
                occurrence_date,
                removed,
                created_at,
            ),
        )
    cursor.execute("DROP TABLE event_packing_lists")
    cursor.execute("ALTER TABLE event_packing_lists_new RENAME TO event_packing_lists")
    for ddl in EVENT_LISTS_INDEXES:
        cursor.execute(ddl)
    return True


def _convert_day_events(cursor, names):
    """Step 4. Returns whether the table was rebuilt."""
    if "packing_list_template_id" not in _columns(cursor, "packing_list_day_events"):
        return False
    cursor.execute(
        "SELECT id, day_id, event_id, occurrence_date, packing_list_template_id, date,"
        " created_at FROM packing_list_day_events ORDER BY id"
    )
    rows = cursor.fetchall()
    cursor.execute(NEW_DAY_EVENTS_TABLE)
    for row_id, day_id, event_id, occurrence_date, template_id, on_date, created_at in rows:
        name = names.get(template_id)
        if name is None:
            continue
        cursor.execute(
            "INSERT OR IGNORE INTO packing_list_day_events_new (id, day_id, event_id,"
            " occurrence_date, list_key, date, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (row_id, day_id, event_id, occurrence_date, _list_key(name), on_date, created_at),
        )
    cursor.execute("DROP TABLE packing_list_day_events")
    cursor.execute("ALTER TABLE packing_list_day_events_new RENAME TO packing_list_day_events")
    for ddl in DAY_EVENTS_INDEXES:
        cursor.execute(ddl)
    return True


def _first_day(cursor, event_id, occurrence_date):
    """The first local date an occurrence covers: its override's start when it
    was moved, else its original date."""
    cursor.execute(
        "SELECT start_date FROM event_overrides WHERE event_id = ? AND occurrence_date = ?",
        (event_id, occurrence_date),
    )
    row = cursor.fetchone()
    return row[0] if row and row[0] else occurrence_date


def _settle_every_day(cursor, tables, today):
    """Step 5. Returns how many links it dropped."""
    if "packing_list_day_events" not in tables:
        return 0
    has_overrides = "event_overrides" in tables
    cursor.execute(
        "SELECT id, day_id, event_id, occurrence_date, date FROM packing_list_day_events"
        " WHERE date >= ?",
        (today,),
    )
    dropped = 0
    for link_id, day_id, event_id, occurrence_date, on_date in cursor.fetchall():
        first = _first_day(cursor, event_id, occurrence_date) if has_overrides else occurrence_date
        if day_id is None or on_date != first:
            cursor.execute("DELETE FROM packing_list_day_events WHERE id = ?", (link_id,))
            dropped += 1
    return dropped


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
            print("✓ Migration: no packing list tables yet - nothing to convert")
            return True

        cursor.execute("BEGIN")
        names = _template_names(cursor, tables)
        done = []
        if _convert_days(cursor, names):
            done.append("days became named lists including their template")
        if "event_packing_lists" in tables and _convert_event_lists(cursor, names):
            done.append("event lists keyed by name")
        if "packing_list_day_events" in tables and _convert_day_events(cursor, names):
            done.append("event day links keyed by name")
            # Only on the run that converts the links: afterwards every link
            # the sync writes is on its occurrence's first day already.
            dropped = _settle_every_day(cursor, _tables(cursor), _local_today(cursor, tables))
            if dropped:
                done.append(f"{dropped} 'Every day' link(s) from today on released")
        cursor.execute("COMMIT")

        if done:
            print("✓ Migration complete: " + "; ".join(done))
        else:
            print("✓ Migration: packing lists already named (idempotent)")
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
