"""The site sidebar, driven in a real browser (issue #239).

What matters here cannot be read off the HTML: that the menu button is where
the design says at each width, that the sidebar slides in over a page that
stays scrollable, that a tap outside it closes it *and goes no further* — the
checkbox under that tap must not change — and that where there is room beside
the page column the sidebar is simply docked open, covering nothing.
"""

from __future__ import annotations

import pytest

from .conftest import VIEWPORTS

TARGET_MIN = 44.0

# Viewports on which the sidebar is an overlay behind the menu button: a phone,
# a tablet, and a laptop window too narrow to fit the column and the sidebar
# side by side. At "desktop" (1440) it is docked.
OVERLAY = {**VIEWPORTS, "laptop-narrow": (1100, 800)}
del OVERLAY["desktop"]


@pytest.fixture
def open_page(browser, live_server):
    contexts = []

    def _open(path: str, viewport: str):
        width, height = {**VIEWPORTS, **OVERLAY}[viewport]
        ctx = browser.new_context(
            viewport={"width": width, "height": height},
            is_mobile=(viewport == "mobile"),
            has_touch=(viewport != "desktop"),
        )
        contexts.append(ctx)
        page = ctx.new_page()
        page.goto(live_server + path, wait_until="networkidle")
        return page

    yield _open
    for ctx in contexts:
        ctx.close()


def _is_open(page) -> bool:
    return page.evaluate("document.body.classList.contains('sidebar-open')")


def _wait_settled(page):
    # The slide is 0.2s; wait it out so geometry is measured at rest.
    page.wait_for_timeout(300)


def test_phone_button_floats_in_the_bottom_right_corner(open_page):
    page = open_page("/shopping", "mobile")
    width, height = VIEWPORTS["mobile"]
    box = page.locator("[data-sidebar-toggle]").bounding_box()
    assert box["width"] >= TARGET_MIN and box["height"] >= TARGET_MIN
    assert width - (box["x"] + box["width"]) < 32
    assert height - (box["y"] + box["height"]) < 32
    assert (
        page.eval_on_selector("[data-sidebar-toggle]", "el => getComputedStyle(el).position")
        == "fixed"
    )
    # It stays put when the page scrolls.
    page.mouse.wheel(0, 400)
    page.wait_for_timeout(100)
    assert page.locator("[data-sidebar-toggle]").bounding_box()["y"] == box["y"]


@pytest.mark.parametrize("viewport", ["tablet", "laptop-narrow"])
def test_wide_button_sits_at_the_headers_right_edge(open_page, viewport):
    page = open_page("/dashboard", viewport)
    header = page.locator(".header").bounding_box()
    box = page.locator("[data-sidebar-toggle]").bounding_box()
    assert box["width"] >= 36 and box["height"] >= 36
    assert abs((box["x"] + box["width"]) - (header["x"] + header["width"])) < 1
    assert box["y"] < header["y"] + header["height"]
    assert (
        page.eval_on_selector("[data-sidebar-toggle]", "el => getComputedStyle(el).borderTopStyle")
        == "none"
    )
    # The wordmark stays centered in the header; the button does not push it.
    text = page.evaluate(
        "() => { const r = document.createRange();"
        " r.selectNodeContents(document.querySelector('.header h1'));"
        " const b = r.getBoundingClientRect(); return b.x + b.width / 2; }"
    )
    assert abs(text - (header["x"] + header["width"] / 2)) < 2


@pytest.mark.parametrize("viewport", sorted(OVERLAY))
def test_opens_from_the_right_and_closes_on_escape(open_page, viewport):
    page = open_page("/todo", viewport)
    width = OVERLAY[viewport][0]
    page.click("[data-sidebar-toggle]")
    _wait_settled(page)
    assert _is_open(page)
    sidebar = page.locator("#site-sidebar").bounding_box()
    assert abs(sidebar["x"] + sidebar["width"] - width) < 1
    # A strip of the page stays visible to tap on a phone.
    assert sidebar["x"] >= 48
    # Focus lands on the current page's link.
    assert page.evaluate("document.activeElement.getAttribute('href')") == "/todo"
    page.keyboard.press("Escape")
    _wait_settled(page)
    assert not _is_open(page)
    assert page.evaluate("document.activeElement.hasAttribute('data-sidebar-toggle')")


