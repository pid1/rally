"""Preparedness: schedule arithmetic, the refresh digest, the API, the go list.

The digest tests are the ones that matter most. A refresh notification that
never arrives, or one that arrives every morning for a month, is the whole
feature failing.
"""

from datetime import UTC, date, datetime

import pytest

from rally.golist import build_groups, render_csv, render_markdown, render_pdf
from rally.models import PrepRefreshNotice
from rally.preparedness import (
    add_months,
    days_until,
    find_due_items,
    is_due,
    mark_refreshed,
    run_daily_digest,
    send_digest,
    status_of,
)

TODAY = date(2026, 8, 15)


def _due(make_prep_item, **kwargs):
    kwargs.setdefault("refresh_mode", "date")
    kwargs.setdefault("next_refresh_date", TODAY.isoformat())
    return make_prep_item(**kwargs)


# --- Schedule arithmetic -------------------------------------------------------


class TestAddMonths:
    def test_simple(self):
        assert add_months(date(2026, 1, 15), 6) == date(2026, 7, 15)

    def test_crosses_year(self):
        assert add_months(date(2026, 8, 15), 12) == date(2027, 8, 15)

    def test_month_end_clamps(self):
        """31 August + 6 months has no 31st to land on."""
        assert add_months(date(2026, 8, 31), 6) == date(2027, 2, 28)

    def test_leap_year_clamps_to_29(self):
        assert add_months(date(2027, 8, 31), 6) == date(2028, 2, 29)

    def test_five_years(self):
        assert add_months(date(2026, 8, 15), 60) == date(2031, 8, 15)


class TestDueness:
    def test_unscheduled_is_never_due(self, make_prep_item):
        item = make_prep_item()
        assert is_due(item, date(2030, 1, 1)) is False
        assert status_of(item, date(2030, 1, 1)) == "ok"

    def test_fires_on_the_date_with_no_lead(self, make_prep_item):
        item = make_prep_item(refresh_mode="date", next_refresh_date="2027-01-01")
        assert is_due(item, date(2026, 12, 31)) is False
        assert is_due(item, date(2027, 1, 1)) is True

    def test_lead_time_opens_the_window_early(self, make_prep_item):
        item = make_prep_item(
            refresh_mode="date", next_refresh_date="2027-01-01", remind_days_before=30
        )
        assert is_due(item, date(2026, 12, 1)) is False
        assert is_due(item, date(2026, 12, 2)) is True

    def test_status_transitions(self, make_prep_item):
        item = make_prep_item(
            refresh_mode="date", next_refresh_date="2027-01-01", remind_days_before=30
        )
        assert status_of(item, date(2026, 11, 1)) == "ok"
        assert status_of(item, date(2026, 12, 15)) == "due"
        assert status_of(item, date(2027, 1, 1)) == "due"
        assert status_of(item, date(2027, 1, 2)) == "overdue"

    def test_days_until(self, make_prep_item):
        item = make_prep_item(refresh_mode="date", next_refresh_date="2026-08-25")
        assert days_until(item, TODAY) == 10
        assert days_until(item, date(2026, 8, 30)) == -5


class TestMarkRefreshed:
    def test_interval_reanchors_on_the_actual_date(self, db_session, make_prep_item):
        """For physical stock the clock starts when you swap it."""
        item = make_prep_item(
            refresh_mode="interval", refresh_interval_months=6, next_refresh_date="2026-08-15"
        )
        mark_refreshed(db_session, item, date(2026, 9, 5))  # three weeks late

        assert item.last_refreshed_on == "2026-09-05"
        assert item.next_refresh_date == "2027-03-05"
        assert item.refresh_mode == "interval"

    def test_spent_one_shot_does_not_invent_a_date(self, db_session, make_prep_item):
        item = make_prep_item(refresh_mode="date", next_refresh_date="2027-01-01")
        mark_refreshed(db_session, item, date(2027, 1, 1))

        assert item.next_refresh_date is None
        assert item.refresh_mode == "none"
        assert item.last_refreshed_on == "2027-01-01"


# --- The digest ----------------------------------------------------------------


class TestFindDue:
    def test_finds_an_item_due_today(self, db_session, make_prep_item):
        item = _due(make_prep_item)
        assert [i.id for i in find_due_items(db_session, TODAY)] == [item.id]

    def test_ignores_unscheduled_and_future(self, db_session, make_prep_item):
        make_prep_item(name="Spork")
        make_prep_item(refresh_mode="date", next_refresh_date="2027-01-01")
        assert find_due_items(db_session, TODAY) == []

    def test_beyond_the_horizon_is_excluded(self, db_session, make_prep_item):
        make_prep_item(refresh_mode="date", next_refresh_date="2030-01-01")
        assert find_due_items(db_session, TODAY) == []

    def test_already_announced_is_excluded(self, db_session, make_prep_item, make_prep_notice):
        item = _due(make_prep_item)
        make_prep_notice(item.id, TODAY.isoformat())
        assert find_due_items(db_session, TODAY) == []


