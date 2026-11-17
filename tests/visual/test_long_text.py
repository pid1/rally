"""Long task descriptions, folded to their first sentences in a real browser.

`longTextHtml()` (static/long_text.js) decides where a long description folds
and builds the `View more` toggle; a click anywhere on the text opens and
closes it. The cut is worth pinning down — it should end where a sentence
ends, never mid-word at a hard count — and so is the toggle, because a click
on a phone number inside the text has to dial rather than fold.
"""

from __future__ import annotations

import pytest

TWO_SENTENCES = (
    "The library is open Monday through Thursday between 9am and 6pm, Friday "
    "from 9am to noon, and the first Saturday of the month from 10am to 2pm. "
    "Books can be renewed online anytime up to three times before they are due."
)
LONG = (
    TWO_SENTENCES + " Bring the library card, not a photo of it. Ask at the desk for Dana if "
    "the hold shelf is empty. Parking is behind the building, off Elm Street."
)


@pytest.fixture(scope="module")
def page(browser, live_server):
    context = browser.new_context()
    page = context.new_page()
    page.goto(f"{live_server}/todo")
    page.wait_for_function("typeof longTextHtml === 'function'")
    try:
        yield page
    finally:
        context.close()


def cut(page, text):
    return page.evaluate("(t) => longTextCut(t)", text)


def test_short_text_is_shown_whole(page):
    assert cut(page, "Call the plumber.") is None
    assert cut(page, TWO_SENTENCES) is None
    html = page.evaluate("(t) => longTextHtml(t, 'x')", "Call 800-111-1234.")
    assert "long-text" not in html and "phone-link" in html


def test_long_text_folds_at_a_sentence_end(page):
    fold = cut(page, LONG)
    assert fold is not None and not fold["ellipsis"]
    assert LONG[: fold["cut"]] == TWO_SENTENCES


def test_abbreviations_do_not_end_a_sentence(page):
    text = "x" * 200 + " see Dr. Ruiz first today. " + "Then " + "z" * 120 + ". The end."
    fold = cut(page, text)
    assert text[: fold["cut"]].endswith("first today.")


def test_text_with_no_sentence_end_folds_at_a_word(page):
    text = "word " * 100
    fold = cut(page, text)
    assert fold["ellipsis"]
    assert text[fold["cut"]] == " "


def test_a_line_break_counts_as_a_sentence_end(page):
    text = "a" * 120 + "\n" + "b " * 150
    assert cut(page, text)["cut"] == 120


def test_clicking_the_text_toggles_it_and_a_phone_link_does_not(page):
    text = LONG + " Questions: 800-111-1234."
    page.evaluate(
        """(t) => {
            const host = document.createElement('div');
            host.id = 'long-text-probe';
            host.className = 'editable-item-description';
            host.innerHTML = longTextHtml(t, 'probe');
            document.querySelector('main, body').prepend(host);
        }""",
        text,
    )
    probe = page.locator("#long-text-probe .long-text")
    assert page.locator("#long-text-probe .long-text-short").is_visible()
    assert not page.locator("#long-text-probe .long-text-full").is_visible()

    page.locator("#long-text-probe .long-text-short").click()
    assert "is-expanded" in probe.get_attribute("class")
    assert (
        page.locator("#long-text-probe .long-text-toggle").get_attribute("aria-expanded") == "true"
    )

    page.locator("#long-text-probe .long-text-full .phone-link").evaluate(
        "(a) => a.addEventListener('click', (e) => e.preventDefault())"
    )
    page.locator("#long-text-probe .long-text-full .phone-link").click()
    assert "is-expanded" in probe.get_attribute("class")

    page.locator("#long-text-probe .long-text-toggle").click()
    assert "is-expanded" not in probe.get_attribute("class")

    # The open state outlives a re-render under the same key.
    page.locator("#long-text-probe .long-text-short").click()
    html = page.evaluate("(t) => longTextHtml(t, 'probe')", text)
    assert "long-text is-expanded" in html
