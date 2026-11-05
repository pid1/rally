"""The busy-only tools: free/busy and pending bookings for an agent that is not trusted.

``/api/busy/...`` is what such an agent reaches, on any connection given the tools. It is the security
boundary, so these tests pin both halves: that the answers are right (every
calendar counted, recurrences, all-day events, DST, the member filter), and
that nothing private ever leaves (merged, rounded blocks; capped, validated
windows; bookings that are pending, tagged with their connection and never
changed by the agent; rate limits; the audit trail).
"""

import hashlib
import json
import re
from datetime import UTC, datetime, timedelta

import pytest

from rally import connections
from rally.audit import identity
from rally.calendars import cache as calendar_cache
from rally.calendars.occurrence import Occurrence
from rally.models import (
    AuditEvent,
    Calendar,
    CalendarCache,
    Connection,
    Event,
    EventAttendee,
    EventOverride,
    FamilyMember,
    Setting,
)
from rally.routers import busy as busy_router

SERVICE_TOKEN = "test-service-token-0123456789"
JON = "jon@example.com"
TZ = "America/Chicago"
# Wednesday 28 October 2026, 7:00 AM in Chicago (CDT, -05:00). US DST ends on
# Sunday 1 November 2026.
NOW = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)


# The phone agent's connections, as a household member would set them up on
# the Connections page: token auth, the Busy-only preset, one allowed range.
PHONE_IP = "203.0.113.5"
PHONE_RANGE = "203.0.113.0/24"
BUSY_CONNECTIONS = ("conn-phone", "conn-other")


def token_for(connection):
    return f"rly_{connection}-token"


def mcp(connection="conn-phone", tool="check_availability", arguments=None):
    """Headers rally-mcp sends for a call on a busy-only token connection.

    These go through every Phase 1 check in the audit middleware: the
    connection exists and is enabled, the IP is on its allowlist, the token
    matches, the tool is one it may use and the route is in the tool's scope.
    """
    return {
        "X-Rally-Service-Token": SERVICE_TOKEN,
        "X-Rally-Mcp-Connection-Id": connection,
        "X-Rally-Mcp-Connection-Token": token_for(connection),
        "X-Rally-Mcp-Client": "elevenlabs",
        "X-Rally-Mcp-Client-Ip": PHONE_IP,
        "X-Rally-Mcp-Tool": tool,
        "X-Rally-Mcp-Arguments": json.dumps(arguments or {}),
    }


def add_busy_connection(db, member, connection):
    db.add(
        Connection(
            id=connection,
            name=f"Phone agent {connection}",
            auth_method="token",
            preset="busy",
            ip_allowlist=[PHONE_RANGE],
            status="enabled",
            authorized_by=member.id,
            token_hash=connections.hash_token(token_for(connection)),
            token_hint=token_for(connection)[:8],
        )
    )
    db.commit()


@pytest.fixture(autouse=True)
def _fresh_rate_limits():
    busy_router.READ_LIMIT.reset()
    busy_router.WRITE_LIMIT.reset()
    yield
    busy_router.READ_LIMIT.reset()
    busy_router.WRITE_LIMIT.reset()


@pytest.fixture
def family(db_session, make_member, make_native_calendar, monkeypatch, frozen_now, local_timezone):
    """MCP on, the family in Chicago, Jon and Emma each with a native calendar."""
    monkeypatch.setenv("RALLY_MCP_SERVICE_TOKEN", SERVICE_TOKEN)
    monkeypatch.setattr(identity, "whois", lambda addr: None)
    jon = make_member("Jon", email=JON)
    emma = make_member("Emma")
    db_session.add(Setting(key="mcp_enabled", value="true"))
    db_session.commit()
    local_timezone(TZ)
    frozen_now(NOW)
    for connection in BUSY_CONNECTIONS:
        add_busy_connection(db_session, jon, connection)

    class Family:
        pass

    f = Family()
    f.jon, f.emma = jon, emma
    f.jon_cal = make_native_calendar(owner=jon)
    f.emma_cal = make_native_calendar(owner=emma)
    return f


def availability(client, start, end, members=(), connection="conn-phone"):
    params = [("start", start), ("end", end)] + [("members", m) for m in members]
    return client.get("/api/busy/availability", params=params, headers=mcp(connection))


def blocks(response, key="busy"):
    assert response.status_code == 200, response.text
    return [(b["start"], b["end"]) for b in response.json()[key]]


DAY = ("2026-10-29T00:00:00-05:00", "2026-10-30T00:00:00-05:00")  # Thursday


