"""Fetch every configured calendar and return one merged list of occurrences.

This is the seam the generator and the `/calendar` page both sit behind. It
owns two things that used to live inside the generator: which sources exist,
and what "the next seven days" means.

The window is expressed in the family's **local** dates. The old code measured
it with ``today_utc()``, so for a family in ``America/Chicago`` the day rolled
over at 6 or 7 PM and the far edge of the window was truncated mid-evening.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from rally.calendars.merge import merge_occurrences
from rally.calendars.native import collect_native
from rally.calendars.occurrence import (
    SOURCE_CALDAV_APPLE,
    SOURCE_CALDAV_GOOGLE,
    SOURCE_ICS,
    FetchResult,
    Occurrence,
    resolve_local,
)
from rally.models import Calendar, FamilyMember

ICS_TIMEOUT_SECONDS = 10


def window_bounds(
    start_day: date, end_day_exclusive: date, local_tz: ZoneInfo
) -> tuple[datetime, datetime]:
    """The UTC instants bounding a span of local days."""
    return (
        resolve_local(datetime.combine(start_day, time.min), local_tz),
        resolve_local(datetime.combine(end_day_exclusive, time.min), local_tz),
    )


def _fetch_ics(
    calendar: Calendar,
    *,
    window_start: datetime,
    window_end: datetime,
    local_tz: ZoneInfo,
    label: str,
    member: str | None,
    member_color: str | None,
) -> list[Occurrence]:
    import requests

    from rally.calendars.ics import occurrences_from_ical_text

    response = requests.get(calendar.url, timeout=ICS_TIMEOUT_SECONDS)
    response.raise_for_status()
    return occurrences_from_ical_text(
        response.text,
        window_start=window_start,
        window_end=window_end,
        local_tz=local_tz,
        owner_email=calendar.owner_email,
        source=SOURCE_ICS,
        calendar_id=calendar.id,
        calendar_label=label,
        member=member,
        member_color=member_color,
    )


def collect_occurrences(
    db: Session,
    *,
    start_day: date,
    end_day_exclusive: date,
    local_tz: ZoneInfo,
    config: dict | None = None,
    sources: set[str] | None = None,
) -> FetchResult:
    """Every occurrence in the window, from every configured source.

    Failures are collected rather than raised: one unreachable feed must
    shorten the list and name itself, never fail the page or the 4 AM summary.
    """
    window_start, window_end = window_bounds(start_day, end_day_exclusive, local_tz)
    result = FetchResult()
    groups: list[list[Occurrence]] = []

    calendars = (
        db.query(Calendar, FamilyMember)
        .outerjoin(FamilyMember, Calendar.family_member_id == FamilyMember.id)
        .all()
    )

    native_calendar_ids = [
        calendar.id for calendar, _ in calendars if (calendar.cal_type or "ics") == "native"
    ]
    if native_calendar_ids and (sources is None or "native" in sources):
        try:
            groups.append(
                collect_native(
                    db,
                    window_start=window_start,
                    window_end=window_end,
                    local_tz=local_tz,
                    calendar_ids=native_calendar_ids,
                )
            )
        except Exception as exc:  # pragma: no cover - defensive
            print(f"Error expanding native events: {exc}")
            result.failures.append("Rally events")

    for calendar, owner in calendars:
        cal_type = calendar.cal_type or "ics"
        if cal_type == "native":
            continue
        if sources is not None and "external" not in sources:
            continue

        member = owner.name if owner else None
        member_color = owner.color if owner else None
        label = f"{calendar.label} ({member})" if member else calendar.label

        try:
            if cal_type == "caldav_google":
                from rally.caldav_client import fetch_google_caldav

                groups.append(
                    fetch_google_caldav(
                        calendar,
                        local_tz,
                        window_start=window_start,
                        window_end=window_end,
                        label=label,
                        member=member,
                        member_color=member_color,
                    )
                )
            elif cal_type == "caldav_apple":
                from rally.caldav_client import fetch_apple_caldav

                groups.append(
                    fetch_apple_caldav(
                        calendar,
                        local_tz,
                        window_start=window_start,
                        window_end=window_end,
                        label=label,
                        member=member,
                        member_color=member_color,
                    )
                )
            else:
                groups.append(
                    _fetch_ics(
                        calendar,
                        window_start=window_start,
                        window_end=window_end,
                        local_tz=local_tz,
                        label=label,
                        member=member,
                        member_color=member_color,
                    )
                )
        except Exception as exc:
            print(f"Error fetching calendar {label}: {exc}")
            result.failures.append(label)

    # config.toml fallback, for installs that predate the Settings UI.
    if not calendars and config and "calendars" in config:
        owners = config.get("calendar_owners", {})
        from rally.calendars.ics import occurrences_from_ical_text

        for key, url in config["calendars"].items():
            try:
                import requests

                response = requests.get(url, timeout=ICS_TIMEOUT_SECONDS)
                response.raise_for_status()
                groups.append(
                    occurrences_from_ical_text(
                        response.text,
                        window_start=window_start,
                        window_end=window_end,
                        local_tz=local_tz,
                        owner_email=owners.get(key),
                        source=SOURCE_ICS,
                        calendar_label=key,
                    )
                )
            except Exception as exc:
                print(f"Error fetching calendar {key}: {exc}")
                result.failures.append(key)

    result.occurrences = merge_occurrences(groups)
    return result


def default_window(local_tz: ZoneInfo, days: int = 7) -> tuple[date, date]:
    """Today through ``days`` days out, measured in local dates."""
    from rally.utils.timezone import now_utc

    today = now_utc().astimezone(local_tz).date()
    return today, today + timedelta(days=days)


__all__ = [
    "SOURCE_CALDAV_APPLE",
    "SOURCE_CALDAV_GOOGLE",
    "collect_occurrences",
    "default_window",
    "window_bounds",
]
