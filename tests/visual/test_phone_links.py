"""Phone numbers in family-written text, linked in a real browser.

`escapeHtmlWithPhoneLinks()` decides what is a number to dial and what is a
date, a ZIP+4 code or a quantity range, and it escapes the text on the way
through. Both halves are worth pinning down: a missed number is a number
copied out by hand on a phone, and a wrong one is a link that dials a
quantity. The rules live in static/phone_links.js.

These run in Chromium rather than against a fake because the function is
shipped to a browser and its output is handed to innerHTML — reading the
result back out of the DOM is what proves the escaping holds.
"""

from __future__ import annotations

import pytest

# The written form, and the number a tap should dial.
LINKED = [
    ("Number is 800-111-1234", "800-111-1234", "tel:+18001111234"),
    ("Call (800) 111-1234 before five", "(800) 111-1234", "tel:+18001111234"),
    ("1-800-111-1234", "1-800-111-1234", "tel:+18001111234"),
    ("+1 800 111 1234", "+1 800 111 1234", "tel:+18001111234"),
    ("Vet 206.555.0147", "206.555.0147", "tel:+12065550147"),
    ("Pharmacy 2065550147", "2065550147", "tel:+12065550147"),
    ("Dr. Ruiz: 206-555-0147. Ask for Dana.", "206-555-0147", "tel:+12065550147"),
    ("The hotel is +44 20 7946 0958", "+44 20 7946 0958", "tel:+442079460958"),
    # A stray digit either side is text, not part of the number.
    ("call 800-111-1234 3 times", "800-111-1234", "tel:+18001111234"),
]

# Things that look numeric and are not numbers to dial. A wrong link is worse
# than no link: it puts a quantity or a date in the dialer.
NOT_LINKED = [
    "Due 2026-09-11",
    "Ship to 98101-1234",
    "Order 100-200-3000",
    "Bring 500-1000 napkins",
    "Gate code 1234, then knock",
    "Meet at 6:30",
    "Serial ID8001111234",
    "Warranty 8001111234-22",
    # Seven digits alone could be anything; and a bare national number with no
    # country code has nothing to dial with.
    "Front desk 555-1234",
    "London 020 7946 0958",
]

RENDER_JS = """
(text) => {
    const host = document.createElement('div');
    host.innerHTML = escapeHtmlWithPhoneLinks(text);
    return {
        text: host.textContent,
        scripts: host.querySelectorAll('script').length,
        links: [...host.querySelectorAll('a')].map((a) => ({
            href: a.getAttribute('href'),
            text: a.textContent,
            cls: a.className,
        })),
    };
}
"""


@pytest.fixture(scope="module")
def render(browser, live_server):
    """Return `render(text) -> dict`, evaluated on a page that loads the helper."""
    context = browser.new_context()
    page = context.new_page()
    page.goto(f"{live_server}/todo")
    page.wait_for_function("typeof escapeHtmlWithPhoneLinks === 'function'")
    try:
        yield lambda text: page.evaluate(RENDER_JS, text)
    finally:
        context.close()


@pytest.mark.parametrize(("written", "link_text", "href"), LINKED)
def test_numbers_people_write_become_links(render, written, link_text, href):
    result = render(written)
    assert [(link["text"], link["href"]) for link in result["links"]] == [(link_text, href)]
    assert result["links"][0]["cls"] == "phone-link"
    # The number is shown the way it was typed; only the href is normalized.
    assert result["text"] == written


@pytest.mark.parametrize("written", NOT_LINKED)
def test_text_that_only_looks_like_a_number_stays_plain(render, written):
    result = render(written)
    assert result["links"] == []
    assert result["text"] == written


def test_every_number_in_a_line_is_linked(render):
    result = render("Office 800-111-1234, cell 206-555-0147")
    assert [link["href"] for link in result["links"]] == ["tel:+18001111234", "tel:+12065550147"]
    assert result["text"] == "Office 800-111-1234, cell 206-555-0147"