def external_calendar(db, *, label="Work feed", owner=None, occurrences=(), cached=True):
    calendar = Calendar(
        label=label,
        url="https://example.invalid/private.ics",
        family_member_id=owner.id if owner else 0,
        cal_type="ics",
    )
    db.add(calendar)
    db.commit()
    if cached:
        db.add(
            CalendarCache(
                calendar_id=calendar.id,
                occurrences=[calendar_cache.occurrence_to_dict(o(calendar)) for o in occurrences],
                window_start="2026-09-01",
                window_end="2027-06-01",
            )
        )
        db.commit()
    return calendar


def feed_event(title, start, end, *, member=None, description="", location="", uid=None):
    def build(calendar):
        return Occurrence(
            uid=uid or f"{title}@feed",
            source="ics",
            title=title,
            start=start,
            end=end,
            start_local_date=start.date().isoformat(),
            end_local_date=end.date().isoformat(),
            description=description,
            location=location,
            calendar_id=calendar.id,
            calendar_label=calendar.label,
            member=member,
            attendees=(member,) if member else (),
        )

    return build


# --- Who may ask -----------------------------------------------------------------


def test_only_mcp_connections_may_call(client, family):
    response = client.get("/api/busy/availability", params={"start": DAY[0], "end": DAY[1]})
    assert response.status_code == 403


def test_the_routes_are_hand_written_mcp_tools():
    from rally.audit import mcp_policy
    from rally.main import app

    routes = {
        ("GET", "/api/busy/availability"),
        ("GET", "/api/busy/free-slots"),
        ("POST", "/api/busy/appointments"),
    }
    assert routes <= mcp_policy.HAND_WRITTEN
    assert routes <= mcp_policy.allowed_routes(app)
    generated = {(m, p) for m, p, _ in mcp_policy.generated_routes(app)}
    assert not routes & generated


# --- Correctness -------------------------------------------------------------------


def test_overlapping_and_touching_events_merge_and_round_outward(client, family, make_event):
    cal = family.jon_cal
    make_event("A", start="2026-10-29T09:00", end="2026-10-29T09:50", calendar=cal)
    make_event("B", start="2026-10-29T09:50", end="2026-10-29T10:20", calendar=cal)
    make_event("C", start="2026-10-29T10:40", end="2026-10-29T11:00", calendar=cal)
    make_event("D", start="2026-10-29T13:05", end="2026-10-29T13:20", calendar=cal)
    make_event("E", start="2026-10-29T13:10", end="2026-10-29T13:12", calendar=cal)

    response = availability(client, *DAY)
    assert blocks(response) == [
        ("2026-10-29T09:00:00-05:00", "2026-10-29T11:00:00-05:00"),
        ("2026-10-29T13:00:00-05:00", "2026-10-29T13:30:00-05:00"),
    ]
    assert blocks(response, "free") == [
        ("2026-10-29T00:00:00-05:00", "2026-10-29T09:00:00-05:00"),
        ("2026-10-29T11:00:00-05:00", "2026-10-29T13:00:00-05:00"),
        ("2026-10-29T13:30:00-05:00", "2026-10-30T00:00:00-05:00"),
    ]
    assert response.json()["timezone"] == TZ


def test_blocks_are_clipped_to_the_window(client, family, make_event):
    make_event("Long", start="2026-10-29T08:00", end="2026-10-29T12:00", calendar=family.jon_cal)
    response = availability(client, "2026-10-29T10:07:00-05:00", "2026-10-29T10:52:00-05:00")
    assert blocks(response) == [("2026-10-29T10:00:00-05:00", "2026-10-29T11:00:00-05:00")]
    assert blocks(response, "free") == []


def test_an_event_ending_at_the_window_start_does_not_count(client, family, make_event):
    make_event("Before", start="2026-10-29T08:00", end="2026-10-29T09:00", calendar=family.jon_cal)
    response = availability(client, "2026-10-29T09:00:00-05:00", "2026-10-29T10:00:00-05:00")
    assert blocks(response) == []


def test_inputs_in_any_offset_answer_in_the_familys_timezone(client, family, make_event):
    make_event("A", start="2026-10-29T09:00", end="2026-10-29T10:00", calendar=family.jon_cal)
    response = availability(client, "2026-10-29T13:00:00Z", "2026-10-29T16:00:00+00:00")
    assert blocks(response) == [("2026-10-29T09:00:00-05:00", "2026-10-29T10:00:00-05:00")]


def test_an_all_day_event_is_busy_for_the_whole_local_day(client, family, make_event):
    make_event("Away", start="2026-10-29", all_day=True, calendar=family.jon_cal)
    response = availability(client, "2026-10-28T12:00:00-05:00", "2026-10-30T12:00:00-05:00")
    assert blocks(response) == [("2026-10-29T00:00:00-05:00", "2026-10-30T00:00:00-05:00")]