class TestSendDigest:
    def test_quiet_day_sends_nothing(
        self, db_session, make_prep_item, mock_pushover, prep_pushover
    ):
        make_prep_item(name="Spork")
        result = send_digest(db_session, TODAY)

        assert result.count == 0
        assert result.sent is False
        assert result.skipped_reason == "nothing due"
        assert mock_pushover.sent == [], "a quiet day must not send a push at all"

    def test_one_digest_for_several_items(
        self, db_session, make_prep_item, make_prep_location, mock_pushover, prep_pushover
    ):
        loc = make_prep_location("Water")
        _due(make_prep_item, name="Sawyer filters", location_id=loc.id, quantity="2")
        _due(make_prep_item, name="Anker power bank", location_id=loc.id)
        _due(make_prep_item, name="SPAM Classic")

        result = send_digest(db_session, TODAY)

        assert result.count == 3
        assert result.sent is True
        assert len(mock_pushover.sent) == 1, "one digest, not one push per item"
        payload = mock_pushover.sent[0]
        assert "3 items to refresh" in payload["title"]
        assert "Sawyer filters" in payload["message"]
        assert "Water" in payload["message"]

    def test_announces_exactly_once(self, db_session, make_prep_item, mock_pushover, prep_pushover):
        _due(make_prep_item)

        first = send_digest(db_session, TODAY)
        second = send_digest(db_session, TODAY)

        assert first.sent is True
        assert second.count == 0
        assert len(mock_pushover.sent) == 1

    def test_still_silent_the_next_day(
        self, db_session, make_prep_item, mock_pushover, prep_pushover
    ):
        """The notice, not a date gate, is what suppresses the repeat."""
        _due(make_prep_item)
        send_digest(db_session, TODAY)
        send_digest(db_session, date(2026, 8, 16))

        assert len(mock_pushover.sent) == 1

    def test_moving_the_date_rearms(self, db_session, make_prep_item, mock_pushover, prep_pushover):
        item = _due(make_prep_item)
        send_digest(db_session, TODAY)

        item.next_refresh_date = "2026-09-01"
        db_session.commit()
        later = send_digest(db_session, date(2026, 9, 1))

        assert later.sent is True
        assert len(mock_pushover.sent) == 2

    def test_lead_window_fires_once_across_its_whole_span(
        self, db_session, make_prep_item, mock_pushover, prep_pushover
    ):
        _due(make_prep_item, next_refresh_date="2026-09-14", remind_days_before=30)
        for day in range(20, 32):
            send_digest(db_session, date(2026, 8, day))

        assert len(mock_pushover.sent) == 1

    def test_failure_records_nothing_and_retries(
        self, db_session, make_prep_item, mock_pushover, prep_pushover
    ):
        _due(make_prep_item)
        mock_pushover.fail_with("application token is invalid")

        failed = send_digest(db_session, TODAY)

        assert failed.sent is False
        assert failed.failed
        assert db_session.query(PrepRefreshNotice).count() == 0, (
            "a failed send must not be recorded as delivered"
        )

    def test_missing_app_token_skips_cleanly(
        self, db_session, make_prep_item, mock_pushover, make_setting
    ):
        _due(make_prep_item)
        result = send_digest(db_session, TODAY)

        assert result.sent is False
        assert "application token" in result.skipped_reason
        assert mock_pushover.sent == []

    def test_member_without_a_key_is_named_not_dropped(
        self, db_session, make_prep_item, make_member, mock_pushover, prep_pushover
    ):
        make_member("Sarah")  # no Pushover key
        _due(make_prep_item)

        result = send_digest(db_session, TODAY)

        assert result.sent_to == ["Jon"]
        assert result.skipped == ["Sarah"]

    def test_disabled_switch_stops_everything(
        self, db_session, make_prep_item, make_setting, mock_pushover, prep_pushover
    ):
        make_setting("prep_notify_enabled", "false")
        _due(make_prep_item)

        result = send_digest(db_session, TODAY)

        assert "turned off" in result.skipped_reason
        assert mock_pushover.sent == []

    def test_dry_run_sends_and_records_nothing(
        self, db_session, make_prep_item, mock_pushover, prep_pushover
    ):
        _due(make_prep_item)
        result = send_digest(db_session, TODAY, dry_run=True)

        assert result.count == 1
        assert result.sent is False
        assert mock_pushover.sent == []
        assert db_session.query(PrepRefreshNotice).count() == 0

    def test_overdue_reads_as_overdue(
        self, db_session, make_prep_item, mock_pushover, prep_pushover
    ):
        _due(make_prep_item, name="Water drums", next_refresh_date="2026-07-01")
        send_digest(db_session, TODAY)

        payload = mock_pushover.sent[0]
        assert "OVERDUE since 2026-07-01" in payload["message"]

    def test_long_digest_is_truncated_not_rejected(
        self, db_session, make_prep_item, mock_pushover, prep_pushover
    ):
        for n in range(60):
            _due(make_prep_item, name=f"Item number {n} with a fairly long descriptive name")

        send_digest(db_session, TODAY)

        payload = mock_pushover.sent[0]
        assert len(payload["message"]) <= 1024
        assert "more" in payload["message"]


