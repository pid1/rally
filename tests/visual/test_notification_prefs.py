"""The notification blocks, measured in a real browser.

Settings is the longest page in the app and this change adds rows to it: a
read-only list in the Notifications section, two controls in Shopping List, and
one checkbox per kind inside the family member modal. The generic suite already
measures the page as a whole; these check the parts of it that only exist once
the fetches have resolved, which a static page probe cannot see.
"""

from __future__ import annotations

import pytest

from .conftest import VIEWPORTS

TARGET_MIN = 44.0


def _settings(browser, live_server, viewport: str):
    width, height = VIEWPORTS[viewport]
    context = browser.new_context(
        viewport={"width": width, "height": height},
        is_mobile=(viewport == "mobile"),
        has_touch=(viewport != "desktop"),
    )
    page = context.new_page()
    page.goto(live_server + "/settings", wait_until="networkidle")
    page.wait_for_selector("#notification-overview .editable-item")
    return context, page


@pytest.mark.parametrize("viewport", sorted(VIEWPORTS))
def test_what_rally_sends_lists_every_kind_without_overflowing(
    browser, live_server, viewport
):
    """Five rows of prose in a bordered container, at 390 as well as 1440."""
    context, page = _settings(browser, live_server, viewport)
    try:
        rows = page.eval_on_selector_all(
            "#notification-overview .editable-item",
            "els => els.map(e => e.getBoundingClientRect())",
        )
        assert len(rows) == 5

        overflow = page.evaluate(
            "() => document.documentElement.scrollWidth - document.documentElement.clientWidth"
        )
        assert overflow <= 0, f"the settings page scrolls sideways by {overflow}px"
    finally:
        context.close()


@pytest.mark.parametrize("viewport", sorted(VIEWPORTS))
def test_the_member_modal_switches_meet_the_touch_target(
    browser, live_server, viewport
):
    """One row per kind, each a full hit area — the rule that bites at 390px.

    Measured against the page's own ``--target-min`` rather than a literal 44,
    because the token is 44px on a coarse pointer and smaller on a desktop
    mouse. The point is that the switches take the standard size, not that they
    take one particular number of pixels.
    """
    context, page = _settings(browser, live_server, viewport)
    try:
        page.click("#btn-add-member")
        page.wait_for_selector("#member-notify-options label")

        # Resolved through a probe element rather than read off the custom
        # property: the token is declared in rem at one breakpoint and px at
        # another, and only layout knows what that is in pixels here.
        target = page.evaluate(
            "() => { const probe = document.createElement('div');"
            " probe.style.height = 'var(--target-min)';"
            " document.body.appendChild(probe);"
            " const height = probe.getBoundingClientRect().height;"
            " probe.remove(); return height; }"
        )
        heights = page.eval_on_selector_all(
            "#member-notify-options label",
            "els => els.map(e => e.getBoundingClientRect().height)",
        )
        assert len(heights) == 5
        assert all(h >= target - 0.5 for h in heights), (heights, target)
        if viewport == "mobile":
            assert target >= TARGET_MIN
    finally:
        context.close()


def test_the_switches_are_held_and_explained_without_a_key(browser, live_server):
    """A member with no Pushover key gets disabled controls and a reason."""
    context, page = _settings(browser, live_server, "desktop")
    try:
        page.click("#btn-add-member")
        page.wait_for_selector("#member-notify-options input")

        disabled = page.eval_on_selector_all(
            "#member-notify-options input", "els => els.every(e => e.disabled)"
        )
        assert disabled is True
        assert "Pushover user key" in page.inner_text("#member-notify-help")

        page.fill("#member-pushover-key", "uQiRzpo4DXghDmr9QzzfQu27cmVRsG")
        enabled = page.eval_on_selector_all(
            "#member-notify-options input", "els => els.every(e => !e.disabled)"
        )
        assert enabled is True
    finally:
        context.close()