def test_recurring_events_expand_and_cancelled_occurrences_are_free(
    client, family, make_event, db_session
):
    event = make_event(
        "Standup",
        start="2026-10-28T09:00",
        end="2026-10-28T09:30",
        rrule="FREQ=DAILY;COUNT=5",
        calendar=family.jon_cal,
    )
    db_session.add(EventOverride(event_id=event.id, occurrence_date="2026-10-29", cancelled=True))
    db_session.commit()

    response = availability(client, "2026-10-28T08:00:00-05:00", "2026-10-31T08:00:00-05:00")
    assert blocks(response) == [
        ("2026-10-28T09:00:00-05:00", "2026-10-28T09:30:00-05:00"),
        ("2026-10-30T09:00:00-05:00", "2026-10-30T09:30:00-05:00"),
    ]


def test_dst_boundary_keeps_local_wall_time_and_changes_offset(client, family, make_event):
    make_event(
        "School run",
        start="2026-10-31T08:00",
        end="2026-10-31T08:30",
        rrule="FREQ=DAILY;COUNT=3",
        calendar=family.jon_cal,
    )
    response = availability(client, "2026-10-31T00:00:00-05:00", "2026-11-03T00:00:00-06:00")
    assert blocks(response) == [
        ("2026-10-31T08:00:00-05:00", "2026-10-31T08:30:00-05:00"),
        ("2026-11-01T08:00:00-06:00", "2026-11-01T08:30:00-06:00"),
        ("2026-11-02T08:00:00-06:00", "2026-11-02T08:30:00-06:00"),
    ]
    # The fall-back night is 25 hours long and still one free block.
    free = blocks(response, "free")
    assert ("2026-10-31T08:30:00-05:00", "2026-11-01T08:00:00-06:00") in free


def test_members_filter_by_name(client, family, make_event):
    make_event("Jon's", start="2026-10-29T09:00", end="2026-10-29T10:00", calendar=family.jon_cal)
    make_event("Emma's", start="2026-10-29T14:00", end="2026-10-29T15:00", calendar=family.emma_cal)
    make_event(
        "Both",
        start="2026-10-29T17:00",
        end="2026-10-29T18:00",
        calendar=family.jon_cal,
        attendees=[family.jon, family.emma],
    )

    emma = blocks(availability(client, *DAY, members=["emma"]))
    assert emma == [
        ("2026-10-29T14:00:00-05:00", "2026-10-29T15:00:00-05:00"),
        ("2026-10-29T17:00:00-05:00", "2026-10-29T18:00:00-05:00"),
    ]
    jon = blocks(availability(client, *DAY, members=["Jon"]))
    assert jon == [
        ("2026-10-29T09:00:00-05:00", "2026-10-29T10:00:00-05:00"),
        ("2026-10-29T17:00:00-05:00", "2026-10-29T18:00:00-05:00"),
    ]
    assert len(blocks(availability(client, *DAY))) == 3


def test_explicit_attendees_replace_the_calendar_owner(client, family, make_event):
    make_event(
        "Emma on Jon's calendar",
        start="2026-10-29T09:00",
        end="2026-10-29T10:00",
        calendar=family.jon_cal,
        attendees=[family.emma],
    )
    assert blocks(availability(client, *DAY, members=["Jon"])) == []
    assert len(blocks(availability(client, *DAY, members=["Emma"]))) == 1


def test_an_unknown_member_is_refused_without_naming_anyone(client, family):
    response = availability(client, *DAY, members=["Mallory"])
    assert response.status_code == 422
    assert "Mallory" not in response.text
    assert "Jon" not in response.text and "Emma" not in response.text


def test_external_feed_events_count_from_the_cache(client, family, db_session):
    start = datetime(2026, 10, 29, 15, 0, tzinfo=UTC)  # 10:00 CDT
    external_calendar(
        db_session,
        owner=family.emma,
        occurrences=[feed_event("Dentist", start, start + timedelta(minutes=40), member="Emma")],
    )
    assert blocks(availability(client, *DAY)) == [
        ("2026-10-29T10:00:00-05:00", "2026-10-29T10:45:00-05:00")
    ]
    assert blocks(availability(client, *DAY, members=["Emma"])) != []
    assert blocks(availability(client, *DAY, members=["Jon"])) == []


def test_an_unowned_feed_blocks_everyone(client, family, db_session):
    start = datetime(2026, 10, 29, 15, 0, tzinfo=UTC)
    external_calendar(
        db_session, occurrences=[feed_event("Shared", start, start + timedelta(hours=1))]
    )
    assert blocks(availability(client, *DAY, members=["Jon"])) == [
        ("2026-10-29T10:00:00-05:00", "2026-10-29T11:00:00-05:00")
    ]


def test_a_never_synced_feed_fails_closed(client, family, db_session):
    external_calendar(db_session, label="Never synced", cached=False)
    response = availability(client, *DAY)
    assert response.status_code == 503
    assert "Never synced" not in response.text


