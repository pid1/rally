"""Tests for the dashboard router: snapshot rendering and the STEM card builder."""

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


def test_api_dashboard_without_snapshot(client):
    body = client.get("/api/dashboard").json()

    assert body["has_snapshot"] is False
    assert body["generated_at"] is None
    assert "No dashboard data available" in body["greeting"]
    assert body["schedule"] == []
    assert body["note"] is None


def test_api_dashboard_returns_snapshot_fields(client, db_session):
    db_session.add(
        DashboardSnapshot(
            date="2026-03-15",
            data={
                "greeting": "Hello Fam",
                "weather_summary": "Sunny",
                "schedule": [
                    {"time": "9:00 AM", "title": "Dentist", "notes": "Bring forms"},
                    {"time": "1:00 PM", "title": "Lunch"},
                    "not a dict",
                ],
                "briefing": "Trash day",
                "stem_concept": {
                    "title": "Buoyancy",
                    "field": "Science",
                    "explanation": "Things float.",
                    "activities": [{"idea": "Tub toys", "audience": "kids"}, {"idea": " "}, 3],
                },
            },
            is_active=True,
        )
    )
    db_session.commit()

    body = client.get("/api/dashboard").json()

    assert body["has_snapshot"] is True
    assert body["generated_at"].endswith("Z") or "+00:00" in body["generated_at"]
    assert body["greeting"] == "Hello Fam"
    assert body["weather_summary"] == "Sunny"
    assert body["briefing"] == "Trash day"
    assert body["schedule"] == [
        {"time": "9:00 AM", "title": "Dentist", "notes": "Bring forms"},
        {"time": "1:00 PM", "title": "Lunch", "notes": ""},
    ]
    assert body["stem_concept"]["title"] == "Buoyancy"
    assert body["stem_concept"]["activities"] == [{"idea": "Tub toys", "audience": "kids"}]


def test_api_dashboard_omits_stem_without_title(client, db_session):
    db_session.add(
        DashboardSnapshot(
            date="2026-03-15", data={"greeting": "Hi", "stem_concept": {}}, is_active=True
        )
    )
    db_session.commit()

    assert client.get("/api/dashboard").json()["stem_concept"] is None


def test_api_dashboard_reads_todays_note_live(client, db_session):
    from rally.utils.settings import today_local_str

    db_session.add(DashboardSnapshot(date="2026-03-15", data={"greeting": "Hi"}, is_active=True))
    db_session.commit()
    assert client.get("/api/dashboard").json()["note"] is None

    resp = client.post(
        "/api/notes", json={"date": today_local_str(db_session), "body": "**Pizza**"}
    )
    assert resp.status_code == 201

    note = client.get("/api/dashboard").json()["note"]
    assert note["body"] == "**Pizza**"
    assert "<strong>Pizza</strong>" in note["body_html"]