def test_markup_in_the_text_is_escaped_not_run(render):
    written = '<script>alert(1)</script> & "quoted" 800-111-1234'
    result = render(written)
    assert result["scripts"] == 0
    assert result["text"] == written
    assert [link["href"] for link in result["links"]] == ["tel:+18001111234"]


def test_empty_text_renders_nothing(render):
    assert render("")["text"] == ""


def test_a_task_description_renders_its_number_as_a_link(browser, live_server):
    """End to end, on the page where a number is most often written down."""
    context = browser.new_context()
    page = context.new_page()
    try:
        page.goto(f"{live_server}/todo")
        page.wait_for_selector(".editable-item-description")
        link = page.query_selector(".editable-item-description a.phone-link")
        assert link is not None, "the seeded dentist task shows a number nobody can tap"
        assert link.get_attribute("href") == "tel:+12065550147"
        assert link.text_content() == "206-555-0147"
        # Inline is what exempts it from the 44px sweep, and what keeps the
        # sentence around it intact.
        assert link.evaluate("(el) => getComputedStyle(el).display") == "inline"
    finally:
        context.close()


# --- linkPhoneNumbers: the same decisions, on markup that is already there ---------

WALK_JS = """
(html) => {
    const host = document.createElement('div');
    host.innerHTML = html;
    document.body.append(host);
    linkPhoneNumbers(host);
    const result = {
        html: host.innerHTML,
        text: host.textContent,
        links: [...host.querySelectorAll('a')].map((a) => ({
            href: a.getAttribute('href'),
            text: a.textContent,
            cls: a.className,
        })),
    };
    host.remove();
    return result;
}
"""


@pytest.fixture(scope="module")
def walk(browser, live_server):
    """Return `walk(html) -> dict`: run `linkPhoneNumbers` over an element holding it."""
    context = browser.new_context()
    page = context.new_page()
    page.goto(f"{live_server}/todo")
    page.wait_for_function("typeof linkPhoneNumbers === 'function'")
    try:
        yield lambda html: page.evaluate(WALK_JS, html)
    finally:
        context.close()


@pytest.mark.parametrize(("written", "link_text", "href"), LINKED)
def test_the_walker_links_exactly_what_the_escaping_helper_links(walk, written, link_text, href):
    result = walk(f"<p>{written}</p>")
    assert [(link["text"], link["href"]) for link in result["links"]] == [(link_text, href)]
    assert result["links"][0]["cls"] == "phone-link"
    assert result["text"] == written


@pytest.mark.parametrize("written", NOT_LINKED)
def test_the_walker_leaves_what_only_looks_like_a_number_alone(walk, written):
    result = walk(f"<p>{written}</p>")
    assert result["links"] == []
    assert result["text"] == written


def test_the_walker_keeps_the_markup_around_a_number(walk):
    result = walk(
        "<p><strong>Coach:</strong> <em>800-111-1234</em></p><ul><li>206-555-0147</li></ul>"
    )
    assert result["html"] == (
        '<p><strong>Coach:</strong> <em><a class="phone-link" href="tel:+18001111234">800-111-1234</a></em></p>'
        '<ul><li><a class="phone-link" href="tel:+12065550147">206-555-0147</a></li></ul>'
    )


def test_the_walker_leaves_a_number_inside_an_existing_link_alone(walk):
    result = walk('<p><a href="https://example.com/8001111234">Call 800-111-1234</a></p>')
    assert [link["href"] for link in result["links"]] == ["https://example.com/8001111234"]


def test_the_walker_is_not_fooled_by_text_that_looks_like_markup(walk):
    # A text node holding "<script>" is text, and must stay text through the walk.
    result = walk("<p>&lt;script&gt;alert(1)&lt;/script&gt; 800-111-1234</p>")
    assert "<script" not in result["html"]
    assert result["text"] == "<script>alert(1)</script> 800-111-1234"
    assert [link["href"] for link in result["links"]] == ["tel:+18001111234"]