def test_a_failing_feed_still_counts_its_last_good_data(client, family, db_session):
    start = datetime(2026, 10, 29, 15, 0, tzinfo=UTC)
    cal = external_calendar(
        db_session, occurrences=[feed_event("Old", start, start + timedelta(hours=1))]
    )
    row = db_session.query(CalendarCache).filter_by(calendar_id=cal.id).one()
    row.last_error = "HTTP 500"
    db_session.commit()
    assert len(blocks(availability(client, *DAY))) == 1


def test_availability_never_touches_the_network(client, family, db_session, monkeypatch):
    import socket

    def no_network(*args, **kwargs):
        raise AssertionError("the busy tools reached the network")

    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.setattr(socket, "getaddrinfo", no_network)
    start = datetime(2026, 10, 29, 15, 0, tzinfo=UTC)
    external_calendar(db_session, occurrences=[feed_event("X", start, start + timedelta(hours=1))])
    assert availability(client, *DAY).status_code == 200


# --- Validation ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "start, end, fragment",
    [
        ("2026-10-29T10:00:00", "2026-10-29T11:00:00-05:00", "UTC offset"),
        ("2026-10-29", "2026-10-30", "UTC offset"),
        ("tomorrow", "2026-10-29T11:00:00-05:00", "UTC offset"),
        ("2026-10-29T11:00:00-05:00", "2026-10-29T10:00:00-05:00", "after the start"),
        ("2026-10-29T11:00:00-05:00", "2026-10-29T11:00:00-05:00", "after the start"),
        ("2026-10-29T00:00:00-05:00", "2026-12-28T00:00:01-06:00", "60 days"),
        ("2026-10-20T00:00:00-05:00", "2026-10-21T00:00:00-05:00", "past"),
        ("2027-04-20T00:00:00-05:00", "2027-05-01T00:00:00-05:00", "180 days"),
    ],
)
def test_bad_windows_are_refused(client, family, start, end, fragment):
    response = availability(client, start, end)
    assert response.status_code == 422
    assert fragment in response.json()["detail"]


def test_a_sixty_day_window_is_allowed(client, family):
    # Sixty days to the hour: the window crosses the end of DST.
    response = availability(client, "2026-10-29T00:00:00-05:00", "2026-12-27T23:00:00-06:00")
    assert response.status_code == 200


# --- Free slots ----------------------------------------------------------------------


def slots(client, connection="conn-phone", **params):
    query = {"window_start": DAY[0], "window_end": DAY[1], "duration_minutes": 60, **params}
    members = query.pop("members", [])
    pairs = list(query.items()) + [("members", m) for m in members]
    response = client.get("/api/busy/free-slots", params=pairs, headers=mcp(connection))
    return response


def slot_list(response):
    assert response.status_code == 200, response.text
    return [(s["start"][11:16], s["end"][11:16]) for s in response.json()["slots"]]


def test_free_slots_fill_gaps_within_working_hours(client, family, make_event):
    make_event("A", start="2026-10-29T09:10", end="2026-10-29T10:00", calendar=family.jon_cal)
    make_event("B", start="2026-10-29T11:30", end="2026-10-29T16:00", calendar=family.jon_cal)
    assert slot_list(slots(client, max_results=10)) == [
        ("08:00", "09:00"),
        ("10:00", "11:00"),
        ("16:00", "17:00"),
        ("17:00", "18:00"),
    ]


def test_free_slots_constraints(client, family, make_event):
    make_event("A", start="2026-10-29T10:00", end="2026-10-29T11:00", calendar=family.jon_cal)
    got = slot_list(
        slots(
            client,
            duration_minutes=30,
            earliest_time="09:00",
            latest_time="12:00",
            buffer_minutes=20,
            max_results=10,
        )
    )
    # 20 minutes either side of 10-11, rounded outward: 09:30-11:30 is kept clear.
    assert got == [("09:00", "09:30"), ("11:30", "12:00")]


def test_free_slots_never_offer_the_past(client, family):
    # It is 07:00 on Wednesday; ask for Wednesday starting at midnight.
    got = slot_list(
        slots(
            client,
            window_start="2026-10-28T00:00:00-05:00",
            window_end="2026-10-29T00:00:00-05:00",
            earliest_time="06:00",
            max_results=1,
        )
    )
    assert got == [("07:00", "08:00")]


def test_free_slots_weekdays_only_skips_the_weekend(client, family):
    response = slots(
        client,
        window_start="2026-10-31T00:00:00-05:00",  # Saturday
        window_end="2026-11-03T00:00:00-06:00",
        weekdays_only="true",
        max_results=1,
    )
    assert response.json()["slots"][0]["start"] == "2026-11-02T08:00:00-06:00"


def test_free_slots_respect_the_members_filter(client, family, make_event):
    make_event(
        "Emma busy", start="2026-10-29T08:00", end="2026-10-29T18:00", calendar=family.emma_cal
    )
    assert slot_list(slots(client, members=["Emma"])) == []
    assert slot_list(slots(client, members=["Jon"], max_results=1)) == [("08:00", "09:00")]


