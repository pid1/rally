"""Smoke tests for the HTML page routes, redirects, and the no-cache static mount."""

import pathlib
import re

import pytest

TEMPLATES = pathlib.Path(__file__).resolve().parents[1] / "templates"


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
        "/calendar",
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


def test_modals_are_opened_only_through_the_shared_helper():
    """Setting `display` on an overlay by hand skips everything the helper does.

    `showModalOverlay()` resets the scroll position and settles the fade that
    signals more content below. Nineteen call sites across four templates set
    `.style.display` directly instead, so those modals reopened wherever they
    were last left — Add Item came back scrolled past its own first label — and
    their fade state was whatever it happened to be. The behavior has to be
    the same for every modal, which means one way in and one way out.
    """
    direct = re.compile(r"getElementById\(['\"][a-z-]*modal-overlay['\"]\)\.style\.display")
    offenders = [
        f"{path.name}:{i}"
        for path in sorted(TEMPLATES.glob("*.html"))
        for i, line in enumerate(path.read_text().splitlines(), 1)
        if direct.search(line)
    ]
    assert not offenders, (
        f"modals must open with showModalOverlay() and close with hideModalOverlay(): {offenders}"
    )


def test_calendar_offers_view_mode_and_range_as_separate_controls():
    """One dropdown could not say "a week, drawn as a calendar".

    `View` and `Range` are orthogonal — the renderer and the slice of time —
    and conflating them is why `Day` and `Week` both rendered agenda lists for
    as long as they existed. The two selectors are the whole feature, so the
    markup carrying them is worth pinning.
    """
    html = (TEMPLATES / "calendar.html").read_text()
    assert 'id="view-select"' in html
    assert 'id="range-select"' in html
    for option in ('value="calendar"', 'value="agenda"'):
        assert option in html, f"the View selector is missing {option}"
    for option in ('value="day"', 'value="week"', 'value="month"', 'value="rolling30"'):
        assert option in html, f"the Range selector is missing {option}"


def test_calendar_renders_a_time_grid_and_not_only_lists():
    """The grid is what a list cannot be: duration as height, overlap as position."""
    html = (TEMPLATES / "calendar.html").read_text()
    assert "function timeGridHtml()" in html
    assert "function layoutColumns(" in html, "overlapping events must split the column"
    assert "calendar-timegrid-allday" in html, "all-day events need their own band"


def test_calendar_agenda_iterates_the_selected_window():
    """The agenda looped a hard-coded 30 days regardless of the view, and only
    showed fewer for Day and Week as a side effect of the narrower fetch. With
    real ranges that is a warm cache away from rendering 30 headings on a day.
    """
    html = (TEMPLATES / "calendar.html").read_text()
    body = html.split("function agendaHtml()", 1)[1].split("function ", 1)[0]
    assert "rangeDayCount()" in body
    assert "AGENDA_DAYS" not in body, "the agenda must not loop a fixed day count"


def test_calendar_has_no_dead_viewport_override():
    """`applyViewportView()` was a stub returning False, still called from
    init() and wired to a matchMedia listener. Width picks the landing view and
    is not consulted again."""
    html = (TEMPLATES / "calendar.html").read_text()
    assert "applyViewportView" not in html


# Pages that show text a family member typed — a task's details, a note on a
# shopping item, an event's location. Each of those can carry a phone number.
FREE_TEXT_PAGES = [
    "/todo",
    "/todo/completed",
    "/shopping",
    "/shopping/purchased",
    "/preparedness",
    "/go-list",
    "/calendar",
]


@pytest.mark.parametrize("path", FREE_TEXT_PAGES)
def test_pages_that_show_family_text_load_the_phone_linker(client, path):
    """The helper is loaded in the head, before the inline script that calls it."""
    html = client.get(path).text
    assert "/static/phone_links.js" in html


def test_family_written_text_is_rendered_through_the_phone_linker():
    """A description, a note or a location is where a number to call gets written.

    Rendering one with a bare `escapeHtml()` puts the number on the screen as
    text, which on a phone means copying it out by hand to dial it. Every one
    of these fields goes through `escapeHtmlWithPhoneLinks()` instead — which
    escapes exactly the same way and marks up the numbers it finds — so a field
    added later cannot quietly lose its links.
    """
    plain = re.compile(
        r"\bescapeHtml\("
        r"(?:todo\.description|item\.note|item\.notes|occurrence\.(?:location|description))\)"
    )
    offenders = [
        f"{path.name}:{i}"
        for path in sorted(TEMPLATES.glob("*.html"))
        for i, line in enumerate(path.read_text().splitlines(), 1)
        if plain.search(line)
    ]
    assert not offenders, f"free text must render through escapeHtmlWithPhoneLinks(): {offenders}"


# --- Per-device behavioral defaults --------------------------------------------


@pytest.mark.parametrize("path", ["/calendar", "/settings"])
def test_pages_that_resolve_a_device_load_the_device_helper(client, path):
    """Both pages answer "which device is this, and whose?" the same way.

    Rally has no logins, so both halves are things only the browser knows. Two
    copies of that logic is how Settings and the calendar end up disagreeing
    about which device they are on.
    """
    assert "/static/device_member.js" in client.get(path).text


def test_the_calendar_is_server_rendered_with_the_behavior_catalog(client):
    """The landing view has to be decided before the first paint.

    Fetching the catalog would mean drawing the month grid and then swapping it
    for somebody's agenda — a flicker on a screen and a full repaint on e-ink.
    """
    html = client.get("/calendar").text
    assert "calendar_default_view" in html
    assert "function applyLandingView(" in html


def test_the_calendar_owns_what_auto_means(client):
    """`auto` names Rally's rule rather than a view, so only the page holding
    the rule can resolve it — and it must hold it in exactly one place."""
    html = client.get("/calendar").text
    assert "const NARROW = '(max-width: 767px)'" in html
    assert html.count("matchMedia(NARROW)") == 1


def test_settings_scopes_its_defaults_to_this_device(client):
    """One answer per person per setting, for the device in front of you, plus
    the list that makes the other devices visible and forgettable."""
    html = client.get("/settings").text
    assert 'id="member-prefs-list"' in html
    assert 'id="device-member"' in html
    assert 'id="device-label"' in html
    assert 'id="device-list"' in html
    assert "RallyDevice.deviceId()" in html
