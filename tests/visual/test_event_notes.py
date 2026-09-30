"""An event's Notes in the detail view, as a family sees them.

The converter's rules are pinned in tests/test_rich_text.py against strings.
What only a browser can answer is whether the result reads the way it should:
paragraphs are separate blocks, a link opens in a new tab and is underlined,
a phone number is still tappable, and a URL too long for the modal wraps
instead of pushing the modal wider than the screen.

The description HTML comes from the real renderer rather than being written by
hand here, so a change to what the server emits is what these see.
"""

from __future__ import annotations

import pytest

from rally import rich_text

SCHOOL = (
    '<p dir="auto">Early Inquiry is now open.</p>'
    '<p dir="auto">Submit the form to stay informed.</p>'
    '<p dir="auto">Early Inquiry Form:<br>'
    '<a href="https://www.ourschool.org/admissions/x">https://www.ourschool.org/admissions/x</a></p>'
    '<p dir="auto"></p>'
)

OPEN_JS = """
(description_html) => {
    openDetail({
        uid: 'u', source: 'ics', title: 'Open house', description: '', description_html,
        location: '', all_day: false, start_date: '2026-10-03', end_date: '2026-10-03',
        time_label: '5:30 PM', end_time_label: '6:30 PM', attendees: [], member: null,
        calendar_label: 'School', editable: false, recurring: false,
    });
}
"""


@pytest.fixture
def detail(browser, live_server):
    """Return `open(description, source='ics')` -> the page, with the detail view showing it."""
    context = browser.new_context()
    page = context.new_page()
    page.goto(f"{live_server}/calendar")
    page.wait_for_function("typeof openDetail === 'function'")

    def _open(description, source="ics"):
        page.evaluate(OPEN_JS, rich_text.render_description(description, source=source))
        page.wait_for_selector("#detail-value-notes")
        return page

    try:
        yield _open
    finally:
        context.close()


def test_the_school_description_reads_as_paragraphs_with_a_tappable_link(detail):
    page = detail(SCHOOL)
    paragraphs = page.query_selector_all("#detail-value-notes p")
    assert [p.inner_text().split("\n")[0] for p in paragraphs] == [
        "Early Inquiry is now open.",
        "Submit the form to stay informed.",
        "Early Inquiry Form:",
    ], "three paragraphs, and the empty trailing one leaves no gap"
    link = page.query_selector("#detail-value-notes a.inline-link")
    assert link.get_attribute("href") == "https://www.ourschool.org/admissions/x"
    assert link.get_attribute("target") == "_blank"
    assert link.get_attribute("rel") == "noopener noreferrer"
    style = link.evaluate("(el) => getComputedStyle(el).textDecorationLine")
    assert style == "underline"
    # Paragraphs are separate blocks, which is what a single escaped span was not.
    tops = [p.bounding_box()["y"] for p in paragraphs]
    assert tops == sorted(set(tops)), "each paragraph starts below the last"


def test_no_markup_from_the_feed_is_shown_as_text(detail):
    page = detail(SCHOOL)
    shown = page.inner_text("#detail-value-notes")
    assert "<" not in shown and "dir=" not in shown


def test_plain_text_keeps_its_paragraphs_and_links_its_url(detail):
    url = "https://www.example.com/events/2026-10-03/"
    page = detail(f"Doors open at 5:30pm\n\n{url}")
    assert len(page.query_selector_all("#detail-value-notes p")) == 2
    assert page.get_attribute("#detail-value-notes a", "href") == url


def test_a_number_in_the_notes_is_still_a_phone_link(detail):
    page = detail("Bring cleats\n\nCoach: 800-555-0142", source="native")
    link = page.query_selector("#detail-value-notes a.phone-link")
    assert link is not None, "formatting the notes must not cost them their phone links"
    assert link.get_attribute("href") == "tel:+18005550142"


def test_a_number_inside_the_authors_own_link_is_not_linked_twice(detail):
    page = detail('<p>Call <a href="tel:+18001111234">800-111-1234</a> or 206-555-0147</p>')
    hrefs = [a.get_attribute("href") for a in page.query_selector_all("#detail-value-notes a")]
    assert hrefs == ["tel:+18001111234", "tel:+12065550147"]


def test_an_empty_description_still_shows_a_dash(detail):
    page = detail("")
    assert page.inner_text("#detail-value-notes") == "—"


def test_other_rows_stay_escaped_text(detail):
    page = detail("x")
    page.evaluate(
        """() => openDetail({
            uid: 'u', source: 'ics', title: 't', description: '', description_html: '',
            location: '<img src=x onerror=alert(1)> 800-111-1234', all_day: true,
            start_date: '2026-10-03', end_date: '2026-10-03', attendees: [], member: null,
            calendar_label: 'c', editable: false, recurring: false,
        })"""
    )
    where = page.inner_text("#detail-value-where")
    assert where == "<img src=x onerror=alert(1)> 800-111-1234"
    assert page.query_selector("#detail-value-where img") is None
    assert page.get_attribute("#detail-value-where a.phone-link", "href") == "tel:+18001111234"


# Neither has a place to break. A URL full of slashes wraps on its own, so the
# hazard is the unbroken run: a tracking token, a signed link, or a word pasted
# without spaces. They need different rules — `.inline-link` for the first,
# `.event-detail-value` and `.rich-text` for the second — so each is its own case.
UNBREAKABLE = {
    "a URL": "https://www.example.com/track?token=" + "a1b2c3d4" * 25,
    "a word": "Bring" + "x" * 300,
}


@pytest.mark.parametrize("viewport", [(1280, 900), (390, 844)])
@pytest.mark.parametrize("kind", list(UNBREAKABLE))
def test_an_unbreakable_run_wraps_instead_of_widening_the_modal(
    browser, live_server, viewport, kind
):
    context = browser.new_context(viewport={"width": viewport[0], "height": viewport[1]})
    page = context.new_page()
    try:
        page.goto(f"{live_server}/calendar")
        page.wait_for_function("typeof openDetail === 'function'")
        page.evaluate(OPEN_JS, rich_text.render_description(UNBREAKABLE[kind], source="ics"))
        page.wait_for_selector("#detail-value-notes")
        overflow = page.evaluate(
            """() => {
                const body = document.querySelector('#detail-modal-overlay .modal-body');
                const modal = document.querySelector('#detail-modal-overlay .modal-content');
                const value = document.querySelector('#detail-value-notes');
                const text = value.querySelector('a') || value;
                const range = document.createRange();
                range.selectNodeContents(text);
                return {
                    page: document.documentElement.scrollWidth - document.documentElement.clientWidth,
                    body: body.scrollWidth - body.clientWidth,
                    textRight: range.getBoundingClientRect().right,
                    modalRight: modal.getBoundingClientRect().right,
                };
            }"""
        )
        assert overflow["page"] <= 0, "the page grew a horizontal scrollbar"
        assert overflow["body"] <= 0, "the modal's content overflows its own box"
        assert overflow["textRight"] <= overflow["modalRight"] + 1
    finally:
        context.close()
