#!/usr/bin/env python3
"""Migration to switch weather config from OpenWeather to a configurable NWS URL.

Removes the legacy OpenWeather settings (weather_api_key, weather_lat,
weather_lon) and seeds a single weather_nws_url setting if none exists.

Run this once to upgrade existing databases. Safe to run multiple times (idempotent).
"""

import os
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

DEFAULT_NWS_URL = (
    "https://forecast.weather.gov/MapClick.php"
    "?lat=33.085&lon=-97.0542&unit=0&lg=english&FcstType=dwml"
)

LEGACY_KEYS = ("weather_api_key", "weather_lat", "weather_lon")


def migrate():
    # Get database path from environment or use default
    db_path = os.environ.get("RALLY_DB_PATH")

    if not db_path:
        prod_path = Path("/data/rally.db")
        dev_path = Path(__file__).parent.parent / "rally.db"
        db_path = str(prod_path) if prod_path.exists() else str(dev_path)

    db_path = Path(db_path)

    if not db_path.exists():
        print(f"✓ Database not found at {db_path}")
        print("  No migration needed - database will be created with correct schema on first run.")
        return True

    print(f"Checking database at {db_path}...")

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    try:
        # Settings table may not exist yet on very old databases
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='settings'")
        if not cursor.fetchone():
            print("✓ Migration: settings table does not exist yet (nothing to migrate)")
            return True

        # Step 1: Remove legacy OpenWeather settings
        removed = 0
        for key in LEGACY_KEYS:
            cursor.execute("DELETE FROM settings WHERE key = ?", (key,))
            removed += cursor.rowcount
        if removed:
            print(f"✓ Migration: removed {removed} legacy OpenWeather setting(s)")
        else:
            print("✓ Migration: no legacy OpenWeather settings present (idempotent check)")

        # Step 2: Seed weather_nws_url if it's missing
        cursor.execute("SELECT value FROM settings WHERE key = 'weather_nws_url'")
        if cursor.fetchone():
            print("✓ Migration: weather_nws_url already configured (idempotent check)")
        else:
            # Match SQLAlchemy's SQLite datetime format (naive UTC)
            now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")
            cursor.execute(
                "INSERT INTO settings (key, value, updated_at) VALUES ('weather_nws_url', ?, ?)",
                (DEFAULT_NWS_URL, now),
            )
            print("✓ Migration: seeded default weather_nws_url")

        conn.commit()
        return True

    except sqlite3.Error as e:
        print(f"✗ Migration failed: {e}")
        return False
    finally:
        conn.close()


if __name__ == "__main__":
    success = migrate()
    sys.exit(0 if success else 1)
