"""Declined- and cancelled-event detection, in one place.

This logic existed twice — ``SummaryGenerator._is_event_declined`` and a
standalone copy in ``caldav_client`` written to avoid a circular import. Two
copies of a heuristic is two sets of behaviour the moment one is fixed, and
this one has already been fixed once for Outlook.
"""

from __future__ import annotations


def _partstat(attendee) -> str:
    params = getattr(attendee, "params", None)
    if not params:
        return ""
    return str(params.get("PARTSTAT", "")).upper()


def is_event_declined(component, owner_email: str | None = None) -> bool:
    """Whether an event should be treated as off the calendar.

    Signals, across providers:

    - ``STATUS:CANCELLED`` — the organiser withdrew it.
    - ``PARTSTAT=DECLINED`` on the owner's own attendee line (Google, Apple).
    - ``X-MICROSOFT-CDO-BUSYSTATUS:FREE`` alongside a declined attendee
      (Outlook/Exchange).

    With ``owner_email`` the answer is exact: only that person's PARTSTAT is
    consulted, and an event they have not declined is kept no matter what the
    other attendees did. Without it there is no way to know which attendee is
    "us", so the fallbacks are deliberately conservative — an event is dropped
    only when *everyone* declined, or when Outlook has additionally marked the
    time free.
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
        owner = owner_email.strip().lower()
        for attendee in attendees:
            address = str(attendee).replace("mailto:", "").strip().lower()
            if address == owner:
                return _partstat(attendee) == "DECLINED"
        return False

    busy_status = component.get("X-MICROSOFT-CDO-BUSYSTATUS")
    if busy_status and str(busy_status).upper() == "FREE":
        if any(_partstat(attendee) == "DECLINED" for attendee in attendees):
            return True

    return all(_partstat(attendee) == "DECLINED" for attendee in attendees)
