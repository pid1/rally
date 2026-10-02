"""The page holds still behind an open modal, on every page that has one.

`modal.js` locks the document while any overlay is showing and releases it
when the last one closes. Without it a wheel or a finger over the dimmed
background scrolled the page behind — and on a phone, reaching the end of a
long form carried on into the page. These drive the real wheel, because the
lock is only real if an actual scroll gesture is what it stops.
"""

from __future__ import annotations

import pytest

# A short viewport, so every one of these pages is taller than it.
VIEWPORT = {"width": 1280, "height": 500}

# Page, the button that opens a modal, and the button that closes it. The
# meal planner closes its modal through its own class, which is why it is here.
MODALS = [
    ("/todo", "#btn-add-task", "#btn-cancel"),
    ("/meal-planner", "#btn-add-meal", "#btn-cancel"),
    ("/packing-lists", "[data-schedule]", "[data-cancel-schedule]"),
]


def _scroll_y(page) -> float:
    return page.evaluate("window.scrollY")


@pytest.mark.parametrize(("path", "opener", "closer"), MODALS)
def test_the_page_behind_an_open_modal_does_not_scroll(browser, live_server, path, opener, closer):
    context = browser.new_context(viewport=VIEWPORT)
    page = context.new_page()
    try:
        page.goto(live_server + path, wait_until="networkidle")
        page.evaluate("window.scrollTo(0, 120)")
        before = _scroll_y(page)
        page.locator(opener).first.evaluate("button => button.click()")
        page.wait_for_function("document.documentElement.classList.contains('modal-open')")

        # Over the dimmed background, well clear of the modal itself.
        page.mouse.move(8, VIEWPORT["height"] - 8)
        page.mouse.wheel(0, 600)
        page.wait_for_timeout(200)
        assert _scroll_y(page) == before

        page.locator(closer).first.click()
        page.wait_for_function("!document.documentElement.classList.contains('modal-open')")
        page.mouse.wheel(0, 600)
        page.wait_for_function(f"window.scrollY > {before}")
    finally:
        context.close()