class TestDailyDigestGate:
    def test_waits_until_the_configured_time(
        self, db_session, make_prep_item, make_setting, mock_pushover, prep_pushover
    ):
        make_setting("local_timezone", "America/Chicago")
        make_setting("prep_notify_time", "08:00")
        _due(make_prep_item, next_refresh_date="2026-08-15")

        # 11:00 UTC is 06:00 in Chicago — before the send time.
        early = run_daily_digest(db_session, datetime(2026, 8, 15, 11, 0, tzinfo=UTC))
        assert early.sent is False
        assert mock_pushover.sent == []

        # 14:00 UTC is 09:00 in Chicago.
        on_time = run_daily_digest(db_session, datetime(2026, 8, 15, 14, 0, tzinfo=UTC))
        assert on_time.sent is True

    def test_runs_at_most_once_per_local_day(
        self, db_session, make_prep_item, make_setting, mock_pushover, prep_pushover
    ):
        make_setting("local_timezone", "America/Chicago")
        _due(make_prep_item, next_refresh_date="2026-08-15")

        run_daily_digest(db_session, datetime(2026, 8, 15, 14, 0, tzinfo=UTC))
        again = run_daily_digest(db_session, datetime(2026, 8, 15, 15, 0, tzinfo=UTC))

        assert again.skipped_reason == "already ran today"
        assert len(mock_pushover.sent) == 1

    def test_uses_the_local_date_not_utc(
        self, db_session, make_prep_item, make_setting, mock_pushover, prep_pushover
    ):
        """03:30 UTC on the 16th is still the 15th in Chicago."""
        make_setting("local_timezone", "America/Chicago")
        make_setting("prep_notify_time", "08:00")
        _due(make_prep_item, next_refresh_date="2026-08-16")

        result = run_daily_digest(db_session, datetime(2026, 8, 16, 3, 30, tzinfo=UTC))
        assert result.count == 0, "not due yet in local terms"


# --- API -----------------------------------------------------------------------


class TestLocationsApi:
    def test_create_and_list(self, client):
        r = client.post("/api/preparedness/locations", json={"name": "Water", "sort_order": 4})
        assert r.status_code == 201
        assert [loc["name"] for loc in client.get("/api/preparedness/locations").json()] == [
            "Water"
        ]

    def test_duplicate_name_rejected_case_insensitively(self, client):
        client.post("/api/preparedness/locations", json={"name": "Water"})
        assert client.post("/api/preparedness/locations", json={"name": "water"}).status_code == 409

    def test_sort_order_drives_listing(self, client):
        client.post("/api/preparedness/locations", json={"name": "Zulu", "sort_order": 1})
        client.post("/api/preparedness/locations", json={"name": "Alpha", "sort_order": 2})
        names = [loc["name"] for loc in client.get("/api/preparedness/locations").json()]
        assert names == ["Zulu", "Alpha"]

    def test_delete_moves_items_to_unassigned(self, client):
        loc = client.post("/api/preparedness/locations", json={"name": "Truck"}).json()
        item = client.post(
            "/api/preparedness/items", json={"name": "Compass", "location_id": loc["id"]}
        ).json()

        assert client.delete(f"/api/preparedness/locations/{loc['id']}").status_code == 204

        after = client.get(f"/api/preparedness/items/{item['id']}").json()
        assert after["location_id"] is None
        assert after["location_name"] == "Unassigned"

        # Still on the go list, which is the failure that would actually matter.
        names = [
            i["name"]
            for g in client.get("/api/preparedness/go-list").json()["groups"]
            for i in g["items"]
        ]
        assert "Compass" in names


