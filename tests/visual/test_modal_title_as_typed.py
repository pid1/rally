"""A modal heading that is entirely the user's own text reads as it was typed (#334).

Every h1/h2/h3 is uppercase and widely tracked by the base rule. The calendar's
event detail view is headed by the event's title, so `.modal-title--as-typed`
keeps that title's casing and tightens the tracking to `--tracking-normal`. A
heading of Rally's own words, like the edit modal's, stays uppercase.
"""

from __future__ import annotations

import pytest

OPEN_JS = """
(title) => {
    openDetail({
        uid: 'u', source: 'native', title, description: '', description_html: '',
        location: '', all_day: false, start_date: '2026-10-03', end_date: '2026-10-03',
        time_label: '5:30 PM', end_time_label: '6:30 PM', attendees: [], member: null,
        calendar_label: 'Emma', editable: false, recurring: false,
    });
}
"""

STYLE_JS = """
(selector) => {
    const style = getComputedStyle(document.querySelector(selector));
    const probe = document.createElement('span');
    probe.style.letterSpacing = 'var(--tracking-normal)';
    probe.style.fontSize = style.fontSize;
    document.body.appendChild(probe);
    const tokenSpacing = getComputedStyle(probe).letterSpacing;
    probe.remove();
    return {
        transform: style.textTransform,
        spacing: style.letterSpacing,
        tokenSpacing,
    };
}
"""


@pytest.fixture
def calendar(browser, live_server):
    context = browser.new_context()
    page = context.new_page()
    page.goto(f"{live_server}/calendar")
    page.wait_for_function("typeof openDetail === 'function'")
    try:
        yield page
    finally:
        context.close()


def test_the_detail_title_reads_as_typed(calendar):
    calendar.evaluate(OPEN_JS, "iPad repair w/ Mr. McKay")
    title = calendar.locator("#detail-modal-title")
    assert title.inner_text() == "iPad repair w/ Mr. McKay"
    style = calendar.evaluate(STYLE_JS, "#detail-modal-title")
    assert style["transform"] == "none"
    assert style["spacing"] == style["tokenSpacing"], "tracked at --tracking-normal"


def test_a_heading_of_rallys_own_words_stays_uppercase(calendar):
    style = calendar.evaluate(STYLE_JS, "#event-modal-title")
    assert style["transform"] == "uppercase"
    assert style["spacing"] != style["tokenSpacing"]
