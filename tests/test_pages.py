"""Smoke tests for the HTML page routes, redirects, and the no-cache static mount."""

import pytest


@pytest.mark.parametrize(
    "path",
    ["/todo", "/todo/completed", "/dinner-planner", "/meal-history", "/settings"],
)
def test_page_renders_html(client, path):
    resp = client.get(path)
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")


def test_root_redirects_to_dashboard(client):
    resp = client.get("/", follow_redirects=False)
    assert resp.status_code in (307, 308)
    assert resp.headers["location"] == "/dashboard"


def test_meal_planner_redirects_to_dinner_planner(client):
    resp = client.get("/meal-planner", follow_redirects=False)
    assert resp.status_code in (307, 308)
    assert resp.headers["location"] == "/dinner-planner"


def test_static_css_sets_no_cache(client):
    resp = client.get("/static/styles.css")
    assert resp.status_code == 200
    assert resp.headers["cache-control"] == "no-cache"
