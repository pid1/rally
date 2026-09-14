"""Per-device behavioral defaults, measured in a real browser.

The feature only exists end to end: a dropdown on Settings writes a row against
this browser's device token, and the calendar on that same browser lands
somewhere different because of it. Every layer of that has a unit test; none of
them can tell you whether the calendar actually opened on the agenda, which is
the only claim the feature makes.

The load-bearing case is two browser contexts at the **same width**. Each one
mints its own device token, so they are two devices by every measure except
screen size — which is exactly what the earlier phone-versus-computer cut could
not express, and what these have to prove.
"""

from __future__ import annotations

import pytest

from .conftest import VIEWPORTS

TARGET_MIN = 44.0


def _context(browser, viewport: str):
    width, height = VIEWPORTS[viewport]
    return browser.new_context(
        viewport={"width": width, "height": height},
        is_mobile=(viewport == "mobile"),
        has_touch=(viewport != "desktop"),
    )


def _settings(context, live_server):
    page = context.new_page()
    page.goto(live_server + "/settings", wait_until="networkidle")
    page.wait_for_selector("#member-prefs-list .editable-item")
    return page


def _claim_and_set(context, live_server, view: str) -> str:
    """Claim this browser for the first family member and pick their view.

    Returns the member's name, so a caller driving a second browser can claim
    it for the same person — which is what makes "two devices, one person" a
    real test rather than two unrelated ones.
    """
    page = _settings(context, live_server)
    name = page.eval_on_selector(
        "#member-prefs-list .editable-item .editable-item-title", "el => el.textContent.trim()"
    )
    page.select_option("#device-member", label=name)
    page.locator("#member-prefs-list select[data-pref-key]").first.select_option(view)
    page.wait_for_selector("#member-prefs-list [data-pref-status]:not(:empty)")
    page.close()
    return name


def _landing_view(context, live_server) -> tuple[str, str]:
    page = context.new_page()
    page.goto(live_server + "/calendar", wait_until="networkidle")
    page.wait_for_selector("#calendar-view")
    pair = (page.input_value("#view-select"), page.input_value("#range-select"))
    page.close()
    return pair


@pytest.mark.parametrize("viewport", sorted(VIEWPORTS))
def test_every_member_gets_one_answer_on_this_device(browser, live_server, viewport):
    """One row per person, one dropdown per setting, at 390 as well as 1440."""
    context = _context(browser, viewport)
    try:
        page = _settings(context, live_server)

        rows = page.locator("#member-prefs-list .editable-item")
        assert rows.count() >= 1

        keys = page.eval_on_selector_all(
            "#member-prefs-list .editable-item:first-child select[data-pref-key]",
            "els => els.map(e => e.dataset.prefKey)",
        )
        assert keys == ["calendar_default_view"]

        overflow = page.evaluate(
            "() => document.documentElement.scrollWidth - document.documentElement.clientWidth"
        )
        assert overflow <= 0, f"the settings page scrolls sideways by {overflow}px"
    finally:
        context.close()


@pytest.mark.parametrize("viewport", sorted(VIEWPORTS))
def test_the_dropdowns_meet_the_touch_target(browser, live_server, viewport):
    """Measured against the page's own `--target-min`, not a literal 44.

    The token is 44px on a coarse pointer and smaller on a desktop mouse; the
    point is that these take the standard size, not one number of pixels.
    """
    context = _context(browser, viewport)
    try:
        page = _settings(context, live_server)

        target = page.evaluate(
            "() => { const probe = document.createElement('div');"
            " probe.style.height = 'var(--target-min)';"
            " document.body.appendChild(probe);"
            " const height = probe.getBoundingClientRect().height;"
            " probe.remove(); return height; }"
        )
        heights = page.eval_on_selector_all(
            "#member-prefs-list select[data-pref-key]",
            "els => els.map(e => e.getBoundingClientRect().height)",
        )
        assert heights
        assert all(h >= target - 0.5 for h in heights), (heights, target)
        if viewport == "mobile":
            assert target >= TARGET_MIN
    finally:
        context.close()


def test_a_browser_announces_itself_and_appears_in_the_device_list(browser, live_server):
    """No enrollment step: opening Settings is enough to be listed and named."""
    context = _context(browser, "desktop")
    try:
        page = _settings(context, live_server)
        page.wait_for_selector("#device-list .editable-item")

        here = page.locator("#device-list .editable-item", has_text="this device")
        assert here.count() == 1
        assert page.input_value("#device-label").strip() != ""
    finally:
        context.close()


