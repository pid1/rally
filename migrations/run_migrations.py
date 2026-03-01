#!/usr/bin/env python3
"""Run all database migrations in order.

This script runs all migration files in sequence. Each migration should be idempotent.
Add new migrations by importing them here and adding to the MIGRATIONS list.
"""

import sys


def run_migrations():
    """Run all migrations in order."""
    # Import migrations
    try:
        from migrate_add_dinner_plan_assignees import (
            migrate as migrate_005_add_dinner_plan_assignees,
        )
        from migrate_add_due_date import migrate as migrate_001_add_due_date
        from migrate_add_family_members import migrate as migrate_002_add_family_members
        from migrate_add_caldav_support import migrate as migrate_008_add_caldav_support
        from migrate_add_last_generated_date import (
            migrate as migrate_007_add_last_generated_date,
        )
        from migrate_add_recurring_todos import migrate as migrate_004_add_recurring_todos
        from migrate_add_reminder_window import migrate as migrate_006_add_reminder_window
        from migrate_add_settings import migrate as migrate_003_add_settings
    except ImportError as e:
        print(f"✗ Failed to import migrations: {e}")
        return False

    # List of migrations to run (in order)
    migrations = [
        ("001_add_due_date", migrate_001_add_due_date),
        ("002_add_family_members", migrate_002_add_family_members),
        ("003_add_settings", migrate_003_add_settings),
        ("004_add_recurring_todos", migrate_004_add_recurring_todos),
        ("005_add_dinner_plan_assignees", migrate_005_add_dinner_plan_assignees),
        ("006_add_reminder_window", migrate_006_add_reminder_window),
        ("007_add_last_generated_date", migrate_007_add_last_generated_date),
        ("008_add_caldav_support", migrate_008_add_caldav_support),
    ]

    print("=" * 60)
    print("Running Rally database migrations...")
    print("=" * 60)

    success = True
    for name, migration_func in migrations:
        print(f"\n[{name}]")
        try:
            result = migration_func()
            if result is False:
                print(f"✗ Migration {name} failed")
                success = False
                break
        except Exception as e:
            print(f"✗ Migration {name} raised exception: {e}")
            success = False
            break

    print("\n" + "=" * 60)
    if success:
        print("✓ All migrations completed successfully")
    else:
        print("✗ Migrations failed")
    print("=" * 60)

    return success


if __name__ == "__main__":
    success = run_migrations()
    sys.exit(0 if success else 1)
