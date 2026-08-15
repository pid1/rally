"""Tests for the CalDAV client: declined-event detection, event parsing, and the
Google/Apple fetch wrappers (with caldav.DAVClient stubbed)."""

from datetime import UTC, datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from icalendar import Event, vCalAddress

from rally.caldav_client import (
    _parse_caldav_events,
    fetch_apple_caldav,
    fetch_google_caldav,
)
from rally.calendars.declined import is_event_declined as _is_event_declined

# The fixture events all sit in March 2026; the window brackets them.
_WINDOW_START = datetime(2026, 3, 1, tzinfo=UTC)
_WINDOW_END = datetime(2026, 4, 1, tzinfo=UTC)


def _parse(client, tz=ZoneInfo("UTC"), **kwargs):
    return _parse_caldav_events(
        client, tz, window_start=_WINDOW_START, window_end=_WINDOW_END, **kwargs
    )


def _fetch(fn, record, tz=ZoneInfo("UTC")):
    return fn(record, tz, window_start=_WINDOW_START, window_end=_WINDOW_END)


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


def test_parse_returns_occurrences():
    client = _FakeClient([_FakeCalendar([_FakeItem(_ICS)])])

    events = _parse(client)

    assert len(events) == 1
    assert events[0].title == "Meeting"
    assert events[0].start_local_date == "2026-03-15"
    assert events[0].start == datetime(2026, 3, 15, 10, 0, tzinfo=UTC)
    assert events[0].editable is False


def test_parse_skips_declined_events():
    client = _FakeClient([_FakeCalendar([_FakeItem(_ICS_CANCELLED)])])
    assert _parse(client) == []


class _RaisingSearchCalendar:
    name = "Bad"

    def search(self, **kwargs):
        raise RuntimeError("search failed")


def test_parse_skips_calendar_when_search_raises():
    client = _FakeClient([_RaisingSearchCalendar()])
    assert _parse(client) == []


def test_parse_skips_unparseable_item():
    client = _FakeClient([_FakeCalendar([_FakeItem(b"this is not iCalendar data")])])
    assert _parse(client) == []


def test_parse_skips_event_without_dtstart():
    ics = b"BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nSUMMARY:NoStart\r\nEND:VEVENT\r\nEND:VCALENDAR"
    client = _FakeClient([_FakeCalendar([_FakeItem(ics)])])
    assert _parse(client) == []


def test_parse_drops_events_outside_the_window():
    """A CalDAV server answers a range on its own terms; we re-apply ours."""
    ics = (
        b"BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\n"
        b"SUMMARY:Later\r\nDTSTART:20260615T100000Z\r\n"
        b"END:VEVENT\r\nEND:VCALENDAR"
    )
    client = _FakeClient([_FakeCalendar([_FakeItem(ics)])])
    assert _parse(client) == []


# --- fetch wrappers ------------------------------------------------------------


def test_fetch_google_missing_credentials_returns_empty():
    record = SimpleNamespace(
        id=1, username=None, password=None, label="G", url=None, owner_email=None
    )
    assert _fetch(fetch_google_caldav, record) == []


def test_fetch_apple_missing_credentials_returns_empty():
    record = SimpleNamespace(
        id=1, username=None, password=None, label="A", url=None, owner_email=None
    )
    assert _fetch(fetch_apple_caldav, record) == []


def test_fetch_google_success(monkeypatch):
    import caldav

    monkeypatch.setattr(
        caldav, "DAVClient", lambda **kwargs: _FakeClient([_FakeCalendar([_FakeItem(_ICS)])])
    )
    record = SimpleNamespace(
        id=1,
        username="user",
        password="secret",
        label="G",
        url="https://dav.example",
        owner_email=None,
    )

    events = _fetch(fetch_google_caldav, record)

    assert len(events) == 1
    assert events[0].title == "Meeting"


def test_fetch_apple_success(monkeypatch):
    import caldav

    monkeypatch.setattr(
        caldav, "DAVClient", lambda **kwargs: _FakeClient([_FakeCalendar([_FakeItem(_ICS)])])
    )
    record = SimpleNamespace(
        id=1,
        username="user",
        password="secret",
        label="A",
        url="https://dav.example",
        owner_email=None,
    )

    events = _fetch(fetch_apple_caldav, record)

    assert len(events) == 1
    assert events[0].title == "Meeting"


class _RaisingPrincipalClient:
    def principal(self):
        raise RuntimeError("dav connection failed")


def test_fetch_google_error_returns_empty(monkeypatch):
    import caldav

    monkeypatch.setattr(caldav, "DAVClient", lambda **kwargs: _RaisingPrincipalClient())
    record = SimpleNamespace(
        id=1,
        username="user",
        password="secret",
        label="G",
        url="https://dav.example",
        owner_email=None,
    )
    assert _fetch(fetch_google_caldav, record) == []


def test_fetch_apple_error_returns_empty(monkeypatch):
    import caldav

    monkeypatch.setattr(caldav, "DAVClient", lambda **kwargs: _RaisingPrincipalClient())
    record = SimpleNamespace(
        id=1,
        username="user",
        password="secret",
        label="A",
        url="https://dav.example",
        owner_email=None,
    )
    assert _fetch(fetch_apple_caldav, record) == []
