"""Tests for the CalDAV client: declined-event detection, event parsing, and the
Google/Apple fetch wrappers (with caldav.DAVClient stubbed)."""

from types import SimpleNamespace
from zoneinfo import ZoneInfo

from icalendar import Event, vCalAddress

from rally.caldav_client import (
    _is_event_declined,
    _parse_caldav_events,
    fetch_apple_caldav,
    fetch_google_caldav,
)

_ICS = (
    b"BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\n"
    b"SUMMARY:Meeting\r\nDTSTART:20260315T100000Z\r\n"
    b"END:VEVENT\r\nEND:VCALENDAR"
)
_ICS_CANCELLED = (
    b"BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\n"
    b"SUMMARY:Dead\r\nDTSTART:20260315T100000Z\r\nSTATUS:CANCELLED\r\n"
    b"END:VEVENT\r\nEND:VCALENDAR"
)


def _attendee(email, partstat=None):
    addr = vCalAddress(f"mailto:{email}")
    if partstat:
        addr.params["PARTSTAT"] = partstat
    return addr


# --- _is_event_declined --------------------------------------------------------


def test_declined_when_cancelled():
    ev = Event()
    ev.add("status", "CANCELLED")
    assert _is_event_declined(ev) is True


def test_declined_when_owner_partstat_declined():
    ev = Event()
    ev.add("attendee", _attendee("me@example.com", "DECLINED"))
    assert _is_event_declined(ev, owner_email="me@example.com") is True


def test_declined_when_all_attendees_declined():
    ev = Event()
    ev.add("attendee", _attendee("a@example.com", "DECLINED"))
    ev.add("attendee", _attendee("b@example.com", "DECLINED"))
    assert _is_event_declined(ev) is True


def test_not_declined_when_accepted():
    ev = Event()
    ev.add("attendee", _attendee("a@example.com", "ACCEPTED"))
    assert _is_event_declined(ev) is False


def test_owner_not_in_attendees_is_not_declined():
    # Owner isn't listed (they may be the organizer) -> treated as not declined.
    ev = Event()
    ev.add("attendee", _attendee("someone@else.com", "DECLINED"))
    assert _is_event_declined(ev, owner_email="me@example.com") is False


def test_outlook_busystatus_free_with_declined_attendee():
    ev = Event()
    ev.add("X-MICROSOFT-CDO-BUSYSTATUS", "FREE")
    ev.add("attendee", _attendee("a@example.com", "DECLINED"))
    ev.add("attendee", _attendee("b@example.com", "ACCEPTED"))
    assert _is_event_declined(ev) is True


# --- _parse_caldav_events ------------------------------------------------------


class _FakeItem:
    def __init__(self, data):
        self.data = data


class _FakeCalendar:
    def __init__(self, items, name="Cal"):
        self._items = items
        self.name = name

    def search(self, **kwargs):
        return self._items


class _FakeClient:
    def __init__(self, calendars):
        self._calendars = calendars

    def principal(self):
        return SimpleNamespace(calendars=lambda: self._calendars)


def test_parse_returns_event_dicts():
    client = _FakeClient([_FakeCalendar([_FakeItem(_ICS)])])

    events = _parse_caldav_events(client, ZoneInfo("UTC"))

    assert len(events) == 1
    assert events[0]["summary"] == "Meeting"
    assert events[0]["date"] == "2026-03-15"


def test_parse_skips_declined_events():
    client = _FakeClient([_FakeCalendar([_FakeItem(_ICS_CANCELLED)])])
    assert _parse_caldav_events(client, ZoneInfo("UTC")) == []


class _RaisingSearchCalendar:
    name = "Bad"

    def search(self, **kwargs):
        raise RuntimeError("search failed")


def test_parse_skips_calendar_when_search_raises():
    client = _FakeClient([_RaisingSearchCalendar()])
    assert _parse_caldav_events(client, ZoneInfo("UTC")) == []


def test_parse_skips_unparseable_item():
    client = _FakeClient([_FakeCalendar([_FakeItem(b"this is not iCalendar data")])])
    assert _parse_caldav_events(client, ZoneInfo("UTC")) == []


def test_parse_skips_event_without_dtstart():
    ics = b"BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nSUMMARY:NoStart\r\nEND:VEVENT\r\nEND:VCALENDAR"
    client = _FakeClient([_FakeCalendar([_FakeItem(ics)])])
    assert _parse_caldav_events(client, ZoneInfo("UTC")) == []


# --- fetch wrappers ------------------------------------------------------------


def test_fetch_google_missing_credentials_returns_empty():
    record = SimpleNamespace(username=None, password=None, label="G", url=None, owner_email=None)
    assert fetch_google_caldav(record, ZoneInfo("UTC")) == []


def test_fetch_apple_missing_credentials_returns_empty():
    record = SimpleNamespace(username=None, password=None, label="A", url=None, owner_email=None)
    assert fetch_apple_caldav(record, ZoneInfo("UTC")) == []


def test_fetch_google_success(monkeypatch):
    import caldav

    monkeypatch.setattr(
        caldav, "DAVClient", lambda **kwargs: _FakeClient([_FakeCalendar([_FakeItem(_ICS)])])
    )
    record = SimpleNamespace(
        username="user", password="secret", label="G", url="https://dav.example", owner_email=None
    )

    events = fetch_google_caldav(record, ZoneInfo("UTC"))

    assert len(events) == 1
    assert events[0]["summary"] == "Meeting"


def test_fetch_apple_success(monkeypatch):
    import caldav

    monkeypatch.setattr(
        caldav, "DAVClient", lambda **kwargs: _FakeClient([_FakeCalendar([_FakeItem(_ICS)])])
    )
    record = SimpleNamespace(
        username="user", password="secret", label="A", url="https://dav.example", owner_email=None
    )

    events = fetch_apple_caldav(record, ZoneInfo("UTC"))

    assert len(events) == 1
    assert events[0]["summary"] == "Meeting"


class _RaisingPrincipalClient:
    def principal(self):
        raise RuntimeError("dav connection failed")


def test_fetch_google_error_returns_empty(monkeypatch):
    import caldav

    monkeypatch.setattr(caldav, "DAVClient", lambda **kwargs: _RaisingPrincipalClient())
    record = SimpleNamespace(
        username="user", password="secret", label="G", url="https://dav.example", owner_email=None
    )
    assert fetch_google_caldav(record, ZoneInfo("UTC")) == []


def test_fetch_apple_error_returns_empty(monkeypatch):
    import caldav

    monkeypatch.setattr(caldav, "DAVClient", lambda **kwargs: _RaisingPrincipalClient())
    record = SimpleNamespace(
        username="user", password="secret", label="A", url="https://dav.example", owner_email=None
    )
    assert fetch_apple_caldav(record, ZoneInfo("UTC")) == []
