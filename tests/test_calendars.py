"""Tests for the normalized calendar layer.

Three things are under test here, and the first two are the reason the layer
exists at all:

1. **The four defects the old dict-based read path made unavoidable.** Each has
   a test named after it, and each fails against the code this replaced.
2. **Timezone and DST behavior**, which is the whole of calendaring wearing a
   different hat. Every case below produces a silently wrong answer rather than
   an error when it is got wrong.
3. Recurrence expansion and per-occurrence overrides.
"""

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from rally.calendars import (
    Occurrence,
    RecurrenceError,
    dates_covered,
    expand_event,
    local_midnight_utc,
    merge_occurrences,
    resolve_local,
    series_end_date,
    validate_rrule,
)
from rally.calendars.ics import occurrences_from_ical_text
from rally.calendars.inputs import EventTimeError, resolve_event_times
from rally.models import EventOverride

CHICAGO = ZoneInfo("America/Chicago")
UTC_TZ = ZoneInfo("UTC")


def _ics(*events: str) -> str:
    body = "".join(events)
    return f"BEGIN:VCALENDAR\r\nVERSION:2.0\r\n{body}END:VCALENDAR\r\n"


def _vevent(summary, dtstart, dtend=None, uid=None, extra=""):
    lines = [
        "BEGIN:VEVENT",
        f"UID:{uid or summary.lower().replace(' ', '-')}",
        f"SUMMARY:{summary}",
        f"DTSTART:{dtstart}" if "VALUE=" not in dtstart else f"DTSTART;{dtstart}",
    ]
    if dtend:
        lines.append(f"DTEND:{dtend}" if "VALUE=" not in dtend else f"DTEND;{dtend}")
    if extra:
        lines.append(extra.rstrip("\r\n"))
    lines.append("END:VEVENT")
    return "\r\n".join(lines) + "\r\n"


def _parse(text, *, start="2026-08-01", end="2026-09-01", tz=CHICAGO, **kwargs):
    from rally.calendars.sources import window_bounds

    window_start, window_end = window_bounds(
        date.fromisoformat(start), date.fromisoformat(end), tz
    )
    return occurrences_from_ical_text(
        text, window_start=window_start, window_end=window_end, local_tz=tz, **kwargs
    )


# --- The four defects ----------------------------------------------------------


def test_occurrences_sort_by_instant_not_by_clock_string():
    """Defect 1. The old key sorted "9:00 AM" after "10:00 AM" and "1:00 PM"."""
    text = _ics(
        _vevent("Afternoon", "20260811T180000Z"),  # 1:00 PM local
        _vevent("Late morning", "20260811T150000Z"),  # 10:00 AM local
        _vevent("Early", "20260811T140000Z"),  # 9:00 AM local
    )
    titles = [o.title for o in merge_occurrences([_parse(text)])]
    assert titles == ["Early", "Late morning", "Afternoon"]


def test_all_day_events_are_flagged_not_formatted_as_midnight():
    """Defect 2. A ``date`` has ``strftime``, so the old code rendered 12:00 AM."""
    text = _ics(_vevent("Emma's birthday", "VALUE=DATE:20260814"))
    occurrence = _parse(text)[0]

    assert occurrence.all_day is True
    assert occurrence.time_label(CHICAGO) == "All day"
    assert occurrence.start_local_date == "2026-08-14"


def test_two_same_named_events_on_one_day_both_survive():
    """Defect 3. The old ``(date, title)`` key dropped the second one."""
    text = _ics(
        _vevent("Soccer practice", "20260815T140000Z", uid="a"),
        _vevent("Soccer practice", "20260815T190000Z", uid="b"),
    )
    merged = merge_occurrences([_parse(text)])
    assert len(merged) == 2
    assert [o.time_label(CHICAGO) for o in merged] == ["9:00 AM", "2:00 PM"]


def test_window_is_measured_in_local_dates():
    """Defect 4. A UTC-dated window rolls over five hours early in Chicago.

    An 11:00 PM local event on the last day of the window is 04:00Z the *next*
    day, which a UTC-dated window excludes.
    """
    text = _ics(_vevent("Late", "20260901T040000Z"))  # 11:00 PM local, Aug 31
    assert [
        o.start_local_date for o in _parse(text, start="2026-08-25", end="2026-09-01")
    ] == ["2026-08-31"]


# --- Timezones and DST ---------------------------------------------------------