def test_renaming_this_device_sticks(browser, live_server):
    context = _context(browser, "desktop")
    try:
        page = _settings(context, live_server)
        page.wait_for_selector("#device-list .editable-item")

        page.fill("#device-label", "Kitchen tablet")
        page.locator("#device-label").blur()
        page.wait_for_selector("#device-list .editable-item:has-text('Kitchen tablet')")

        page.reload(wait_until="networkidle")
        page.wait_for_selector("#device-list .editable-item")
        assert page.input_value("#device-label") == "Kitchen tablet"
    finally:
        context.close()


def test_a_saved_answer_survives_a_reload(browser, live_server):
    """Saved on change, server-side, and read back the same way it was written."""
    context = _context(browser, "desktop")
    try:
        page = _settings(context, live_server)
        select = page.locator("#member-prefs-list select[data-pref-key]").first
        select.select_option("agenda:week")
        page.wait_for_selector("#member-prefs-list [data-pref-status]:not(:empty)")

        page.reload(wait_until="networkidle")
        page.wait_for_selector("#member-prefs-list .editable-item")
        reloaded = page.locator("#member-prefs-list select[data-pref-key]").first
        assert reloaded.input_value() == "agenda:week"
    finally:
        context.close()


def test_an_unconfigured_device_lands_where_rally_always_landed(browser, live_server):
    """`auto`, which is what every device gets until somebody says otherwise."""
    desktop = _context(browser, "desktop")
    try:
        assert _landing_view(desktop, live_server) == ("calendar", "month")
    finally:
        desktop.close()

    phone = _context(browser, "mobile")
    try:
        assert _landing_view(phone, live_server) == ("calendar", "day")
    finally:
        phone.close()


def test_two_devices_of_the_same_width_keep_different_answers(browser, live_server):
    """The claim the old phone-versus-computer split could not make.

    Two browser contexts at 1440px: same person, same screen size, two device
    tokens. Each opens on the view configured on *it*, which is only possible
    because the answer hangs on the device rather than on a media query.
    """
    laptop = _context(browser, "desktop")
    kitchen = _context(browser, "desktop")
    try:
        name = _claim_and_set(laptop, live_server, "calendar:week")
        other = _claim_and_set(kitchen, live_server, "agenda:rolling30")
        assert name == other, "both devices must be claimed by the same person"

        assert _landing_view(laptop, live_server) == ("calendar", "week")
        assert _landing_view(kitchen, live_server) == ("agenda", "rolling30")
    finally:
        laptop.close()
        kitchen.close()


def test_choosing_auto_hands_the_decision_back_to_the_screen(browser, live_server):
    """ "Let Rally pick" is an answer somebody can give back after giving another."""
    context = _context(browser, "desktop")
    try:
        _claim_and_set(context, live_server, "agenda:day")
        assert _landing_view(context, live_server) == ("agenda", "day")

        page = _settings(context, live_server)
        page.locator("#member-prefs-list select[data-pref-key]").first.select_option("auto")
        page.wait_for_selector("#member-prefs-list [data-pref-status]:not(:empty)")
        page.close()

        assert _landing_view(context, live_server) == ("calendar", "month")
    finally:
        context.close()


def test_an_unclaimed_device_ignores_answers_stored_on_it(browser, live_server):
    """Both halves of the key are required. A screen that belongs to nobody
    resolves to `auto` even when somebody's answers are sitting on it."""
    context = _context(browser, "desktop")
    try:
        _claim_and_set(context, live_server, "agenda:rolling30")

        page = _settings(context, live_server)
        page.select_option("#device-member", "")
        page.wait_for_timeout(200)
        page.close()

        assert _landing_view(context, live_server) == ("calendar", "month")
    finally:
        context.close()


def test_forgetting_a_device_clears_what_it_remembered(browser, live_server):
    """And the browser carries on — a device with no settings, not a broken page."""
    context = _context(browser, "desktop")
    try:
        _claim_and_set(context, live_server, "agenda:week")
        assert _landing_view(context, live_server) == ("agenda", "week")

        page = _settings(context, live_server)
        page.wait_for_selector("#device-list .editable-item")
        page.on("dialog", lambda dialog: dialog.accept())
        page.locator("#device-list .editable-item", has_text="this device").locator(
            "button", has_text="Forget"
        ).click()
        page.wait_for_selector("#member-prefs-list select[data-pref-key][data-saved='auto']")
        page.close()

        assert _landing_view(context, live_server) == ("calendar", "month")
    finally:
        context.close()
