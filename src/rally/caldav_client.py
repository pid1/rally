"""CalDAV client for Rally — Google and Apple CalDAV via app-specific passwords.

Both Google and Apple expose CalDAV endpoints that accept basic auth with
app-specific passwords (requires 2FA on the account). This module provides a
unified interface that returns events in the same format as the legacy ICS path,
so the generator can consume them identically.
"""

from datetime import timedelta

import caldav
from icalendar import Calendar as ICalCalendar

from rally.utils.timezone import ensure_utc, today_utc

# Default CalDAV server URLs
GOOGLE_CALDAV_URL = "https://apidata.googleusercontent.com/caldav/v2/"
APPLE_CALDAV_URL = "https://caldav.icloud.com/"


def _is_event_declined(component, owner_email: str | None = None) -> bool:
    """Check if a calendar event has been declined.

    Mirrors the logic in SummaryGenerator._is_event_declined but as a
    standalone function to avoid circular imports.
    """
    status = component.get("status")
    if status and str(status).upper() == "CANCELLED":
        return True

    attendees = component.get("attendee")
    if not attendees:
        return False

    if not isinstance(attendees, list):
        attendees = [attendees]

    if owner_email:
        owner_email_lower = owner_email.strip().lower()
        for att in attendees:
            att_email = str(att).replace("mailto:", "").strip().lower()
            if att_email == owner_email_lower:
                partstat = ""
                if hasattr(att, "params"):
                    partstat = str(att.params.get("PARTSTAT", ""))
                return partstat.upper() == "DECLINED"
        return False

    # No owner email: conservative heuristics
    busystatus = component.get("X-MICROSOFT-CDO-BUSYSTATUS")
    if busystatus and str(busystatus).upper() == "FREE":
        has_declined = any(
            hasattr(att, "params") and str(att.params.get("PARTSTAT", "")).upper() == "DECLINED"
            for att in attendees
        )
        if has_declined:
            return True

    all_declined = all(
        hasattr(att, "params") and str(att.params.get("PARTSTAT", "")).upper() == "DECLINED"
        for att in attendees
    )
    return all_declined


def _parse_caldav_events(caldav_client: caldav.DAVClient, local_tz, owner_email=None):
    """Fetch events from a CalDAV principal, returning a list of event dicts.

    Each calendar discovered under the principal produces events.
    Events are expanded (recurring instances resolved by the server) and filtered
    to the next 7 days.
    """
    today = today_utc()
    end_date = today + timedelta(days=7)

    principal = caldav_client.principal()
    server_calendars = principal.calendars()

    all_events = []
    for server_cal in server_calendars:
        cal_name = getattr(server_cal, "name", None) or "Calendar"
        try:
            search_results = server_cal.search(start=today, end=end_date, event=True, expand=True)
        except Exception as exc:
            print(f"  Warning: failed to search CalDAV calendar '{cal_name}': {exc}")
            continue

        for item in search_results:
            try:
                ical = ICalCalendar.from_ical(item.data)
            except Exception:
                continue

            for component in ical.walk():
                if component.name != "VEVENT":
                    continue

                dtstart = component.get("dtstart")
                if not dtstart:
                    continue

                # Skip declined / cancelled events
                if _is_event_declined(component, owner_email):
                    continue

                event_date = dtstart.dt
                if hasattr(event_date, "date"):
                    event_date = event_date.date()

                summary = str(component.get("summary", "Untitled Event"))
                description = str(component.get("description", ""))
                location = str(component.get("location", ""))

                time_str = ""
                if hasattr(dtstart.dt, "strftime"):
                    dt = dtstart.dt
                    if hasattr(dt, "tzinfo") and dt.tzinfo is not None:
                        dt = ensure_utc(dt).astimezone(local_tz)
                    time_str = dt.strftime("%I:%M %p %Z").lstrip("0")

                all_events.append(
                    {
                        "summary": summary,
                        "time": time_str,
                        "date": event_date.strftime("%Y-%m-%d"),
                        "description": description,
                        "location": location,
                    }
                )

    all_events.sort(key=lambda e: (e["date"], e["time"]))
    return all_events


def fetch_google_caldav(calendar_record, local_tz):
    """Fetch events from Google CalDAV using username + app-specific password.

    Requires 2FA enabled on the Google account. Generate an app-specific
    password at https://myaccount.google.com/apppasswords.

    Returns list of event dicts, or empty list on failure.
    """
    if not calendar_record.username or not calendar_record.password:
        print(f"  Skipping {calendar_record.label}: missing Google CalDAV credentials")
        return []

    url = calendar_record.url or GOOGLE_CALDAV_URL
    client = caldav.DAVClient(
        url=url,
        username=calendar_record.username,
        password=calendar_record.password,
    )

    owner_email = calendar_record.owner_email or calendar_record.username

    try:
        return _parse_caldav_events(client, local_tz, owner_email)
    except Exception as exc:
        print(f"  Error fetching Google CalDAV for {calendar_record.label}: {exc}")
        return []


def fetch_apple_caldav(calendar_record, local_tz):
    """Fetch events from Apple iCloud CalDAV using username + app-specific password.

    Requires 2FA enabled on the Apple account. Generate an app-specific
    password at https://appleid.apple.com/account/manage.

    Returns list of event dicts, or empty list on failure.
    """
    if not calendar_record.username or not calendar_record.password:
        print(f"  Skipping {calendar_record.label}: missing Apple CalDAV credentials")
        return []

    url = calendar_record.url or APPLE_CALDAV_URL
    client = caldav.DAVClient(
        url=url,
        username=calendar_record.username,
        password=calendar_record.password,
    )

    owner_email = calendar_record.owner_email or calendar_record.username

    try:
        return _parse_caldav_events(client, local_tz, owner_email)
    except Exception as exc:
        print(f"  Error fetching Apple CalDAV for {calendar_record.label}: {exc}")
        return []