def test_nonexistent_local_time_shifts_forward_past_the_gap():
    """2:30 AM does not exist on the US spring-forward date."""
    resolved = resolve_local(datetime(2026, 3, 8, 2, 30), CHICAGO)
    assert resolved.strftime("%Y-%m-%d %H:%M %Z") == "2026-03-08 03:30 CDT"


def test_ambiguous_local_time_takes_the_first_instant():
    """1:30 AM happens twice on the fall-back date; the earlier one wins."""
    resolved = resolve_local(datetime(2026, 11, 1, 1, 30), CHICAGO)
    assert resolved.utcoffset() == timedelta(hours=-5)  # CDT, pre-transition


def test_all_day_anchors_to_local_midnight_not_utc_midnight():
    """Anchoring at 00:00Z puts a birthday on the previous day in Chicago."""
    assert local_midnight_utc(date(2026, 8, 14), CHICAGO) == datetime(
        2026, 8, 14, 5, 0, tzinfo=UTC
    )


def test_weekly_event_keeps_its_local_time_across_a_dst_transition(make_event):
    """The Tuesday 7 PM meeting must not become 6 PM in November."""
    event = make_event(
        "Scouts",
        start="2026-10-20T19:00",
        end="2026-10-20T20:00",
        rrule="FREQ=WEEKLY;BYDAY=TU;COUNT=5",
    )
    occurrences = expand_event(
        event,
        overrides=[],
        window_start=datetime(2026, 10, 1, tzinfo=UTC),
        window_end=datetime(2026, 12, 1, tzinfo=UTC),
        local_tz=CHICAGO,
    )

    local_times = {
        o.start.astimezone(CHICAGO).strftime("%I:%M %p") for o in occurrences
    }
    assert local_times == {"07:00 PM"}
    # And the UTC instant really does move, which is what proves the wall time
    # was preserved rather than the offset.
    assert {o.start.strftime("%H:%M") for o in occurrences} == {"00:00", "01:00"}


def test_changing_the_family_timezone_does_not_move_an_event(make_event):
    """``tzid`` is captured per event, so the view converts but the event does not."""
    event = make_event("Dentist", start="2026-08-11T09:00", tzid="America/Chicago")
    original = event.start_utc

    occurrences = expand_event(
        event,
        overrides=[],
        window_start=datetime(2026, 8, 1, tzinfo=UTC),
        window_end=datetime(2026, 9, 1, tzinfo=UTC),
        local_tz=ZoneInfo("America/New_York"),  # The family moved
    )
    assert event.start_utc == original
    assert occurrences[0].start == original.replace(tzinfo=UTC)
    # Same instant, shown an hour later on the new wall clock.
    assert (
        occurrences[0].start.astimezone(ZoneInfo("America/New_York")).strftime("%H:%M")
        == "10:00"
    )


def test_all_day_end_date_is_inclusive_while_the_instant_is_exclusive():
    """The classic off-by-one, pinned in both directions."""
    times = resolve_event_times(
        start="2026-08-14", end="2026-08-14", all_day=True, tzid="America/Chicago"
    )
    assert times["end_date"] == "2026-08-14"  # Inclusive: what a human means
    assert times["end_utc"] == datetime(2026, 8, 15, 5, 0)  # Exclusive: what ICS means


def test_multi_day_event_covers_every_day_it_spans():
    occurrence = Occurrence(
        uid="camping",
        source="native",
        title="Camping trip",
        start=datetime(2026, 8, 14, 5, tzinfo=UTC),
        end=datetime(2026, 8, 17, 5, tzinfo=UTC),
        start_local_date="2026-08-14",
        end_local_date="2026-08-16",
        all_day=True,
    )
    assert dates_covered(occurrence) == ["2026-08-14", "2026-08-15", "2026-08-16"]
    assert occurrence.spans_days() is True


def test_event_ending_at_local_midnight_belongs_to_the_day_it_started():
    """A 10 PM–midnight event is a Friday event, not a Friday-and-Saturday one."""
    text = _ics(_vevent("Late show", "20260815T030000Z", "20260815T050000Z"))
    occurrence = _parse(text)[0]
    assert occurrence.start_local_date == "2026-08-14"
    assert occurrence.end_local_date == "2026-08-14"


# --- Merge and dedupe ----------------------------------------------------------


def _occurrence(title, start, *, uid="", member=None, source="ics", **kwargs):
    return Occurrence(
        uid=uid,
        source=source,
        title=title,
        start=start,
        end=start + timedelta(hours=1),
        start_local_date=start.astimezone(CHICAGO).date().isoformat(),
        end_local_date=start.astimezone(CHICAGO).date().isoformat(),
        member=member,
        attendees=(member,) if member else (),
        **kwargs,
    )


