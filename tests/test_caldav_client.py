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


# --- Recurrence rules from the unexpanded masters ------------------------------

# A server-expanded instance: the RRULE is resolved away, and RECURRENCE-ID is
# what marks it as one of a series.
_ICS_EXPANDED_INSTANCE = (
    b"BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\n"
    b"UID:soccer-practice\r\nSUMMARY:Soccer practice\r\n"
    b"DTSTART:20260315T100000Z\r\nRECURRENCE-ID:20260315T100000Z\r\n"
    b"END:VEVENT\r\nEND:VCALENDAR"
)
# The same event unexpanded, as a second query returns it.
_ICS_MASTER = (
    b"BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\n"
    b"UID:soccer-practice\r\nSUMMARY:Soccer practice\r\n"
    b"DTSTART:20260315T100000Z\r\nRRULE:FREQ=WEEKLY;BYDAY=TU,TH\r\n"
    b"END:VEVENT\r\nEND:VCALENDAR"
)


class _ExpandAwareCalendar:
    """A server that answers expanded and unexpanded queries differently.

    Which is the whole point: `expand=True` is why the grid is fast, and it is
    also why the rule has to be asked for separately.
    """

    def __init__(self, expanded, masters, name="Cal"):
        self._expanded = expanded
        self._masters = masters
        self.name = name
        self.searches = []

    def search(self, **kwargs):
        self.searches.append(kwargs)
        return self._masters if kwargs.get("expand") is False else self._expanded


def test_an_expanded_instance_gets_its_rule_from_the_master():
    cal = _ExpandAwareCalendar([_FakeItem(_ICS_EXPANDED_INSTANCE)], [_FakeItem(_ICS_MASTER)])

    events = _parse(_FakeClient([cal]))

    assert len(events) == 1
    assert events[0].recurring is True
    assert events[0].rrule == "FREQ=WEEKLY;BYDAY=TU,TH"


def test_a_window_without_a_series_costs_no_second_request():
    """The masters are only worth fetching when something in the window repeats."""
    cal = _ExpandAwareCalendar([_FakeItem(_ICS)], [_FakeItem(_ICS_MASTER)])

    events = _parse(_FakeClient([cal]))

    assert [s.get("expand") for s in cal.searches] == [True]
    assert events[0].rrule is None
    assert events[0].recurring is False


class _NoUnexpandedSearchCalendar(_ExpandAwareCalendar):
    def search(self, **kwargs):
        if kwargs.get("expand") is False:
            raise RuntimeError("unexpanded search not supported")
        return self._expanded


def test_a_server_that_refuses_the_unexpanded_query_still_returns_events():
    """The degraded path is the one that predates this: repeats, schedule unknown.

    A rule Rally cannot fetch must never cost the calendar its events, and the
    occurrence must still say it repeats — omitting that claims a one-off.
    """
    cal = _NoUnexpandedSearchCalendar([_FakeItem(_ICS_EXPANDED_INSTANCE)], [])

    events = _parse(_FakeClient([cal]))

    assert len(events) == 1
    assert events[0].recurring is True
    assert events[0].rrule is None


def test_an_unparseable_master_does_not_lose_the_occurrence():
    cal = _ExpandAwareCalendar(
        [_FakeItem(_ICS_EXPANDED_INSTANCE)], [_FakeItem(b"not iCalendar data")]
    )

    events = _parse(_FakeClient([cal]))

    assert len(events) == 1
    assert events[0].rrule is None
    assert events[0].recurring is True


def test_rules_are_matched_by_uid_not_by_position():
    """All instances of a series share a UID; that is what joins the two answers."""
    other_instance = _ICS_EXPANDED_INSTANCE.replace(b"soccer-practice", b"band-rehearsal")
    other_master = _ICS_MASTER.replace(b"soccer-practice", b"band-rehearsal").replace(
        b"FREQ=WEEKLY;BYDAY=TU,TH", b"FREQ=MONTHLY;BYMONTHDAY=1"
    )
    cal = _ExpandAwareCalendar(
        [_FakeItem(other_instance), _FakeItem(_ICS_EXPANDED_INSTANCE)],
        [_FakeItem(_ICS_MASTER), _FakeItem(other_master)],
    )

    events = _parse(_FakeClient([cal]))

    by_uid = {e.uid: e.rrule for e in events}
    assert by_uid == {
        "soccer-practice": "FREQ=WEEKLY;BYDAY=TU,TH",
        "band-rehearsal": "FREQ=MONTHLY;BYMONTHDAY=1",
    }
