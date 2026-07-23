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