def test_one_event_on_two_feeds_becomes_one_row_with_both_attendees():
    start = datetime(2026, 8, 15, 14, tzinfo=UTC)
    merged = merge_occurrences(
        [
            [_occurrence("Recital", start, uid="shared", member="Mom")],
            [_occurrence("Recital", start, uid="shared", member="Dad")],
        ]
    )
    assert len(merged) == 1
    assert set(merged[0].attendees) == {"Mom", "Dad"}


def test_native_wins_when_the_same_event_arrives_from_a_feed():
    """A native event exported to Google and read back must stay editable."""
    start = datetime(2026, 8, 15, 14, tzinfo=UTC)
    merged = merge_occurrences(
        [
            [_occurrence("Dentist", start, uid="rally-1@rally.local", source="ics")],
            [
                _occurrence(
                    "Dentist",
                    start,
                    uid="rally-1@rally.local",
                    source="native",
                    editable=True,
                )
            ],
        ]
    )
    assert len(merged) == 1
    assert merged[0].source == "native"
    assert merged[0].editable is True


def test_events_without_uids_dedupe_on_title_and_instants():
    start = datetime(2026, 8, 15, 14, tzinfo=UTC)
    merged = merge_occurrences(
        [[_occurrence("Recital", start)], [_occurrence("recital ", start)]]
    )
    assert len(merged) == 1


def test_all_day_events_sort_before_timed_events_on_the_same_day():
    day_start = datetime(2026, 8, 14, 5, tzinfo=UTC)
    timed = _occurrence("Standup", datetime(2026, 8, 14, 14, 30, tzinfo=UTC), uid="s")
    all_day = Occurrence(
        uid="b",
        source="ics",
        title="Birthday",
        start=day_start,
        end=day_start + timedelta(days=1),
        start_local_date="2026-08-14",
        end_local_date="2026-08-14",
        all_day=True,
    )
    assert [o.title for o in merge_occurrences([[timed, all_day]])] == [
        "Birthday",
        "Standup",
    ]


# --- Declined events -----------------------------------------------------------


def test_cancelled_events_are_dropped():
    text = _ics(_vevent("Dead", "20260811T140000Z", extra="STATUS:CANCELLED"))
    assert _parse(text) == []


# --- Recurrence ----------------------------------------------------------------


def test_validate_rrule_accepts_a_prefixed_rule():
    assert validate_rrule("RRULE:FREQ=WEEKLY;BYDAY=TU") == "FREQ=WEEKLY;BYDAY=TU"


def test_validate_rrule_rejects_nonsense():
    with pytest.raises(RecurrenceError):
        validate_rrule("FREQ=NEVER;INTERVAL=banana")


def test_validate_rrule_treats_blank_as_no_recurrence():
    assert validate_rrule("   ") is None


def test_series_end_date_reads_until():
    assert series_end_date("FREQ=WEEKLY;UNTIL=20261231T235959Z") == "2026-12-31"
    assert series_end_date("FREQ=WEEKLY;COUNT=5") is None


def test_override_moves_one_occurrence_and_keeps_its_original_identity(
    db_session, make_event
):
    """An override is keyed on the date the occurrence *was*, not where it went."""
    event = make_event(
        "Soccer",
        start="2026-08-04T09:00",
        end="2026-08-04T10:00",
        rrule="FREQ=WEEKLY;BYDAY=TU;COUNT=3",
    )
    times = resolve_event_times(
        start="2026-08-13T09:00",
        end="2026-08-13T10:00",
        all_day=False,
        tzid="America/Chicago",
    )
    db_session.add(
        EventOverride(
            event_id=event.id,
            occurrence_date="2026-08-11",
            title="Soccer (moved)",
            **{k: v for k, v in times.items() if k != "tzid"},
        )
    )
    db_session.commit()

    occurrences = expand_event(
        event,
        overrides=db_session.query(EventOverride).all(),
        window_start=datetime(2026, 8, 1, tzinfo=UTC),
        window_end=datetime(2026, 9, 1, tzinfo=UTC),
        local_tz=CHICAGO,
    )
    moved = [o for o in occurrences if o.occurrence_date == "2026-08-11"][0]
    assert moved.title == "Soccer (moved)"
    assert moved.start_local_date == "2026-08-13"