@pytest.mark.parametrize(
    "params",
    [
        {"duration_minutes": 5},
        {"duration_minutes": 9999},
        {"buffer_minutes": 500},
        {"max_results": 50},
        {"earliest_time": "9am"},
        {"earliest_time": "25:00"},
        {"earliest_time": "12:00", "latest_time": "09:00"},
    ],
)
def test_free_slots_validation(client, family, params):
    assert slots(client, **params).status_code == 422


# --- Booking ---------------------------------------------------------------------------


def book(client, connection="conn-phone", **body):
    body = {
        "start": "2026-10-29T14:00:00-05:00",
        "end": "2026-10-29T15:00:00-05:00",
        "title": "Furnace inspection",
        **body,
    }
    return client.post(
        "/api/busy/appointments", json=body, headers=mcp(connection, "book_appointment", body)
    )


def booked(db):
    db.expire_all()
    return db.query(Event).filter(Event.source.isnot(None)).all()


def test_booking_creates_a_pending_tagged_event_and_returns_only_an_id(client, family, db_session):
    response = book(
        client,
        contact_name="Pat Jones",
        contact_phone="+1 (800) 555-0142",
        notes="Side door.\nDog is friendly.",
        members=["Emma"],
    )
    assert response.status_code == 201, response.text
    assert set(response.json()) == {"confirmation_id"}
    confirmation = response.json()["confirmation_id"]
    assert re.fullmatch(r"[2-9A-HJKMNP-Z]{4}-[2-9A-HJKMNP-Z]{4}", confirmation)

    [event] = booked(db_session)
    assert event.source == "conn-phone"
    assert event.pending_confirmation is True
    assert event.title == "Furnace inspection"
    assert event.description == "Side door.\nDog is friendly."
    assert event.contact_name == "Pat Jones"
    assert event.contact_phone == "+1 (800) 555-0142"
    assert event.tzid == TZ
    assert event.start_date == "2026-10-29"
    assert event.start_utc == datetime(2026, 10, 29, 19, 0)
    attendees = db_session.query(EventAttendee).filter_by(event_id=event.id).all()
    assert [a.family_member_id for a in attendees] == [family.emma.id]


def test_the_confirmation_id_is_opaque(client, family, db_session):
    first = book(client).json()["confirmation_id"]
    second = book(
        client, start="2026-10-29T16:00:00-05:00", end="2026-10-29T17:00:00-05:00"
    ).json()["confirmation_id"]
    assert first != second
    hashes = {hashlib.sha256(c.replace("-", "").encode()).hexdigest() for c in (first, second)}
    events = booked(db_session)
    assert {e.confirmation_hash for e in events} == hashes
    for event in events:
        for confirmation in (first, second):
            raw = confirmation.replace("-", "")
            assert raw != str(event.id)
            assert raw not in event.uid.upper()
            # Only the hash is stored, never the id itself.
            assert raw not in json.dumps(
                {c.name: str(getattr(event, c.name)) for c in Event.__table__.columns}
            )


def test_booking_into_busy_time_is_refused(client, family, make_event, db_session):
    make_event(
        "Private thing", start="2026-10-29T14:50", end="2026-10-29T15:30", calendar=family.jon_cal
    )
    response = book(client)
    assert response.status_code == 409
    assert "Private" not in response.text
    assert booked(db_session) == []


def test_rounding_applies_to_conflicts_too(client, family, make_event, db_session):
    # Ends 13:05, which reads as busy until 13:15.
    make_event("X", start="2026-10-29T12:00", end="2026-10-29T13:05", calendar=family.jon_cal)
    assert book(client, start="2026-10-29T13:10:00-05:00").status_code == 409
    assert book(client, start="2026-10-29T13:15:00-05:00").status_code == 201


def test_a_booking_blocks_the_next_one(client, family):
    assert book(client).status_code == 201
    assert book(client, connection="conn-other").status_code == 409
    response = availability(client, *DAY)
    assert blocks(response) == [("2026-10-29T14:00:00-05:00", "2026-10-29T15:00:00-05:00")]


def test_booking_checks_only_the_named_members(client, family, make_event):
    make_event(
        "Jon away", start="2026-10-29T13:00", end="2026-10-29T16:00", calendar=family.jon_cal
    )
    assert book(client, members=["Jon"]).status_code == 409
    assert book(client, members=["Emma"]).status_code == 201


