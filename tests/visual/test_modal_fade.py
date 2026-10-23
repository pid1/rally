"""The scroll fade over a modal's content (static/modal.js, .modal-scroll).

.modal-body reaches var(--space-1) past .modal-scroll on the left and at the
bottom, to give focus rings room. The fade has to cover that strip too, or a
field cut by the fade shows its side borders beneath it.
"""

from __future__ import annotations

# Short enough that Add Calendar's CalDAV fields overflow.
VIEWPORT = {"width": 1280, "height": 600}


def test_the_bottom_fade_covers_the_whole_scroll_box(browser, live_server):
    context = browser.new_context(viewport=VIEWPORT)
    page = context.new_page()
    try:
        page.goto(f"{live_server}/settings", wait_until="networkidle")
        page.evaluate("() => openAddCalendarModal()")
        page.select_option("#calendar-type", "caldav_google")
        page.wait_for_selector("#calendar-modal-overlay .modal-scroll[data-overflow-bottom]")
        edges = page.evaluate(
            """() => {
                const scroll = document.querySelector('#calendar-modal-overlay .modal-scroll');
                const body = scroll.querySelector('.modal-body').getBoundingClientRect();
                const box = scroll.getBoundingClientRect();
                const fade = getComputedStyle(scroll, '::after');
                return {
                    fadeLeft: box.left + parseFloat(fade.left),
                    fadeBottom: box.bottom - parseFloat(fade.bottom),
                    bodyLeft: body.left,
                    bodyBottom: body.bottom,
                };
            }"""
        )
        assert edges["fadeLeft"] <= edges["bodyLeft"], edges
        assert edges["fadeBottom"] >= edges["bodyBottom"], edges
    finally:
        context.close()