def test_cancelled_override_removes_only_that_occurrence(db_session, make_event):
    event = make_event(
        "Scouts", start="2026-08-04T19:00", rrule="FREQ=WEEKLY;BYDAY=TU;COUNT=3"
    )
    db_session.add(
        EventOverride(event_id=event.id, occurrence_date="2026-08-11", cancelled=True)
    )
    db_session.commit()

    occurrences = expand_event(
        event,
        overrides=db_session.query(EventOverride).all(),
        window_start=datetime(2026, 8, 1, tzinfo=UTC),
        window_end=datetime(2026, 9, 1, tzinfo=UTC),
        local_tz=CHICAGO,
    )
    assert [o.occurrence_date for o in occurrences] == ["2026-08-04", "2026-08-18"]


def test_unbounded_daily_rule_is_capped_rather_than_expanded_forever(
    make_event, capsys
):
    from rally.calendars.native import MAX_OCCURRENCES_PER_EVENT

    event = make_event("Vitamins", start="2020-01-01T08:00", rrule="FREQ=DAILY")
    occurrences = expand_event(
        event,
        overrides=[],
        window_start=datetime(2020, 1, 1, tzinfo=UTC),
        window_end=datetime(2030, 1, 1, tzinfo=UTC),
        local_tz=CHICAGO,
    )
    assert len(occurrences) <= MAX_OCCURRENCES_PER_EVENT
    assert "occurrence cap" in capsys.readouterr().out


def test_all_day_recurrence_stays_all_day(make_event):
    event = make_event(
        "Bin day",
        start="2026-08-03",
        end="2026-08-03",
        all_day=True,
        rrule="FREQ=WEEKLY;COUNT=3",
    )
    occurrences = expand_event(
        event,
        overrides=[],
        window_start=datetime(2026, 8, 1, tzinfo=UTC),
        window_end=datetime(2026, 9, 1, tzinfo=UTC),
        local_tz=CHICAGO,
    )
    assert len(occurrences) == 3
    assert all(o.all_day for o in occurrences)
    assert [o.start_local_date for o in occurrences] == [
        "2026-08-03",
        "2026-08-10",
        "2026-08-17",
    ]


# --- Input parsing -------------------------------------------------------------


def test_end_before_start_is_rejected():
    with pytest.raises(EventTimeError):
        resolve_event_times(
            start="2026-08-11T10:00", end="2026-08-11T09:00", all_day=False, tzid="UTC"
        )


def test_missing_end_defaults_to_an_hour():
    times = resolve_event_times(
        start="2026-08-11T09:00", end=None, all_day=False, tzid="UTC"
    )
    assert times["end_utc"] - times["start_utc"] == timedelta(hours=1)


def test_unknown_timezone_is_rejected():
    with pytest.raises(EventTimeError):
        resolve_event_times(
            start="2026-08-11", end=None, all_day=True, tzid="Mars/Olympus"
        )


# --- Source collection ---------------------------------------------------------


def test_collect_occurrences_merges_native_and_ics(
    db_session, make_member, make_native_calendar, make_event, mock_requests
):
    from rally.calendars import collect_occurrences
    from rally.models import Calendar

    owner = make_member("Jon")
    native = make_native_calendar(owner)
    make_event("Dentist", start="2026-08-11T09:00", calendar=native)

    db_session.add(
        Calendar(
            label="Work", url="https://cal.example/w.ics", family_member_id=owner.id
        )
    )
    db_session.commit()
    mock_requests.set_response(
        text=_ics(_vevent("Offsite", "20260812T140000Z")), status_code=200
    )

    result = collect_occurrences(
        db_session,
        start_day=date(2026, 8, 1),
        end_day_exclusive=date(2026, 9, 1),
        local_tz=CHICAGO,
    )
    assert [o.title for o in result.occurrences] == ["Dentist", "Offsite"]
    assert result.failures == []


def test_a_failing_feed_names_itself_and_does_not_hide_the_rest(
    db_session, make_member, make_native_calendar, make_event, mock_requests
):
    """One unreachable feed shortens the list; it never fails the page."""
    from rally.calendars import collect_occurrences
    from rally.models import Calendar

    owner = make_member("Jon")
    native = make_native_calendar(owner)
    make_event("Dentist", start="2026-08-11T09:00", calendar=native)
    db_session.add(
        Calendar(
            label="Work", url="https://cal.example/w.ics", family_member_id=owner.id
        )
    )
    db_session.commit()
    mock_requests.set_response(status_code=500)

    result = collect_occurrences(
        db_session,
        start_day=date(2026, 8, 1),
        end_day_exclusive=date(2026, 9, 1),
        local_tz=CHICAGO,
    )
    assert [o.title for o in result.occurrences] == ["Dentist"]
    assert result.failures == ["Work (Jon)"]
