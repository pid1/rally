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
        from migrate_011_add_meal_reviews import migrate as migrate_011_add_meal_reviews
        from migrate_012_add_ai_settings_history import (
            migrate as migrate_012_add_ai_settings_history,
        )
        from migrate_014_configurable_nws_weather import (
            migrate as migrate_014_configurable_nws_weather,
        )
        from migrate_015_add_llm_settings_history import (
            migrate as migrate_015_add_llm_settings_history,
        )
        from migrate_016_add_stem_concept_history import (
            migrate as migrate_016_add_stem_concept_history,
        )
        from migrate_017_add_shopping_lists import (
            migrate as migrate_017_add_shopping_lists,
        )
        from migrate_018_add_sports_watchlist import (
            migrate as migrate_018_add_sports_watchlist,
        )
        from migrate_019_add_llm_max_tokens import (
            migrate as migrate_019_add_llm_max_tokens,
        )
        from migrate_020_add_native_calendaring import (
            migrate as migrate_020_add_native_calendaring,
        )
        from migrate_021_add_preparedness import (
            migrate as migrate_021_add_preparedness,
        )
        from migrate_022_add_home_location import (
            migrate as migrate_022_add_home_location,
        )
        from migrate_023_add_prep_reviews import (
            migrate as migrate_023_add_prep_reviews,
        )
        from migrate_024_add_calendar_cache import (
            migrate as migrate_024_add_calendar_cache,
        )
        from migrate_025_add_caldav_sync_tokens import (
            migrate as migrate_025_add_caldav_sync_tokens,
        )
        from migrate_026_add_shopping_sort_order import (
            migrate as migrate_026_add_shopping_sort_order,
        )
        from migrate_027_add_member_notification_prefs import (
            migrate as migrate_027_add_member_notification_prefs,
        )
        from migrate_add_caldav_support import migrate as migrate_008_add_caldav_support
        from migrate_add_completed_at import migrate as migrate_013_add_completed_at
        from migrate_add_custom_recurrence import migrate as migrate_009_add_custom_recurrence
        from migrate_add_dinner_plan_assignees import (
            migrate as migrate_005_add_dinner_plan_assignees,
        )
        from migrate_add_due_date import migrate as migrate_001_add_due_date
        from migrate_add_family_members import migrate as migrate_002_add_family_members
        from migrate_add_last_generated_date import (
            migrate as migrate_007_add_last_generated_date,
        )
        from migrate_add_meal_type import migrate as migrate_010_add_meal_type
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
        ("009_add_custom_recurrence", migrate_009_add_custom_recurrence),
        ("010_add_meal_type", migrate_010_add_meal_type),
        ("011_add_meal_reviews", migrate_011_add_meal_reviews),
        ("012_add_ai_settings_history", migrate_012_add_ai_settings_history),
        ("013_add_completed_at", migrate_013_add_completed_at),
        ("014_configurable_nws_weather", migrate_014_configurable_nws_weather),
        ("015_add_llm_settings_history", migrate_015_add_llm_settings_history),
        ("016_add_stem_concept_history", migrate_016_add_stem_concept_history),
        ("017_add_shopping_lists", migrate_017_add_shopping_lists),
        ("018_add_sports_watchlist", migrate_018_add_sports_watchlist),
        ("019_add_llm_max_tokens", migrate_019_add_llm_max_tokens),
        ("020_add_native_calendaring", migrate_020_add_native_calendaring),
        ("021_add_preparedness", migrate_021_add_preparedness),
        ("022_add_home_location", migrate_022_add_home_location),
        ("023_add_prep_reviews", migrate_023_add_prep_reviews),
        ("024_add_calendar_cache", migrate_024_add_calendar_cache),
        ("025_add_caldav_sync_tokens", migrate_025_add_caldav_sync_tokens),
        ("026_add_shopping_sort_order", migrate_026_add_shopping_sort_order),
        ("027_add_member_notification_prefs", migrate_027_add_member_notification_prefs),
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
