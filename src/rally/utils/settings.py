"""Settings-derived helpers that need database access.

These deliberately do NOT live in ``utils/timezone.py``: that module is imported
by ``models.py`` (for ``now_utc``), so a helper there that needs ``Setting``
from ``models.py`` would close an import cycle. Keeping them in their own module
breaks it.
"""

import os
import tomllib
from datetime import UTC, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from rally.database import SessionLocal
from rally.models import Setting
from rally.utils.timezone import today_local

DEFAULT_TIMEZONE = "UTC"


def config_toml_path() -> Path:
    """Where ``config.toml`` lives: ``/data`` in production, the working
    directory otherwise. The generator looks in the same place."""
    if os.getenv("RALLY_ENV", "development") == "production":
        return Path("/data/config.toml")
    return Path.cwd() / "config.toml"


def _config_toml() -> dict:
    try:
        with open(config_toml_path(), "rb") as f:
            return tomllib.load(f)
    except OSError, tomllib.TOMLDecodeError:
        return {}


def resolve_timezone_name(stored: str | None, config: dict | None = None) -> str:
    """The one rule for the family's timezone: the Settings row, else
    ``config.toml``'s ``local_timezone``, else UTC.

    Every part of Rally asks here — the web app, the generator and the 4 AM job
    in ``entrypoint.sh`` — because when they each had their own fallback (UTC in
    the app, ``config.toml`` then UTC in the generator, ``config.toml`` then
    America/Chicago in the 4 AM job) they disagreed about what day it was every
    evening on an install with no Settings row (#279). ``config`` is the parsed
    file for a caller that already has it; otherwise it is read here, and only
    when the row is missing.
    """
    if stored:
        return stored
    if config is None:
        config = _config_toml()
    return str(config.get("local_timezone") or DEFAULT_TIMEZONE)


def local_timezone_name(db: Session) -> str:
    """The family's IANA timezone name (see ``resolve_timezone_name``)."""
    setting = db.query(Setting).filter(Setting.key == "local_timezone").first()
    return resolve_timezone_name(setting.value if setting else None)


def configured_timezone_name() -> str:
    """``local_timezone_name`` with its own session, for ``entrypoint.sh``."""
    db = SessionLocal()
    try:
        return local_timezone_name(db)
    finally:
        db.close()


def home_location(db: Session) -> str:
    """Where the family lives, as free text ("Highland Village, TX").

    First-party rather than something buried in ``family_context``, because
    several things want it structurally and not as prose: the daily summary
    needs it to reason about local conditions, and anything that has to think
    about climate, seasons or regional hazards needs a place to start from.

    Free text on purpose. A structured address would need parsing, validation
    and a geocoder to be worth more than this, and nothing here needs
    coordinates — the weather integration already carries its own NWS URL.
    Empty is a valid answer and every caller must handle it.
    """
    setting = db.query(Setting).filter(Setting.key == "home_location").first()
    return (setting.value or "").strip() if setting else ""


def today_local_str(db: Session) -> str:
    """Today's local date as ``YYYY-MM-DD``, for comparing against date columns.

    Some tables store a day as a ``String(10)`` rather than an instant —
    ``notes.date``, ``dinner_plans.date`` — and the boundary between "current"
    and "past" for those is a string comparison, not ``today_start_utc``. Every
    view that partitions such a table must use this one helper, or a row could
    sit on both sides of midnight or neither.

    The callers are Notes (``/notes`` vs ``/notes/previous``), the Meal Planner
    (Current & Upcoming vs Previous Meals), Packing Lists (Coming Up vs Previous
    Packing Lists, the rule that a packing list is put on today or later, and that
    a past day is read-only), the dashboard's
    Daily Note lookup, and the shopping purge's once-per-local-day marker. They agree because they all
    ask here; two of them used to carry their own copy, which is exactly the
    drift this exists to stop.

    Use ``today_local()`` directly instead when you need a ``date`` to do
    arithmetic on rather than a string to compare.
    """
    return today_local(local_timezone_name(db)).strftime("%Y-%m-%d")


def today_start_utc(db: Session) -> datetime:
    """UTC instant of local midnight today, in the user's configured timezone.

    This is the boundary between "current" rows (todos shown on /todo, shopping
    items shown on /shopping) and previously-completed ones. Every view that
    partitions on "completed today" must use this same helper, or rows would
    appear on both sides of the boundary or neither.
    """
    tz_name = local_timezone_name(db)
    local_midnight = datetime.combine(today_local(tz_name), time.min, tzinfo=ZoneInfo(tz_name))
    return local_midnight.astimezone(UTC)
