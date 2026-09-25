"""The shared base layout and the site sidebar (issue #239).

Every page extends ``templates/base.html``, which owns the ``<head>``, the
wordmark header, the menu button and the sidebar. These tests pin the contract
the pages depend on: which links the sidebar carries and in what order, which
one each page marks as current, the ``Rally — <page>`` title, and that the
footer survives only on the Dashboard.
"""

import pathlib
import re

import pytest

from rally.models import DashboardSnapshot, Note
from rally.utils.settings import today_local_str

TEMPLATES = pathlib.Path(__file__).resolve().parents[1] / "templates"

SIDEBAR_ORDER = [
    ("/dashboard", "Dashboard"),
    ("/todo", "Tasks"),
    ("/shopping", "Shopping"),
    ("/calendar", "Calendar"),
    ("/notes", "Notes"),
    ("/dinner-planner", "Meal Planner"),
    ("/meal-history", "Previous Meals"),
    ("/preparedness", "Preparedness"),
    ("/settings", "Settings"),
]

# path -> (title after "Rally — ", sidebar href marked current or None)
PAGES = {
    "/todo": ("Tasks", "/todo"),
    "/todo/completed": ("Tasks", "/todo"),
    "/shopping": ("Shopping List", "/shopping"),
    "/shopping/purchased": ("Purchased Items", "/shopping"),
    "/calendar": ("Calendar", "/calendar"),
    "/notes": ("Notes", "/notes"),
    "/notes/previous": ("Previous Notes", "/notes"),
    "/dinner-planner": ("Meal Planner", "/dinner-planner"),
    "/meal-history": ("Meal History", "/meal-history"),
    "/preparedness": ("Preparedness", "/preparedness"),
    "/go-list": ("Go List", "/preparedness"),
    "/settings": ("Settings", "/settings"),
    "/styleguide": ("Style Guide", None),
}

ALL_PATHS = ["/dashboard", *PAGES]


def _sidebar(html: str) -> str:
    start = html.index('<nav class="sidebar"')
    return html[start : html.index("</nav>", start)]


def _links(sidebar: str) -> list[tuple[str, str, bool]]:
    return [
        (href, label, bool(current))
        for href, current, label in re.findall(
            r'<a href="([^"]+)"( aria-current="page")?>([^<]+)</a>', sidebar
        )
    ]


@pytest.mark.parametrize("path", ALL_PATHS)
def test_every_page_carries_the_sidebar_in_order(client, path):
    """Nine links, the order they had across the row and the dropdown, with
    Settings moved up from the footer to the end."""
    html = client.get(path).text
    links = _links(_sidebar(html))
    assert [(href, label) for href, label, _ in links] == SIDEBAR_ORDER, path


@pytest.mark.parametrize("path", ALL_PATHS)
def test_settings_sits_alone_below_the_divider(client, path):
    sidebar = _sidebar(client.get(path).text)
    lists = sidebar.split('<ul class="sidebar-list">')[1:]
    assert len(lists) == 2
    assert 'href="/settings"' in lists[1]
    assert lists[1].count("<a ") == 1


@pytest.mark.parametrize("path", ALL_PATHS)
def test_the_old_nav_is_gone(client, path):
    html = client.get(path).text
    for gone in ("other-dropdown", "nav-dropdown", "toggleOtherDropdown", "<nav>"):
        assert gone not in html, (path, gone)


@pytest.mark.parametrize("path", ALL_PATHS)
def test_every_page_has_one_menu_button_and_loads_the_sidebar_script(client, path):
    html = client.get(path).text
    assert html.count("data-sidebar-toggle") == 1
    assert 'aria-label="Open menu"' in html
    assert 'aria-controls="site-sidebar"' in html
    assert "/static/sidebar.js" in html


@pytest.mark.parametrize(("path", "expected"), [(p, v[1]) for p, v in PAGES.items()])
def test_each_page_marks_itself_or_its_parent(client, path, expected):
    current = [href for href, _, cur in _links(_sidebar(client.get(path).text)) if cur]
    assert current == ([expected] if expected else []), path


def test_the_dashboard_marks_itself(client):
    current = [href for href, _, cur in _links(_sidebar(client.get("/dashboard").text)) if cur]
    assert current == ["/dashboard"]


@pytest.mark.parametrize(("path", "title"), [(p, v[0]) for p, v in PAGES.items()])
def test_titles_use_the_em_dash(client, path, title):
    assert f"<title>Rally — {title}</title>" in client.get(path).text


@pytest.mark.parametrize("path", ["/todo", "/todo/completed"])
def test_tasks_subtitle_says_task(client, path):
    html = client.get(path).text
    assert '<div class="subtitle">Task Management</div>' in html
    assert "Todo Management" not in html


@pytest.mark.parametrize("path", list(PAGES))
def test_only_the_dashboard_has_a_footer(client, path):
    assert "<footer" not in client.get(path).text


def test_every_page_template_extends_the_base_layout():
    """The header and nav have one source of truth; a template with its own
    <head> would be a copy of it drifting."""
    for path in TEMPLATES.glob("*.html"):
        if path.name in {"base.html"} or path.name.startswith("_"):
            continue
        text = path.read_text()
        assert text.startswith('{% extends "base.html" %}'), path.name
        assert "<head>" not in text and "<!DOCTYPE" not in text, path.name


# ── The Dashboard, now on Jinja ──────────────────────────────────────────────


def test_dashboard_without_a_snapshot_renders_the_error_state(client):
    html = client.get("/dashboard").text
    assert "No dashboard data available for today." in html
    assert "<title>Rally — " in html
    assert '<div class="subtitle" id="current-date">' in html


def test_dashboard_inserts_snapshot_html_unescaped(client, db_session):
    """The cards are built as HTML strings; autoescaping them would show the
    family raw tags. Values the LLM returned are inserted verbatim, as before."""
    db_session.add(
        DashboardSnapshot(
            date="2026-09-25",
            data={
                "greeting": "Good <em>morning</em>",
                "weather_summary": "Sunny, <strong>72°</strong>",
                "schedule": [{"time": "9:00 AM", "title": "Standup", "notes": "Room <b>4</b>"}],
                "briefing": "<p>Big day.</p>",
                "stem_concept": {"title": "Levers", "field": "Physics"},
            },
            is_active=True,
        )
    )
    db_session.add(Note(date=today_local_str(db_session), body="**Pack lunches**"))
    db_session.commit()

    html = client.get("/dashboard").text
    assert '<div class="greeting">Good <em>morning</em></div>' in html
    assert "Sunny, <strong>72°</strong>" in html
    assert '<div class="schedule-notes">Room <b>4</b></div>' in html
    assert '<div class="briefing-title">The Briefing</div><p>Big day.</p>' in html
    assert '<div class="stem-title">Levers</div>' in html
    assert "<strong>Pack lunches</strong>" in html
    assert "&lt;" not in html
    # The ISO timestamp still lands inside the page script's string literal.
    assert re.search(r"const utcTimestamp = '\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ';", html)
    # The footer keeps only its timestamp.
    footer = html[html.index("<footer>") : html.index("</footer>")]
    assert "Last updated" in footer and "href" not in footer