@pytest.mark.parametrize(
    "body, status",
    [
        ({"title": ""}, 422),
        ({"title": "   "}, 422),
        ({"title": "x" * 101}, 422),
        ({"notes": "x" * 1001}, 422),
        ({"contact_name": "x" * 101}, 422),
        ({"contact_phone": "call me maybe"}, 422),
        ({"start": "2026-10-29T14:00:00"}, 422),
        ({"start": "2026-10-27T14:00:00-05:00", "end": "2026-10-27T15:00:00-05:00"}, 422),
        ({"end": "2026-10-29T13:00:00-05:00"}, 422),
        ({"end": "2026-10-29T23:00:00-05:00"}, 422),
        ({"start": "2027-06-01T14:00:00-05:00", "end": "2027-06-01T15:00:00-05:00"}, 422),
        ({"members": ["Mallory"]}, 422),
        ({"event_id": 1}, 422),
        ({"pending_confirmation": False}, 422),
    ],
)
def test_booking_validation(client, family, db_session, body, status):
    assert book(client, **body).status_code == status
    assert booked(db_session) == []


def test_booking_text_is_cleaned_of_control_characters(client, family, db_session):
    assert book(client, title="Plumber\x00‮  visit\r\n").status_code == 201
    [event] = booked(db_session)
    assert event.title == "Plumber visit"


def test_a_booked_event_shows_as_pending_and_phone_booked(client, family, db_session):
    book(client, contact_name="Pat")
    [occurrence] = client.get("/api/events?start=2026-10-29&end=2026-10-30").json()["occurrences"]
    assert occurrence["booked_by_connection"] is True
    assert occurrence["pending_confirmation"] is True
    assert occurrence["contact_name"] == "Pat"
    event_id = occurrence["event_id"]
    assert client.get(f"/api/events/{event_id}").json()["pending_confirmation"] is True


def test_the_family_confirms_a_booking(client, family, db_session):
    book(client)
    [event] = booked(db_session)
    response = client.post(f"/api/events/{event.id}/confirm")
    assert response.status_code == 200
    assert response.json()["pending_confirmation"] is False
    assert response.json()["booked_by_connection"] is True
    [occurrence] = client.get("/api/events?start=2026-10-29&end=2026-10-30").json()["occurrences"]
    assert occurrence["pending_confirmation"] is False
    [update] = [
        e
        for e in db_session.query(AuditEvent).filter_by(action="event.update").all()
        if e.entity_id == str(event.id)
    ]
    assert update.after["pending_confirmation"] is False
    assert update.before["pending_confirmation"] is True


def test_ordinary_events_are_not_phone_booked(client, family, make_event):
    make_event("Mine", start="2026-10-29T09:00", calendar=family.jon_cal)
    [occurrence] = client.get("/api/events?start=2026-10-29&end=2026-10-30").json()["occurrences"]
    assert occurrence["booked_by_connection"] is False
    assert occurrence["pending_confirmation"] is False


# --- The agent changes nothing it booked -------------------------------------------------


def test_there_is_no_way_for_the_agent_to_cancel(client, family, db_session):
    confirmation = book(client).json()["confirmation_id"]
    body = {"confirmation_id": confirmation}
    for tool in ("book_appointment", "check_availability"):
        response = client.post(
            "/api/busy/appointments/cancel", json=body, headers=mcp("conn-phone", tool, body)
        )
        assert response.status_code in (403, 404, 405), response.text
    [event] = booked(db_session)
    assert event.pending_confirmation is True


# --- Rate limits ---------------------------------------------------------------------------


def test_reads_are_rate_limited_per_connection(client, family):
    for _ in range(busy_router.READ_LIMIT.capacity):
        assert availability(client, *DAY).status_code == 200
    limited = availability(client, *DAY)
    assert limited.status_code == 429
    assert availability(client, *DAY, connection="conn-other").status_code == 200


def test_writes_are_rate_limited_per_connection(client, family):
    # The first books the slot and the rest find it taken; each still counts.
    for _ in range(busy_router.WRITE_LIMIT.capacity):
        assert book(client).status_code in (201, 409)
    assert book(client).status_code == 429
    assert book(client, connection="conn-other").status_code == 409


