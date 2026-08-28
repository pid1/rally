"""The external-calendar cache.

The bug this exists for: `/calendar` fetched every remote feed synchronously on
every request — 11.5s measured against three sources in production. So the
first-order assertion in most of these tests is *no network call happened*.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from rally import member_colors
from rally.calendars import cache as calendar_cache
from rally.calendars.occurrence import Occurrence
from rally.models import CalendarCache, FamilyMember

TZ = ZoneInfo("America/Chicago")


def _occ(uid="u1", title="Dentist", start=None, calendar_id=1):
    start = start or datetime(2026, 8, 20, 15, 0, tzinfo=UTC)
    return Occurrence(
        uid=uid,
        source="ics",
        title=title,
        start=start,
        end=start + timedelta(hours=1),
        start_local_date="2026-08-20",
        end_local_date="2026-08-20",
        calendar_id=calendar_id,
        calendar_label="Work",
        attendees=("Jon",),
    )


@pytest.fixture
def ics_calendar(db_session, make_member):
    from rally.models import Calendar

    member = make_member("Jon")
    cal = Calendar(
        label="Work",
        url="https://example.invalid/feed.ics",
        family_member_id=member.id,
        cal_type="ics",
    )
    db_session.add(cal)
    db_session.commit()
    db_session.refresh(cal)
    return cal


class TestSerialisation:
    def test_round_trips_every_field(self):
        original = _occ()
        restored = calendar_cache.occurrence_from_dict(calendar_cache.occurrence_to_dict(original))
        assert restored == original

    def test_attendees_survive_as_a_tuple(self):
        """A list would break dedupe_key, which hashes the occurrence."""
        restored = calendar_cache.occurrence_from_dict(calendar_cache.occurrence_to_dict(_occ()))
        assert isinstance(restored.attendees, tuple)

    def test_instants_keep_their_timezone(self):
        restored = calendar_cache.occurrence_from_dict(calendar_cache.occurrence_to_dict(_occ()))
        assert restored.start.tzinfo is not None
        assert restored.start == datetime(2026, 8, 20, 15, 0, tzinfo=UTC)


class TestContentFingerprint:
    """What makes the incremental path fire at all.

    Both observations below are from this install's real Google feeds: a naive
    body hash reported "changed" on every single fetch, so the incremental
    branch was dead code until the fingerprint became semantic.
    """

    BASE = (
        "BEGIN:VCALENDAR\r\nVERSION:2.0\r\n"
        "BEGIN:VEVENT\r\nUID:a\r\nSUMMARY:Dentist\r\nDTSTAMP:20260815T235044Z\r\nEND:VEVENT\r\n"
        "BEGIN:VEVENT\r\nUID:b\r\nSUMMARY:Optician\r\nDTSTAMP:20260815T235044Z\r\nEND:VEVENT\r\n"
        "END:VCALENDAR\r\n"
    )

    def test_a_new_dtstamp_is_not_a_change(self):
        """Google rewrites DTSTAMP on every response."""
        later = self.BASE.replace("20260815T235044Z", "20260815T235149Z")
        assert calendar_cache.content_fingerprint(self.BASE) == calendar_cache.content_fingerprint(
            later
        )

    def test_reordered_events_are_not_a_change(self):
        """The same feed comes back with its VEVENTs in a different order."""
        reordered = (
            "BEGIN:VCALENDAR\r\nVERSION:2.0\r\n"
            "BEGIN:VEVENT\r\nUID:b\r\nSUMMARY:Optician\r\nDTSTAMP:20260815T235044Z\r\nEND:VEVENT\r\n"
            "BEGIN:VEVENT\r\nUID:a\r\nSUMMARY:Dentist\r\nDTSTAMP:20260815T235044Z\r\nEND:VEVENT\r\n"
            "END:VCALENDAR\r\n"
        )
        assert calendar_cache.content_fingerprint(self.BASE) == calendar_cache.content_fingerprint(
            reordered
        )

    def test_a_real_edit_is_a_change(self):
        """The fingerprint must not be so loose that it misses an actual edit."""
        edited = self.BASE.replace("SUMMARY:Dentist", "SUMMARY:Dentist (moved)")
        assert calendar_cache.content_fingerprint(self.BASE) != calendar_cache.content_fingerprint(
            edited
        )

    def test_a_new_event_is_a_change(self):
        added = self.BASE.replace(
            "END:VCALENDAR",
            "BEGIN:VEVENT\r\nUID:c\r\nSUMMARY:School run\r\nEND:VEVENT\r\nEND:VCALENDAR",
        )
        assert calendar_cache.content_fingerprint(self.BASE) != calendar_cache.content_fingerprint(
            added
        )

    def test_folded_lines_are_unfolded_before_sorting(self):
        """Sorting raw lines would tear a folded value away from its property,
        which could collide two genuinely different feeds."""
        folded = "BEGIN:VEVENT\r\nUID:a\r\nLOCATION:Lucy's Alterations\r\n 108 N Main St\r\nEND:VEVENT\r\n"
        other = "BEGIN:VEVENT\r\nUID:a\r\nLOCATION:Lucy's Alterations\r\n 999 S Other Rd\r\nEND:VEVENT\r\n"
        assert calendar_cache.content_fingerprint(folded) != calendar_cache.content_fingerprint(
            other
        )


class TestReading:
    def test_returns_cached_occurrences(self, db_session, ics_calendar):
        db_session.add(
            CalendarCache(
                calendar_id=ics_calendar.id,
                occurrences=[calendar_cache.occurrence_to_dict(_occ(calendar_id=ics_calendar.id))],
                window_start="2026-08-01",
                window_end="2027-01-01",
            )
        )
        db_session.commit()

        occ, failures, uncached = calendar_cache.read_cached(
            db_session,
            window_start=datetime(2026, 8, 1, tzinfo=UTC),
            window_end=datetime(2026, 9, 1, tzinfo=UTC),
        )

        assert [o.title for o in occ] == ["Dentist"]
        assert failures == []
        assert uncached == []

    def test_filters_to_the_requested_window(self, db_session, ics_calendar):
        db_session.add(
            CalendarCache(
                calendar_id=ics_calendar.id,
                occurrences=[calendar_cache.occurrence_to_dict(_occ(calendar_id=ics_calendar.id))],
                window_start="2026-08-01",
                window_end="2027-01-01",
            )
        )
        db_session.commit()

        occ, _f, _u = calendar_cache.read_cached(
            db_session,
            window_start=datetime(2026, 9, 1, tzinfo=UTC),
            window_end=datetime(2026, 10, 1, tzinfo=UTC),
        )
        assert occ == []

    def test_an_uncached_calendar_is_reported_not_silently_empty(self, db_session, ics_calendar):
        """A fresh install must fall back to a live fetch, not show nothing."""
        _occ_list, _f, uncached = calendar_cache.read_cached(
            db_session,
            window_start=datetime(2026, 8, 1, tzinfo=UTC),
            window_end=datetime(2026, 9, 1, tzinfo=UTC),
        )
        assert uncached == [ics_calendar.id]

    def test_a_failing_feed_still_serves_its_last_good_data(self, db_session, ics_calendar):
        """A stale calendar beats an empty one; the failure travels alongside."""
        db_session.add(
            CalendarCache(
                calendar_id=ics_calendar.id,
                occurrences=[calendar_cache.occurrence_to_dict(_occ(calendar_id=ics_calendar.id))],
                window_start="2026-08-01",
                window_end="2027-01-01",
                last_error="ConnectionError: unreachable",
                failure_count=3,
            )
        )
        db_session.commit()

        occ, failures, _u = calendar_cache.read_cached(
            db_session,
            window_start=datetime(2026, 8, 1, tzinfo=UTC),
            window_end=datetime(2026, 9, 1, tzinfo=UTC),
        )

        assert [o.title for o in occ] == ["Dentist"]
        assert failures == ["Work"]


class TestSyncing:
    def _feed(self, uid="abc", summary="Dentist"):
        return (
            "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//test//EN\r\n"
            "BEGIN:VEVENT\r\n"
            f"UID:{uid}\r\nSUMMARY:{summary}\r\n"
            "DTSTART:20260820T150000Z\r\nDTEND:20260820T160000Z\r\n"
            "END:VEVENT\r\nEND:VCALENDAR\r\n"
        )

    def test_populates_the_cache(self, db_session, ics_calendar, mock_requests):
        mock_requests.set_response(text=self._feed())

        summary = calendar_cache.sync_calendars(db_session, TZ)

        assert summary["synced"] == 1
        row = db_session.query(CalendarCache).one()
        assert row.calendar_id == ics_calendar.id
        assert len(row.occurrences) == 1
        assert row.occurrences[0]["title"] == "Dentist"
        assert row.content_hash
        assert row.last_error is None

    def test_an_unchanged_body_skips_re_expansion(self, db_session, ics_calendar, mock_requests):
        """The incremental path that actually fires here: neither production
        feed sends a validator, so the fingerprint is what saves the parse."""
        mock_requests.set_response(text=self._feed())
        calendar_cache.sync_calendars(db_session, TZ)
        first = db_session.query(CalendarCache).one()
        changed_at = first.changed_at

        second = calendar_cache.sync_calendars(db_session, TZ)

        assert second["unchanged"] == 1
        assert second["synced"] == 0
        row = db_session.query(CalendarCache).one()
        assert row.changed_at == changed_at, "an unchanged body must not restamp changed_at"

    def test_a_rewritten_dtstamp_still_counts_as_unchanged(
        self, db_session, ics_calendar, mock_requests
    ):
        """The end-to-end version of the fingerprint tests: Google rewrites
        DTSTAMP every fetch, and that must not trigger a re-expansion."""
        mock_requests.set_response(text=self._feed())
        calendar_cache.sync_calendars(db_session, TZ)

        restamped = self._feed().replace(
            "DTSTART:20260820T150000Z", "DTSTART:20260820T150000Z\r\nDTSTAMP:20260815T235149Z"
        )
        mock_requests.set_response(text=restamped)
        summary = calendar_cache.sync_calendars(db_session, TZ)

        assert summary["unchanged"] == 1, "a fresh DTSTAMP is not a content change"

    def test_a_changed_body_re_expands(self, db_session, ics_calendar, mock_requests):
        mock_requests.set_response(text=self._feed())
        calendar_cache.sync_calendars(db_session, TZ)

        mock_requests.set_response(text=self._feed(uid="def", summary="Optician"))
        summary = calendar_cache.sync_calendars(db_session, TZ)

        assert summary["synced"] == 1
        assert db_session.query(CalendarCache).one().occurrences[0]["title"] == "Optician"

    def test_a_304_keeps_the_cache_and_costs_nothing(self, db_session, ics_calendar, mock_requests):
        mock_requests.set_response(text=self._feed())
        calendar_cache.sync_calendars(db_session, TZ)
        row = db_session.query(CalendarCache).one()
        row.etag = 'W/"v1"'
        db_session.commit()

        mock_requests.set_response(status_code=304, text="")
        summary = calendar_cache.sync_calendars(db_session, TZ)

        assert summary["unchanged"] == 1
        assert len(db_session.query(CalendarCache).one().occurrences) == 1
        # The conditional header must actually have been sent.
        assert mock_requests.calls[-1]["kwargs"]["headers"]["If-None-Match"] == 'W/"v1"'

    def _recolor(self, db_session, color):
        member = db_session.query(FamilyMember).filter_by(name="Jon").one()
        member.color = color
        db_session.commit()
        return color

    def test_a_304_picks_up_a_new_member_color(self, db_session, ics_calendar, mock_requests):
        """A member's color is ours, not the feed's, so the paths that skip
        re-expansion must still revisit it. Otherwise a calendar nobody edits
        upstream pins the old dot forever — and Refresh lands here too, so
        there is no way out of it from the UI."""
        mock_requests.set_response(text=self._feed())
        calendar_cache.sync_calendars(db_session, TZ)
        row = db_session.query(CalendarCache).one()
        row.etag = 'W/"v1"'
        db_session.commit()
        recolored = self._recolor(db_session, member_colors.PALETTE[1].value)

        mock_requests.set_response(status_code=304, text="")
        summary = calendar_cache.sync_calendars(db_session, TZ)

        assert summary["unchanged"] == 1, "the feed did not change; only our color did"
        stored = db_session.query(CalendarCache).one().occurrences
        assert [o["member_color"] for o in stored] == [recolored]

    def test_an_unchanged_body_picks_up_a_new_member_color(
        self, db_session, ics_calendar, mock_requests
    ):
        """The same guarantee on the fingerprint path, which is the one that
        actually fires here — neither production feed sends a validator."""
        mock_requests.set_response(text=self._feed())
        calendar_cache.sync_calendars(db_session, TZ)
        recolored = self._recolor(db_session, member_colors.PALETTE[1].value)

        summary = calendar_cache.sync_calendars(db_session, TZ)

        assert summary["unchanged"] == 1
        stored = db_session.query(CalendarCache).one().occurrences
        assert [o["member_color"] for o in stored] == [recolored]

    def test_a_stable_color_leaves_the_blob_alone(self, db_session, ics_calendar, mock_requests):
        """The guard on the restamp. Without it a warm cache would rewrite
        every stored occurrence every fifteen minutes to no effect."""
        mock_requests.set_response(text=self._feed())
        calendar_cache.sync_calendars(db_session, TZ)
        row = db_session.query(CalendarCache).one()
        before = row.occurrences
        owner = db_session.query(FamilyMember).filter_by(name="Jon").one()

        assert calendar_cache._restamp_member_color(row, owner) is False
        assert row.occurrences is before, "an unchanged color must not reassign the column"

    def test_a_failure_keeps_the_previous_occurrences(
        self, db_session, ics_calendar, mock_requests
    ):
        mock_requests.set_response(text=self._feed())
        calendar_cache.sync_calendars(db_session, TZ)

        def boom(*args, **kwargs):
            raise ConnectionError("feed unreachable")

        mock_requests.set_handler(boom)
        summary = calendar_cache.sync_calendars(db_session, TZ)

        assert summary["failed"] == 1
        row = db_session.query(CalendarCache).one()
        assert len(row.occurrences) == 1, "a down feed must not blank the calendar"
        assert "feed unreachable" in row.last_error
        assert row.failure_count == 1

    def test_recovery_clears_the_error(self, db_session, ics_calendar, mock_requests):
        def boom(*args, **kwargs):
            raise ConnectionError("down")

        mock_requests.set_handler(boom)
        calendar_cache.sync_calendars(db_session, TZ)

        mock_requests.set_response(text=self._feed())
        calendar_cache.sync_calendars(db_session, TZ)

        row = db_session.query(CalendarCache).one()
        assert row.last_error is None
        assert row.failure_count == 0

    def test_native_calendars_are_never_cached(self, db_session, make_member, mock_requests):
        """They are a local query, and the events most likely to have just been
        edited. Serving them stale would make Rally feel broken."""
        from rally.models import Calendar

        member = make_member("Jon")
        db_session.add(
            Calendar(label="Rally", url="", family_member_id=member.id, cal_type="native")
        )
        db_session.commit()

        summary = calendar_cache.sync_calendars(db_session, TZ)

        assert summary["calendars"] == 0
        assert db_session.query(CalendarCache).count() == 0
        assert mock_requests.calls == []


class TestStaleness:
    def test_syncs_when_a_calendar_has_never_been_cached(
        self, db_session, ics_calendar, mock_requests
    ):
        mock_requests.set_response(text=TestSyncing()._feed())
        assert calendar_cache.sync_if_stale(db_session, TZ) is not None

    def test_does_nothing_while_the_cache_is_warm(self, db_session, ics_calendar, mock_requests):
        mock_requests.set_response(text=TestSyncing()._feed())
        calendar_cache.sync_calendars(db_session, TZ)
        before = len(mock_requests.calls)

        assert calendar_cache.sync_if_stale(db_session, TZ) is None
        assert len(mock_requests.calls) == before, "a warm cache must not touch the network"

    def test_syncs_once_the_interval_has_elapsed(
        self, db_session, ics_calendar, mock_requests, make_setting
    ):
        make_setting("calendar_sync_interval_minutes", "15")
        mock_requests.set_response(text=TestSyncing()._feed())
        calendar_cache.sync_calendars(db_session, TZ)

        row = db_session.query(CalendarCache).one()
        row.fetched_at = datetime.now(UTC) - timedelta(minutes=16)
        db_session.commit()

        assert calendar_cache.sync_if_stale(db_session, TZ) is not None

    def test_interval_is_configurable(self, db_session, make_setting):
        make_setting("calendar_sync_interval_minutes", "60")
        assert calendar_cache.sync_interval_minutes(db_session) == 60

    def test_a_nonsense_interval_falls_back(self, db_session, make_setting):
        make_setting("calendar_sync_interval_minutes", "not a number")
        assert (
            calendar_cache.sync_interval_minutes(db_session)
            == calendar_cache.DEFAULT_SYNC_INTERVAL_MINUTES
        )

    def test_no_external_calendars_means_no_sync(self, db_session, mock_requests):
        assert calendar_cache.sync_if_stale(db_session, TZ) is None
        assert mock_requests.calls == []


class TestReadPath:
    """The whole point: a read must not touch the network."""

    def test_listing_events_does_not_fetch_remotely(
        self, client, db_session, ics_calendar, mock_requests
    ):
        db_session.add(
            CalendarCache(
                calendar_id=ics_calendar.id,
                occurrences=[calendar_cache.occurrence_to_dict(_occ(calendar_id=ics_calendar.id))],
                window_start="2026-01-01",
                window_end="2027-12-31",
                fetched_at=datetime.now(UTC),
            )
        )
        db_session.commit()
        before = len(mock_requests.calls)

        response = client.get("/api/events?start=2026-08-01&end=2026-09-01")

        assert response.status_code == 200
        assert len(mock_requests.calls) == before, (
            "a warm cache must serve the page without any remote fetch"
        )

    def test_sync_status_reports_freshness(self, client, db_session, ics_calendar):
        db_session.add(
            CalendarCache(
                calendar_id=ics_calendar.id,
                occurrences=[],
                window_start="2026-01-01",
                window_end="2027-12-31",
                last_error="boom",
            )
        )
        db_session.commit()

        body = client.get("/api/events/sync/status").json()

        assert body["cached"] == 1
        assert body["expected"] == 1
        assert body["failing"] == ["Work"]
        assert body["interval_minutes"] == calendar_cache.DEFAULT_SYNC_INTERVAL_MINUTES

    def test_manual_sync_forces_a_refresh(self, client, ics_calendar, mock_requests):
        mock_requests.set_response(text=TestSyncing()._feed())

        body = client.post("/api/events/sync").json()

        assert body["synced"] == 1


class TestCaldavSyncTokens:
    """RFC 6578: ask what changed instead of downloading everything.

    Verified against iCloud, which answers a delta call in ~0.12s where a full
    fetch and expansion of the same account costs ~2.1s.
    """

    @pytest.fixture
    def caldav_calendar(self, db_session, make_member):
        from rally.models import Calendar

        member = make_member("Jon")
        cal = Calendar(
            label="Personal",
            url="https://caldav.icloud.com/",
            family_member_id=member.id,
            cal_type="caldav_apple",
            username="jon@example.invalid",
            password="app-specific",
        )
        db_session.add(cal)
        db_session.commit()
        db_session.refresh(cal)
        return cal

    def _seed_cache(self, db_session, cal, tokens=None):
        row = CalendarCache(
            calendar_id=cal.id,
            occurrences=[calendar_cache.occurrence_to_dict(_occ(calendar_id=cal.id))],
            window_start="2026-01-01",
            window_end="2027-12-31",
            sync_tokens=tokens,
        )
        db_session.add(row)
        db_session.commit()
        return row

    def test_an_unchanged_collection_skips_the_fetch(
        self, db_session, caldav_calendar, monkeypatch
    ):
        self._seed_cache(db_session, caldav_calendar, {"cal-a": "tok-1"})

        import rally.caldav_client as cc

        monkeypatch.setattr(cc, "sync_probe", lambda cal, stored: ({"cal-a": "tok-1"}, True))

        def must_not_run(*a, **k):
            raise AssertionError("a full fetch must not happen when nothing changed")

        monkeypatch.setattr(cc, "fetch_apple_caldav", must_not_run)

        summary = calendar_cache.sync_calendars(db_session, TZ)

        assert summary["unchanged"] == 1
        assert summary["synced"] == 0

    def test_a_changed_collection_fetches_and_stores_new_tokens(
        self, db_session, caldav_calendar, monkeypatch
    ):
        self._seed_cache(db_session, caldav_calendar, {"cal-a": "tok-1"})

        import rally.caldav_client as cc

        monkeypatch.setattr(cc, "sync_probe", lambda cal, stored: ({"cal-a": "tok-2"}, False))
        monkeypatch.setattr(
            cc,
            "fetch_apple_caldav",
            lambda *a, **k: [_occ(title="Moved", calendar_id=caldav_calendar.id)],
        )

        summary = calendar_cache.sync_calendars(db_session, TZ)

        assert summary["synced"] == 1
        row = db_session.query(CalendarCache).one()
        assert row.occurrences[0]["title"] == "Moved"
        assert row.sync_tokens == {"cal-a": "tok-2"}

    def test_a_server_without_sync_collection_still_works(
        self, db_session, caldav_calendar, monkeypatch
    ):
        """Google's endpoint may not support it; that is not an error."""
        self._seed_cache(db_session, caldav_calendar, None)

        import rally.caldav_client as cc

        monkeypatch.setattr(cc, "sync_probe", lambda cal, stored: (None, False))
        monkeypatch.setattr(
            cc, "fetch_apple_caldav", lambda *a, **k: [_occ(calendar_id=caldav_calendar.id)]
        )

        summary = calendar_cache.sync_calendars(db_session, TZ)

        assert summary["synced"] == 1, "an unsupported server must fall back, not fail"

    def test_the_first_sync_never_probes(self, db_session, caldav_calendar, monkeypatch):
        """With no cached occurrences there is nothing to skip, and no baseline
        token to compare against."""
        import rally.caldav_client as cc

        calls = []
        monkeypatch.setattr(
            cc, "sync_probe", lambda cal, stored: (calls.append(stored), ({"a": "t"}, False))[1]
        )
        monkeypatch.setattr(
            cc, "fetch_apple_caldav", lambda *a, **k: [_occ(calendar_id=caldav_calendar.id)]
        )

        summary = calendar_cache.sync_calendars(db_session, TZ)

        assert summary["synced"] == 1
        # Probed only for the post-fetch baseline, never to decide whether to fetch.
        assert calls == [None]

    def test_a_probe_failure_is_reported_not_swallowed(
        self, db_session, caldav_calendar, monkeypatch
    ):
        """Reaching the principal is the same work a fetch does, so failing
        there is a real failure rather than a reason to fetch anyway."""
        self._seed_cache(db_session, caldav_calendar, {"cal-a": "tok-1"})

        import rally.caldav_client as cc

        def boom(cal, stored):
            raise RuntimeError("CalDAV connection failed: unauthorised")

        monkeypatch.setattr(cc, "sync_probe", boom)

        summary = calendar_cache.sync_calendars(db_session, TZ)

        assert summary["failed"] == 1
        row = db_session.query(CalendarCache).one()
        assert "unauthorised" in row.last_error
        assert len(row.occurrences) == 1, "a failed probe must not blank the calendar"