class TestItemsApi:
    def test_quantity_is_free_text(self, client):
        body = client.post(
            "/api/preparedness/items", json={"name": "Batteries", "quantity": "8-10"}
        ).json()
        assert body["quantity"] == "8-10"

    def test_empty_name_rejected(self, client):
        assert client.post("/api/preparedness/items", json={"name": "   "}).status_code == 422

    def test_unknown_location_rejected(self, client):
        r = client.post("/api/preparedness/items", json={"name": "Spork", "location_id": 999})
        assert r.status_code == 422

    def test_interval_seeds_the_first_date(self, client):
        body = client.post(
            "/api/preparedness/items",
            json={"name": "Water", "refresh_mode": "interval", "refresh_interval_months": 6},
        ).json()
        assert body["next_refresh_date"] is not None

    @pytest.mark.parametrize(
        "payload",
        [
            {"name": "Chili", "refresh_mode": "date"},
            {"name": "Chili", "refresh_mode": "none", "next_refresh_date": "2027-01-01"},
            {"name": "Water", "refresh_mode": "interval", "refresh_interval_months": 0},
        ],
    )
    def test_incoherent_schedules_rejected(self, client, payload):
        assert client.post("/api/preparedness/items", json=payload).status_code == 422

    def test_partial_update_leaves_omitted_fields_alone(self, client):
        item = client.post(
            "/api/preparedness/items",
            json={"name": "Chili", "quantity": "10", "notes": "Pop-top"},
        ).json()

        updated = client.put(f"/api/preparedness/items/{item['id']}", json={"quantity": "8"}).json()
        assert updated["quantity"] == "8"
        assert updated["notes"] == "Pop-top", "omission must not clear a field"

    def test_explicit_null_clears(self, client):
        item = client.post("/api/preparedness/items", json={"name": "Chili", "notes": "x"}).json()
        assert (
            client.put(f"/api/preparedness/items/{item['id']}", json={"notes": None}).json()[
                "notes"
            ]
            is None
        )

    def test_update_rejects_an_incoherent_merge(self, client):
        """Flipping only the mode still has to leave a valid item behind."""
        item = client.post(
            "/api/preparedness/items",
            json={"name": "Chili", "refresh_mode": "date", "next_refresh_date": "2027-01-01"},
        ).json()
        r = client.put(f"/api/preparedness/items/{item['id']}", json={"refresh_mode": "interval"})
        assert r.status_code == 422

    def test_bad_date_format_rejected(self, client):
        item = client.post("/api/preparedness/items", json={"name": "Chili"}).json()
        r = client.put(
            f"/api/preparedness/items/{item['id']}", json={"next_refresh_date": "01/01/2027"}
        )
        assert r.status_code == 422

    def test_refresh_endpoint(self, client):
        item = client.post(
            "/api/preparedness/items",
            json={"name": "Water", "refresh_mode": "interval", "refresh_interval_months": 6},
        ).json()

        refreshed = client.post(
            f"/api/preparedness/items/{item['id']}/refresh", json={"on": "2026-08-15"}
        ).json()
        assert refreshed["last_refreshed_on"] == "2026-08-15"
        assert refreshed["next_refresh_date"] == "2027-02-15"

    def test_search_covers_name_and_notes(self, client):
        client.post("/api/preparedness/items", json={"name": "SPAM", "notes": "high sodium"})
        client.post("/api/preparedness/items", json={"name": "Spork", "notes": "titanium"})

        assert len(client.get("/api/preparedness/items?search=spam").json()) == 1
        assert len(client.get("/api/preparedness/items?search=sodium").json()) == 1

    def test_location_filter_includes_unassigned(self, client):
        loc = client.post("/api/preparedness/locations", json={"name": "Water"}).json()
        client.post("/api/preparedness/items", json={"name": "Filter", "location_id": loc["id"]})
        client.post("/api/preparedness/items", json={"name": "Orphan"})

        assert len(client.get(f"/api/preparedness/items?location={loc['id']}").json()) == 1
        assert len(client.get("/api/preparedness/items?location=unassigned").json()) == 1


# --- Go list -------------------------------------------------------------------


