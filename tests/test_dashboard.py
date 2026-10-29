"""Tests for the dashboard router: snapshot rendering and the STEM card builder."""

from datetime import UTC, datetime

from rally.models import DashboardSnapshot
from rally.routers.dashboard import _build_stem_section


def test_dashboard_without_snapshot_shows_placeholder(client):
    resp = client.get("/dashboard")
    assert resp.status_code == 200
    assert "No dashboard data available" in resp.text


def test_dashboard_renders_most_recent_active_snapshot(client, db_session):
    db_session.add(
        DashboardSnapshot(
            date="2026-03-15", data={"greeting": "Hello Fam", "schedule": []}, is_active=True
        )
    )
    db_session.commit()

    resp = client.get("/dashboard")

    assert resp.status_code == 200
    assert "Hello Fam" in resp.text


def test_the_header_date_is_today_in_the_settings_zone(client, frozen_now, local_timezone):
    """#311: 7 PM Central on October 28 is already the 29th in UTC. The header
    and the tab title both read the household's date, with no leading zero."""
    local_timezone("America/Chicago")
    frozen_now(datetime(2026, 10, 29, 0, 30, tzinfo=UTC))

    html = client.get("/dashboard").text

    assert '<div class="subtitle" id="current-date">Wednesday, October 28, 2026</div>' in html
    assert "<title>Rally — Wednesday, October 28, 2026</title>" in html
    assert "Thursday" not in html
    assert "toLocaleDateString" not in html


def test_the_header_day_has_no_leading_zero(client, frozen_now, local_timezone):
    local_timezone("America/Chicago")
    frozen_now(datetime(2026, 10, 5, 15, 0, tzinfo=UTC))

    assert "Monday, October 5, 2026" in client.get("/dashboard").text


def test_build_stem_section_empty_without_dict_or_title():
    assert _build_stem_section(None) == ""
    assert _build_stem_section({}) == ""
    assert _build_stem_section({"title": "   "}) == ""


def test_build_stem_section_escapes_injected_markup():
    html = _build_stem_section(
        {
            "title": "<script>alert(1)</script>",
            "field": "Biology",
            "explanation": "x & y",
            "activities": [{"idea": "<b>build</b>", "audience": "kids"}],
        }
    )

    assert "STEM Concept of the Day" in html
    # Injected values are escaped, never rendered as raw markup.
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert "<b>build</b>" not in html
    assert "&lt;b&gt;build&lt;/b&gt;" in html


def test_build_stem_section_renders_and_filters_activities():
    html = _build_stem_section(
        {
            "title": "Buoyancy",
            "field": "Science",
            "explanation": "Some things float.",
            "activities": [
                {"idea": "Float toys in the tub", "audience": "kids"},
                "not a dict",  # skipped
                {"idea": "   "},  # empty idea skipped
                {"idea": "Guess sink or float"},  # no audience
            ],
        }
    )

    assert "Float toys in the tub" in html
    assert "kids" in html
    assert "Guess sink or float" in html
    assert "not a dict" not in html


def test_dashboard_renders_schedule_notes_and_briefing(client, db_session):
    data = {
        "greeting": "Morning!",
        "weather_summary": "Sunny",
        "briefing": "Pack an umbrella",
        "schedule": [
            {"time": "8:00 AM", "title": "School", "notes": "early release"},
            {"time": "9:00 AM", "title": "Gym"},
        ],
    }
    db_session.add(DashboardSnapshot(date="2026-03-15", data=data, is_active=True))
    db_session.commit()

    html = client.get("/dashboard").text

    assert "early release" in html  # schedule item notes
    assert "Pack an umbrella" in html  # briefing section
    assert "School" in html
    assert "Gym" in html
