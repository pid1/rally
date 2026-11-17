"""static/escape.js in a real browser (#344).

One escapeHtml() replaced every page's copy. It escapes quotes as well as
`&`, `<` and `>`, so a value is safe between tags and in a quoted attribute
alike, and a missing value is an empty string rather than the word
`undefined`. What a browser makes of the result is the point, so a browser
parses it.
"""

from __future__ import annotations

import pytest


@pytest.fixture(scope="module")
def page(browser, live_server):
    context = browser.new_context()
    page = context.new_page()
    try:
        # Any page: base.html loads escape.js on every one.
        page.goto(f"{live_server}/styleguide", wait_until="domcontentloaded")
        yield page
    finally:
        context.close()


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("Tom & Jerry", "Tom &amp; Jerry"),
        ("<b>bold</b>", "&lt;b&gt;bold&lt;/b&gt;"),
        ('Pick up "Coach"', "Pick up &quot;Coach&quot;"),
        ("Sam's kit", "Sam&#39;s kit"),
        ("plain", "plain"),
        ("", ""),
    ],
)
def test_escapes_markup_and_both_quotes(page, value, expected):
    assert page.evaluate("(value) => escapeHtml(value)", value) == expected


def test_missing_values_are_empty_and_others_are_strings(page):
    got = page.evaluate(
        "() => [escapeHtml(undefined), escapeHtml(null), escapeHtml(42), escapeHtml(0)]"
    )
    assert got == ["", "", "42", "0"]


@pytest.mark.parametrize("quote", ['"', "'"])
def test_a_value_stays_one_attribute_in_either_quote(page, quote):
    value = """Lunch" onmouseover="x" data-x='y' & <z>"""
    got = page.evaluate(
        """([value, quote]) => {
            const host = document.createElement('div');
            host.innerHTML = `<span title=${quote}${escapeHtml(value)}${quote}></span>`;
            const span = host.firstElementChild;
            return { title: span.getAttribute('title'), names: span.getAttributeNames() };
        }""",
        [value, quote],
    )
    assert got == {"title": value, "names": ["title"]}


def test_text_between_tags_reads_as_typed(page):
    value = """She said "hi" & left <early>, didn't she?"""
    got = page.evaluate(
        """(value) => {
            const host = document.createElement('div');
            host.innerHTML = `<p>${escapeHtml(value)}</p>`;
            return host.textContent;
        }""",
        value,
    )
    assert got == value