class TestGoList:
    def test_orders_by_sort_order_then_unassigned_last(
        self, db_session, make_prep_location, make_prep_item
    ):
        water = make_prep_location("Water", sort_order=4)
        food = make_prep_location("Food", sort_order=1)
        make_prep_item(name="Filter", location_id=water.id)
        make_prep_item(name="SPAM", location_id=food.id)
        make_prep_item(name="Orphan")

        groups = build_groups(db_session)
        assert [name for _lid, name, _i in groups] == ["Food", "Water", "Unassigned"]

    def test_empty_locations_are_omitted(self, db_session, make_prep_location, make_prep_item):
        make_prep_location("Empty", sort_order=1)
        used = make_prep_location("Used", sort_order=2)
        make_prep_item(name="Thing", location_id=used.id)

        assert [name for _lid, name, _i in build_groups(db_session)] == ["Used"]

    def test_items_alphabetical_within_a_group(
        self, db_session, make_prep_location, make_prep_item
    ):
        loc = make_prep_location("Water")
        for name in ("Zebra", "apple", "Mango"):
            make_prep_item(name=name, location_id=loc.id)

        _lid, _name, items = build_groups(db_session)[0]
        assert [i.name for i in items] == ["apple", "Mango", "Zebra"]

    def test_markdown_uses_checkboxes(self, db_session, make_prep_location, make_prep_item):
        loc = make_prep_location("Food")
        make_prep_item(name="SPAM Classic", quantity="5", location_id=loc.id)

        out = render_markdown(build_groups(db_session), TODAY)
        assert "## Food" in out
        assert "- [ ] SPAM Classic — 5" in out

    def test_csv_columns(self, db_session, make_prep_location, make_prep_item):
        loc = make_prep_location("Food")
        make_prep_item(name="SPAM", location_id=loc.id)

        out = render_csv(build_groups(db_session), TODAY)
        assert out.splitlines()[0] == "location,name,quantity,notes,next_refresh_date"

    def test_pdf_renders(self, db_session, make_prep_location, make_prep_item):
        loc = make_prep_location("Food")
        make_prep_item(name="SPAM", quantity="5", location_id=loc.id)

        out = render_pdf(build_groups(db_session), TODAY)
        assert out.startswith(b"%PDF")

    def test_pdf_survives_unicode(self, db_session, make_prep_location, make_prep_item):
        """fpdf2's core fonts are latin-1 only, so an em dash or a degree sign
        in a notes field would otherwise raise in the export that matters most."""
        loc = make_prep_location("Cooking & Fuel")
        make_prep_item(
            name="Collapsible pot — green",
            location_id=loc.id,
            notes="Boil-safe to 100 °F · silicone walls — keep flame under the base “only”…",
        )
        assert render_pdf(build_groups(db_session), TODAY).startswith(b"%PDF")

    def test_export_endpoint_headers(self, client):
        client.post("/api/preparedness/items", json={"name": "Spork"})
        for fmt in ("md", "csv", "pdf"):
            r = client.get(f"/api/preparedness/go-list/export?format={fmt}")
            assert r.status_code == 200, fmt
            assert "attachment" in r.headers["content-disposition"]
            assert f".{fmt}" in r.headers["content-disposition"]

    def test_export_rejects_unknown_format(self, client):
        assert client.get("/api/preparedness/go-list/export?format=docx").status_code == 422

    def test_export_defaults_to_every_item(self, client):
        loc = client.post("/api/preparedness/locations", json={"name": "Water"}).json()
        client.post("/api/preparedness/items", json={"name": "Filter", "location_id": loc["id"]})
        client.post("/api/preparedness/items", json={"name": "Orphan"})

        body = client.get("/api/preparedness/go-list/export?format=md").text
        assert "Filter" in body and "Orphan" in body


class TestPages:
    def test_new_pages_render(self, client):
        for path in ("/preparedness", "/go-list"):
            assert client.get(path).status_code == 200, path

    def test_every_page_carries_the_other_dropdown(self, client):
        """One dropdown, on every page, holding both low-traffic sections."""
        for path in (
            "/dashboard",
            "/todo",
            "/shopping",
            "/calendar",
            "/dinner-planner",
            "/meal-history",
            "/settings",
            "/preparedness",
            "/go-list",
            "/styleguide",
        ):
            body = client.get(path).text
            assert 'id="other-dropdown"' in body, path
            assert 'href="/preparedness"' in body, path
            assert 'href="/go-list"' in body, path
            assert 'href="/dinner-planner"' in body, path

    def test_top_level_nav_is_the_four_daily_pages(self, client):
        """Dashboard, Tasks, Shopping, Calendar stay one tap away."""
        body = client.get("/dashboard").text
        nav = body[body.index("<nav>") : body.index("</nav>")]
        assert nav.count("nav-dropdown") >= 1
        for href in ('href="/dashboard"', 'href="/todo"', 'href="/shopping"', 'href="/calendar"'):
            assert href in nav
        # The meal and preparedness pages are reachable only through the dropdown.
        assert nav.index('href="/dinner-planner"') > nav.index("nav-dropdown")
