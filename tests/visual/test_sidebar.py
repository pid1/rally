"""The site sidebar, driven in a real browser (issue #239).

What matters here cannot be read off the HTML: that the menu button is where
the design says at each width, that the sidebar slides in over a page that
stays scrollable, and that a tap outside it closes it *and goes no further* —
the checkbox under that tap must not change.
"""

from __future__ import annotations

import pytest

from .conftest import VIEWPORTS

TARGET_MIN = 44.0


@pytest.fixture
def open_page(browser, live_server):
    contexts = []

    def _open(path: str, viewport: str):
        width, height = VIEWPORTS[viewport]
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


@pytest.mark.parametrize("viewport", ["tablet", "desktop"])
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
    # The wordmark stays centred in the header; the button does not push it.
    text = page.evaluate(
        "() => { const r = document.createRange();"
        " r.selectNodeContents(document.querySelector('.header h1'));"
        " const b = r.getBoundingClientRect(); return b.x + b.width / 2; }"
    )
    assert abs(text - (header["x"] + header["width"] / 2)) < 2


@pytest.mark.parametrize("viewport", sorted(VIEWPORTS))
def test_opens_from_the_right_and_closes_on_escape(open_page, viewport):
    page = open_page("/todo", viewport)
    width = VIEWPORTS[viewport][0]
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
    page = open_page("/settings", "desktop")
    page.click("[data-sidebar-toggle]")
    _wait_settled(page)
    start = page.evaluate("window.scrollY")
    # Wheel over the dimmed page, left of the sidebar.
    page.mouse.move(200, 400)
    page.mouse.wheel(0, 600)
    page.wait_for_timeout(300)
    assert page.evaluate("window.scrollY") > start
    assert _is_open(page), "scrolling must not close the sidebar"


def test_the_toggle_closes_it_again_where_it_is_reachable(open_page):
    page = open_page("/calendar", "desktop")
    page.click("[data-sidebar-toggle]")
    _wait_settled(page)
    assert page.get_attribute("[data-sidebar-toggle]", "aria-expanded") == "true"
    # The dim layer covers the button, so a click there is a click outside:
    # it closes either way.
    page.mouse.click(*_centre(page.locator("[data-sidebar-toggle]").bounding_box()))
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


def _centre(box):
    return box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
