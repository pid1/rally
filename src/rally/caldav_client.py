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


def sync_probe(calendar, stored_tokens: dict | None) -> tuple[dict[str, str] | None, bool]:
    """Ask a CalDAV server whether anything changed, without downloading events.

    RFC 6578 sync-collection. Handing back the token from last time returns
    only what has changed since — so an untouched calendar answers "nothing"
    in about a tenth of a second, where a full fetch and expansion of this
    install's iCloud account costs 2.1s.

    Returns ``(tokens, unchanged)``. ``tokens`` is ``None`` when the server does
    not support sync-collection at all, which is not an error: the caller falls
    back to fetching everything, exactly as before.

    A principal holds several server-side calendars and each carries its own
    token, so the answer is a map. A calendar appearing or disappearing counts
    as a change on its own — otherwise adding a shared calendar upstream would
    stay invisible until something inside it happened to move.
    """
    import caldav

    from rally.calendars.occurrence import SOURCE_CALDAV_APPLE

    url = calendar.url or (
        APPLE_CALDAV_URL if (calendar.cal_type or "") == SOURCE_CALDAV_APPLE else GOOGLE_CALDAV_URL
    )

    try:
        client = caldav.DAVClient(url=url, username=calendar.username, password=calendar.password)
        server_calendars = client.principal().calendars()
    except Exception as exc:
        # Reaching the principal is the same work a real fetch would do, so a
        # failure here is a genuine failure — let the caller handle it.
        raise RuntimeError(f"CalDAV connection failed: {exc}") from exc

    tokens: dict[str, str] = {}
    changed = False

    for server_cal in server_calendars:
        key = str(getattr(server_cal, "url", "") or getattr(server_cal, "name", "") or "?")
        previous = (stored_tokens or {}).get(key)

        try:
            # disable_fallback: the library will otherwise emulate
            # sync-collection with a full listing, which returns every object as
            # "changed" and costs more than the fetch it is meant to avoid.
            collection = server_cal.objects_by_sync_token(
                sync_token=previous, load_objects=False, disable_fallback=True
            )
            token = str(getattr(collection, "sync_token", "") or "")
            if previous is None:
                changed = True
            elif len(list(collection)) > 0:
                changed = True
        except Exception:
            # No sync-collection support on this server. Say so once, for the
            # whole calendar, rather than guessing per collection.
            return None, False

        tokens[key] = token

    if stored_tokens is not None and set(tokens) != set(stored_tokens):
        # A server-side calendar was added or removed.
        changed = True

    return tokens, not changed