def test_the_bucket_refills(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(busy_router.monotonic_time, "monotonic", lambda: clock[0])
    bucket = busy_router.TokenBucket(2, 60)
    assert bucket.take("a") and bucket.take("a")
    assert not bucket.take("a")
    clock[0] += 30
    assert bucket.take("a")
    assert not bucket.take("a")


# --- Audit -------------------------------------------------------------------------------------


def audit(db, **filters):
    db.expire_all()
    return db.query(AuditEvent).filter_by(**filters).order_by(AuditEvent.occurred_at).all()


def test_availability_is_audited_with_tool_arguments_and_result_size(
    client, family, make_event, db_session
):
    make_event("A", start="2026-10-29T09:00", calendar=family.jon_cal)
    response = availability(client, *DAY)
    rid = response.headers["x-request-id"]
    [event] = audit(db_session, action="busy.check_availability", request_id=rid)
    assert event.source == "mcp"
    assert event.actor_type == "mcp_client"
    assert event.mcp_tool == "check_availability"
    assert event.client_info["connection_id"] == "conn-phone"
    assert event.after["result_size"] == 3
    assert event.after["busy_blocks"] == 1
    [req] = audit(db_session, action="http.request", request_id=rid)
    assert req.outcome == "success"


def test_free_slots_are_audited(client, family, db_session):
    response = slots(client, max_results=3)
    [event] = audit(
        db_session, action="busy.find_free_slots", request_id=response.headers["x-request-id"]
    )
    assert event.after["result_size"] == 3


def test_booking_is_audited(client, family, db_session):
    response = book(client, notes="hello")
    rid = response.headers["x-request-id"]
    [create] = audit(db_session, action="event.create", request_id=rid)
    assert create.after["source"] == "conn-phone"
    assert create.after["pending_confirmation"] is True
    [booked_event] = audit(db_session, action="busy.book_appointment", request_id=rid)
    assert booked_event.mcp_tool == "book_appointment"
    assert booked_event.mcp_arguments["notes"] == "hello"


def test_refusals_are_audited(client, family, db_session):
    assert book(client).status_code == 201
    response = book(client, connection="conn-other")
    assert response.status_code == 409
    [req] = audit(db_session, action="http.request", request_id=response.headers["x-request-id"])
    assert req.outcome == "failure"
    assert req.after["status"] == 409


# --- Nothing private leaks -------------------------------------------------------------------

SECRET = "S3CR3T"


def test_no_private_text_appears_in_any_response_or_error(
    client, family, make_event, db_session, monkeypatch
):
    """Seed a distinctive marker in every text field Rally holds, then try everything."""
    secret_member = FamilyMember(name=f"Zed-{SECRET}-attendee")
    db_session.add(secret_member)
    db_session.commit()
    secret_cal = Calendar(
        label=f"{SECRET} calendar label",
        url="",
        family_member_id=family.jon.id,
        cal_type="native",
    )
    db_session.add(secret_cal)
    db_session.commit()
    event = make_event(
        f"{SECRET} title",
        start="2026-10-29T14:00",
        end="2026-10-29T15:00",
        calendar=secret_cal,
        description=f"{SECRET} description",
        location=f"{SECRET} location",
        attendees=[secret_member, family.jon],
        rrule="FREQ=DAILY;COUNT=3",
    )
    event.uid = f"{SECRET}-uid@example"
    db_session.add(
        EventOverride(
            event_id=event.id,
            occurrence_date="2026-10-30",
            title=f"{SECRET} override title",
            description=f"{SECRET} override description",
            location=f"{SECRET} override location",
        )
    )
    db_session.commit()
    start = datetime(2026, 10, 29, 21, 0, tzinfo=UTC)
    external_calendar(
        db_session,
        label=f"{SECRET} feed label",
        owner=family.emma,
        occurrences=[
            feed_event(
                f"{SECRET} feed title",
                start,
                start + timedelta(hours=1),
                member="Emma",
                description=f"{SECRET} feed description",
                location=f"{SECRET} feed location",
                uid=f"{SECRET}-feed-uid",
            )
        ],
    )
    external_calendar(db_session, label=f"{SECRET} unsynced label", cached=False)

    responses = []

    def call(method, path, **kwargs):
        busy_router.READ_LIMIT.reset()
        busy_router.WRITE_LIMIT.reset()
        responses.append(client.request(method, path, headers=mcp(), **kwargs))
        return responses[-1]

    week = {"start": "2026-10-28T00:00:00-05:00", "end": "2026-11-04T00:00:00-06:00"}
    # With the unsynced feed present, every read fails closed...
    assert call("GET", "/api/busy/availability", params=week).status_code == 503
    # ...so give it a cache row (with a failing sync) and ask everything again.
    unsynced = db_session.query(Calendar).filter(Calendar.label.like("%unsynced%")).one()
    db_session.add(
        CalendarCache(
            calendar_id=unsynced.id,
            occurrences=[],
            window_start="2026-09-01",
            window_end="2027-06-01",
            last_error=f"{SECRET} sync error",
        )
    )
    db_session.commit()

    ok = call("GET", "/api/busy/availability", params=week)
    assert ok.status_code == 200 and ok.json()["busy"]
    call("GET", "/api/busy/availability", params={**week, "members": "Emma"})
    call("GET", "/api/busy/availability", params={**week, "members": f"Zed-{SECRET}-attendee"})
    call("GET", "/api/busy/availability", params={**week, "members": "Nobody"})
    call("GET", "/api/busy/availability", params={"start": "bad", "end": "worse"})
    call("GET", "/api/busy/availability", params={"start": week["start"]})
    call(
        "GET",
        "/api/busy/free-slots",
        params={"window_start": week["start"], "window_end": week["end"], "duration_minutes": 30},
    )
    call("GET", "/api/busy/free-slots", params={"window_start": "x", "window_end": "y"})
    conflict = call(
        "POST",
        "/api/busy/appointments",
        json={
            "start": "2026-10-29T14:30:00-05:00",
            "end": "2026-10-29T15:30:00-05:00",
            "title": "Overlap",
        },
    )
    assert conflict.status_code == 409
    call("POST", "/api/busy/appointments", json={"title": "x"})
    call("POST", "/api/busy/appointments", json={"start": 1, "end": [], "title": None})
    # Rate-limited answers too.
    for _ in range(busy_router.READ_LIMIT.capacity + 1):
        responses.append(client.get("/api/busy/availability", params=week, headers=mcp()))
    assert responses[-1].status_code == 429

    # Positive control: the family's own calendar view does carry the markers,
    # so their absence above is the busy routes' doing, not a seeding mistake.
    family_view = client.get("/api/events?start=2026-10-28&end=2026-11-04").text
    for field in ("title", "description", "location", "attendee", "calendar label", "feed title"):
        assert f"{SECRET} {field}" in family_view or f"{SECRET}-{field}" in family_view, field

    for response in responses:
        assert SECRET not in response.text, (response.request.url, response.text)
        assert SECRET.lower() not in response.text.lower()
        for value in response.headers.values():
            assert SECRET not in value


# --- End to end with Phase 1's connection checks -------------------------------------------

CLAUDE_IP = "160.79.104.10"


def test_busy_only_token_connection_end_to_end(client, family, db_session, mock_pushover):
    """A busy-only token connection gets the three busy tools and nothing else.

    Every call below goes through the real audit middleware checks: the
    connection, its IP allowlist, its token, the tool's permission and the
    route's scope. Each refusal is a ``mcp.permission_denied`` event and a
    push to the alert list. A full-access connection on the same Rally is
    unaffected.
    """
    family.jon.pushover_user_key = "u-alert-test"
    db_session.add_all(
        [
            Setting(key="pushover_app_token", value="app-token-test"),
            Setting(key="audit_alert_member_ids", value=json.dumps([family.jon.id])),
            Connection(
                id="c-claude",
                name="Claude",
                auth_method="oauth",
                preset="full",
                ip_allowlist=["160.79.104.0/21"],
                status="enabled",
            ),
        ]
    )
    db_session.commit()

    # The three busy tools, each on its own route.
    assert availability(client, *DAY).status_code == 200
    assert slots(client).status_code == 200
    booking = book(client)
    assert booking.status_code == 201

    def denied_count():
        return len(audit(db_session, action="mcp.permission_denied"))

    refusals = [
        # Another tool, even on a busy route.
        ("GET", "/api/busy/availability?start=x&end=y", "list_events"),
        ("GET", "/api/events?start=2026-10-29&end=2026-10-30", "list_events"),
        ("POST", "/api/todos", "create_todo"),
        # A busy tool on a route outside its scope.
        ("GET", "/api/events?start=2026-10-29&end=2026-10-30", "check_availability"),
        ("POST", "/api/events", "book_appointment"),
        ("GET", "/api/family", "check_availability"),
        ("DELETE", "/api/events/1", "book_appointment"),
    ]
    for method, path, tool in refusals:
        before, pushed = denied_count(), len(mock_pushover.sent)
        response = client.request(
            method, path, headers=mcp(tool=tool), json={} if method == "POST" else None
        )
        assert response.status_code == 403, (method, path, tool, response.text)
        assert denied_count() == before + 1, (method, path, tool)
        assert len(mock_pushover.sent) == pushed + 1, (method, path, tool)
        assert "Phone agent conn-phone" in mock_pushover.sent[-1]["message"]
    [event] = audit(db_session, action="mcp.permission_denied")[-1:]
    assert event.outcome == "denied"
    assert event.client_info["connection_id"] == "conn-phone"

    # A full-access connection still reaches the calendar and the busy tools.
    full = {
        "X-Rally-Service-Token": SERVICE_TOKEN,
        "X-Rally-Mcp-Connection-Id": "c-claude",
        "X-Rally-Mcp-Grant-Id": "conn_grant-test",
        "X-Rally-Mcp-Email": JON,
        "X-Rally-Mcp-Client": "claude.ai",
        "X-Rally-Mcp-Client-Ip": CLAUDE_IP,
    }
    response = client.get(
        "/api/events?start=2026-10-29&end=2026-10-30",
        headers={**full, "X-Rally-Mcp-Tool": "list_events"},
    )
    assert response.status_code == 200, response.text
    response = client.get(
        "/api/busy/availability",
        params={"start": DAY[0], "end": DAY[1]},
        headers={**full, "X-Rally-Mcp-Tool": "check_availability"},
    )
    assert response.status_code == 200, response.text
