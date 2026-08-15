"""CalDAV client for Rally — Google and Apple CalDAV via app-specific passwords.

Both Google and Apple expose CalDAV endpoints that accept basic auth with
app-specific passwords (requires 2FA on the account). This module returns
``Occurrence`` objects, the same shape the ICS and native adapters produce, so
the merge layer cannot tell which transport an event arrived over.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

import caldav
from icalendar import Calendar as ICalCalendar

from rally.calendars.ics import occurrences_from_components
from rally.calendars.occurrence import (
    SOURCE_CALDAV_APPLE,
    SOURCE_CALDAV_GOOGLE,
    Occurrence,
)

# Default CalDAV server URLs
GOOGLE_CALDAV_URL = "https://apidata.googleusercontent.com/caldav/v2/"
APPLE_CALDAV_URL = "https://caldav.icloud.com/"


def _parse_caldav_events(
    caldav_client: caldav.DAVClient,
    local_tz: ZoneInfo,
    *,
    window_start: datetime,
    window_end: datetime,
    owner_email: str | None = None,
    source: str = SOURCE_CALDAV_GOOGLE,
    calendar_id: int | None = None,
    label: str = "",
    member: str | None = None,
    member_color: str | None = None,
) -> list[Occurrence]:
    """Fetch occurrences from every calendar under a CalDAV principal.

    The server expands recurrences (``expand=True``); the window is re-applied
    locally because a server answers a date range on its own terms.
    """
    principal = caldav_client.principal()
    server_calendars = principal.calendars()

    occurrences: list[Occurrence] = []
    for server_cal in server_calendars:
        cal_name = getattr(server_cal, "name", None) or "Calendar"
        try:
            search_results = server_cal.search(
                start=window_start, end=window_end, event=True, expand=True
            )
        except Exception as exc:
            print(f"  Warning: failed to search CalDAV calendar '{cal_name}': {exc}")
            continue

        for item in search_results:
            try:
                ical = ICalCalendar.from_ical(item.data)
            except Exception:
                continue

            components = [c for c in ical.walk() if c.name == "VEVENT"]
            occurrences.extend(
                occurrences_from_components(
                    components,
                    window_start=window_start,
                    window_end=window_end,
                    local_tz=local_tz,
                    owner_email=owner_email,
                    source=source,
                    calendar_id=calendar_id,
                    calendar_label=label or cal_name,
                    member=member,
                    member_color=member_color,
                )
            )

    return occurrences


def _fetch_caldav(
    calendar_record,
    local_tz,
    *,
    window_start,
    window_end,
    default_url: str,
    source: str,
    provider: str,
    label: str = "",
    member: str | None = None,
    member_color: str | None = None,
) -> list[Occurrence]:
    if not calendar_record.username or not calendar_record.password:
        print(f"  Skipping {calendar_record.label}: missing {provider} CalDAV credentials")
        return []

    client = caldav.DAVClient(
        url=calendar_record.url or default_url,
        username=calendar_record.username,
        password=calendar_record.password,
    )
    owner_email = calendar_record.owner_email or calendar_record.username

    try:
        return _parse_caldav_events(
            client,
            local_tz,
            window_start=window_start,
            window_end=window_end,
            owner_email=owner_email,
            source=source,
            calendar_id=calendar_record.id,
            label=label or calendar_record.label,
            member=member,
            member_color=member_color,
        )
    except Exception as exc:
        print(f"  Error fetching {provider} CalDAV for {calendar_record.label}: {exc}")
        return []


def fetch_google_caldav(
    calendar_record,
    local_tz,
    *,
    window_start,
    window_end,
    label: str = "",
    member: str | None = None,
    member_color: str | None = None,
) -> list[Occurrence]:
    """Fetch occurrences from Google CalDAV using username + app password.

    Requires 2FA enabled on the Google account. Generate an app-specific
    password at https://myaccount.google.com/apppasswords.
    """
    return _fetch_caldav(
        calendar_record,
        local_tz,
        window_start=window_start,
        window_end=window_end,
        default_url=GOOGLE_CALDAV_URL,
        source=SOURCE_CALDAV_GOOGLE,
        provider="Google",
        label=label,
        member=member,
        member_color=member_color,
    )


def fetch_apple_caldav(
    calendar_record,
    local_tz,
    *,
    window_start,
    window_end,
    label: str = "",
    member: str | None = None,
    member_color: str | None = None,
) -> list[Occurrence]:
    """Fetch occurrences from Apple iCloud CalDAV using username + app password.

    Requires 2FA enabled on the Apple account. Generate an app-specific
    password at https://appleid.apple.com/account/manage.
    """
    return _fetch_caldav(
        calendar_record,
        local_tz,
        window_start=window_start,
        window_end=window_end,
        default_url=APPLE_CALDAV_URL,
        source=SOURCE_CALDAV_APPLE,
        provider="Apple",
        label=label,
        member=member,
        member_color=member_color,
    )