def test_a_tap_outside_closes_it_and_reaches_nothing(open_page):
    page = open_page("/todo", "mobile")
    checkbox = page.locator(".todo-checkbox input[type=checkbox]").first
    checkbox.scroll_into_view_if_needed()
    before = checkbox.is_checked()
    box = checkbox.bounding_box()

    page.click("[data-sidebar-toggle]")
    _wait_settled(page)
    assert _is_open(page)
    sidebar_x = page.locator("#site-sidebar").bounding_box()["x"]
    assert box["x"] + box["width"] / 2 < sidebar_x, "the checkbox must sit in the visible strip"

    page.touchscreen.tap(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    _wait_settled(page)
    assert not _is_open(page)
    assert checkbox.is_checked() == before, "the closing tap reached the page"


def test_the_page_scrolls_behind_the_open_sidebar(open_page):
    page = open_page("/settings", "laptop-narrow")
    page.click("[data-sidebar-toggle]")
    _wait_settled(page)
    start = page.evaluate("window.scrollY")
    # Wheel over the dimmed page, left of the sidebar.
    page.mouse.move(200, 400)
    page.mouse.wheel(0, 600)
    page.wait_for_timeout(300)
    assert page.evaluate("window.scrollY") > start
    assert _is_open(page), "scrolling must not close the sidebar"


def test_a_finger_drag_scrolls_the_page_behind_the_open_sidebar(open_page):
    """The wheel test above, with a finger. On a phone the strip left of the
    sidebar is all scrim, and a drag that starts there must scroll the page
    rather than be swallowed by it, or close the sidebar as a tap would."""
    page = open_page("/settings", "mobile")
    page.click("[data-sidebar-toggle]")
    _wait_settled(page)
    x = page.locator("#site-sidebar").bounding_box()["x"] / 2
    y = VIEWPORTS["mobile"][1] * 0.75
    assert page.evaluate(
        f"document.elementFromPoint({x}, {y}).hasAttribute('data-sidebar-scrim')"
    ), "the drag must start on the scrim"
    start = page.evaluate("window.scrollY")
    # A real finger through Chromium's input pipeline, not a scrollBy(): raw
    # touch events are hit-tested like a finger, so the drag lands on the
    # scrim. Raw events rather than Input.synthesizeScrollGesture, which
    # scrolls on macOS but does nothing in headless Chromium on Linux (CI).
    cdp = page.context.new_cdp_session(page)

    def touch(kind, at_y=None):
        points = [] if at_y is None else [{"x": x, "y": at_y}]
        cdp.send("Input.dispatchTouchEvent", {"type": kind, "touchPoints": points})

    touch("touchStart", y)
    for step in range(1, 21):  # upward, 400px in 20px steps: scrolls the page down
        touch("touchMove", y - step * 20)
    touch("touchEnd")
    page.wait_for_function(f"window.scrollY > {start}", timeout=3000)
    assert _is_open(page), "a drag must not close the sidebar"


def test_the_toggle_closes_it_again_where_it_is_reachable(open_page):
    page = open_page("/calendar", "laptop-narrow")
    page.click("[data-sidebar-toggle]")
    _wait_settled(page)
    assert page.get_attribute("[data-sidebar-toggle]", "aria-expanded") == "true"
    # The dim layer covers the button, so a click there is a click outside:
    # it closes either way.
    page.mouse.click(*_center(page.locator("[data-sidebar-toggle]").bounding_box()))
    _wait_settled(page)
    assert not _is_open(page)
    assert page.get_attribute("[data-sidebar-toggle]", "aria-expanded") == "false"


def test_a_link_navigates(open_page):
    page = open_page("/preparedness", "mobile")
    page.click("[data-sidebar-toggle]")
    _wait_settled(page)
    page.click("#site-sidebar a[href='/settings']")
    page.wait_for_url("**/settings")
    assert page.get_attribute("#site-sidebar a[aria-current='page']", "href") == "/settings"


def test_links_meet_the_target_size_and_the_sidebar_scrolls(open_page):
    page = open_page("/dashboard", "mobile")
    page.set_viewport_size({"width": 390, "height": 300})
    page.click("[data-sidebar-toggle]")
    _wait_settled(page)
    for box in (
        page.locator("#site-sidebar a").nth(i).bounding_box()
        for i in range(page.locator("#site-sidebar a").count())
    ):
        assert box["height"] >= TARGET_MIN
    overflow = page.eval_on_selector(
        "#site-sidebar", "el => [el.scrollHeight > el.clientHeight, getComputedStyle(el).overflowY]"
    )
    assert overflow == [True, "auto"]


def test_hidden_in_print(open_page):
    page = open_page("/go-list", "desktop")
    page.emulate_media(media="print")
    for sel in ("[data-sidebar-toggle]", "#site-sidebar", "[data-sidebar-scrim]"):
        assert page.eval_on_selector(sel, "el => getComputedStyle(el).display") == "none"


@pytest.mark.parametrize("path", ["/dashboard", "/calendar", "/settings"])
def test_docked_open_beside_the_page_where_there_is_room(open_page, path):
    """At 1440 the column and the sidebar fit side by side, so the sidebar is
    on the page without a click, covers nothing, and there is no button."""
    page = open_page(path, "desktop")
    width = VIEWPORTS["desktop"][0]
    sidebar = page.locator("#site-sidebar").bounding_box()
    assert page.locator("#site-sidebar").is_visible()
    assert abs(sidebar["x"] + sidebar["width"] - width) < 1
    assert not page.locator("[data-sidebar-toggle]").is_visible()
    assert not page.locator("[data-sidebar-scrim]").is_visible()
    body = page.locator("body").bounding_box()
    assert body["x"] + body["width"] <= sidebar["x"] + 1, "the sidebar covers the page"
    # The column centers in the space left of the sidebar.
    left_gap = body["x"]
    right_gap = sidebar["x"] - (body["x"] + body["width"])
    assert abs(left_gap - right_gap) < 2
    assert page.get_attribute("#site-sidebar a[aria-current='page']", "href") == path


def test_docked_page_still_scrolls_and_links_navigate(open_page):
    page = open_page("/settings", "desktop")
    page.mouse.move(400, 400)
    page.mouse.wheel(0, 600)
    page.wait_for_timeout(300)
    assert page.evaluate("window.scrollY") > 0
    page.click("#site-sidebar a[href='/todo']")
    page.wait_for_url("**/todo")
    assert page.locator("#site-sidebar").is_visible()


def test_resizing_moves_between_overlay_and_docked(open_page):
    page = open_page("/dashboard", "laptop-narrow")
    page.click("[data-sidebar-toggle]")
    _wait_settled(page)
    assert _is_open(page)
    # Wider: docked, and the overlay's open state is dropped with its button.
    page.set_viewport_size({"width": 1440, "height": 800})
    page.wait_for_timeout(200)
    assert not _is_open(page)
    assert page.locator("#site-sidebar").is_visible()
    assert not page.locator("[data-sidebar-scrim]").is_visible()
    # Narrower again: back behind the button, closed.
    page.set_viewport_size({"width": 1100, "height": 800})
    _wait_settled(page)
    assert not page.locator("#site-sidebar").is_visible()
    assert page.locator("[data-sidebar-toggle]").is_visible()


def _center(box):
    return box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
