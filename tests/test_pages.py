"""Smoke tests for the HTML page routes, redirects, and the no-cache static mount."""

import pytest


@pytest.mark.parametrize(
    "path",
    [
        "/todo",
        "/todo/completed",
        "/shopping",
        "/shopping/purchased",
        "/dinner-planner",
        "/meal-history",
        "/settings",
    ],
)
def test_page_renders_html(client, path):
    resp = client.get(path)
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")


@pytest.mark.parametrize(
    "path",
    [
        "/dashboard",
        "/todo",
        "/todo/completed",
        "/shopping",
        "/shopping/purchased",
        "/dinner-planner",
        "/meal-history",
        "/settings",
    ],
)
def test_nav_links_to_shopping(client, path):
    """The nav is duplicated across templates, so every page must carry the link."""
    assert 'href="/shopping"' in client.get(path).text


@pytest.mark.parametrize("path", ["/dinner-planner", "/meal-history"])
def test_meal_pages_include_shared_edit_modal(client, path):
    """Both meal pages render the shared edit modal partial and load its JS, so
    the edit experience has a single source of truth."""
    html = client.get(path).text
    assert 'id="modal-overlay"' in html  # markup from _meal_edit_modal.html
    assert 'id="plan-form"' in html
    assert "/static/meal_edit_modal.js" in html


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
