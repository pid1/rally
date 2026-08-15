"""Settings-derived helpers that need database access.

These deliberately do NOT live in ``utils/timezone.py``: that module is imported
by ``models.py`` (for ``now_utc``), so a helper there that needs ``Setting``
from ``models.py`` would close an import cycle. Keeping them in their own module
breaks it.
"""

from datetime import UTC, datetime, time
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from rally.models import Setting
from rally.utils.timezone import today_local


def local_timezone_name(db: Session) -> str:
    """The user's configured IANA timezone name, defaulting to UTC."""
    setting = db.query(Setting).filter(Setting.key == "local_timezone").first()
    return setting.value if setting and setting.value else "UTC"


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
