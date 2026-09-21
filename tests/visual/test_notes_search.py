"""Searching previous notes, driven in a real browser.

The API's own tests cover what `?search=` returns. What they cannot cover is
*when the page asks* — and that is the part with a contract worth holding:

  Search is **submit-driven**. It fires on the Search button and on Enter, and
  never on a keystroke.

That matters because the alternative is a live filter, which Preparedness does
use (debounced, refetching) — so "search that runs as you type" is a shape
already present in this codebase and an easy thing to drift into. Here it would
be wrong twice over: every keystroke would be a round trip, and `Load more`
would page a query the server no longer agrees with.

The Enter binding in particular is one line of JavaScript that nothing else
would notice losing. It is asserted here because there is nowhere else it can
be.
"""

from __future__ import annotations

import pytest

# Seeded by `rally.cli.seed()` across several past notes. Counts are read at
# runtime rather than hardcoded, so adding a note to the seed cannot fail this.
TERM = "Emma"
NONSENSE = "kryptonite-xyzzy"

STATE_JS = r"""() => {
  const count = document.getElementById('search-results-count');
  return {
    rows: document.querySelectorAll('#list-container .editable-item').length,
    emptyState: document.querySelector('#list-container .container-empty-state')?.innerText ?? null,
    countShown: !count.hidden,
    countText: count.textContent.trim(),
    query: document.getElementById('search-input').value,
    searchDisabled: document.getElementById('search-btn').disabled,
    bodies: Array.from(document.querySelectorAll('#list-container .editable-item-multiline'))
      .map((el) => el.innerText),
  };
}"""


def _open(browser, live_server):
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    page = context.new_page()
    page.goto(live_server + "/notes/previous", wait_until="networkidle")
    page.wait_for_selector("#list-container .editable-item")
    return context, page


@pytest.fixture(scope="module")
def page_state(browser, live_server):
    """Walk the search bar once and capture the state at each step."""
    context, page = _open(browser, live_server)
    try:
        before = page.evaluate(STATE_JS)

        # Typing alone must change nothing — this is the contract.
        page.fill("#search-input", TERM)
        page.wait_for_function("() => !document.getElementById('search-btn').disabled")
        typed_only = page.evaluate(STATE_JS)

        # Enter submits it.
        page.press("#search-input", "Enter")
        page.wait_for_function("() => !document.getElementById('search-results-count').hidden")
        after_enter = page.evaluate(STATE_JS)

        # Clear puts everything back.
        page.click("#search-clear")
        page.wait_for_function("() => document.getElementById('search-results-count').hidden")
        after_clear = page.evaluate(STATE_JS)

        # The button is the other way in, and must agree with Enter.
        page.fill("#search-input", TERM)
        page.click("#search-btn")
        page.wait_for_function("() => !document.getElementById('search-results-count').hidden")
        after_button = page.evaluate(STATE_JS)

        # A query that matches nothing.
        page.fill("#search-input", NONSENSE)
        page.click("#search-btn")
        page.wait_for_function(
            "() => document.querySelector('#list-container .container-empty-state')"
        )
        after_nonsense = page.evaluate(STATE_JS)

        yield before, typed_only, after_enter, after_clear, after_button, after_nonsense
    finally:
        page.close()
        context.close()


def test_the_archive_has_notes_to_search(page_state):
    """Guards the rest: every assertion below is vacuous on an empty archive."""
    before, *_ = page_state
    assert before["rows"] > 1, "seed no longer provides enough previous notes to search"
    assert not before["countShown"], "the results count shows before any search has run"


def test_typing_alone_does_not_search(page_state):
    """The contract. A live filter here would be a round trip per keystroke."""
    before, typed_only, *_ = page_state

    assert typed_only["query"] == TERM, "the query never reached the field"
    assert typed_only["rows"] == before["rows"], "the list changed while only typing"
    assert not typed_only["countShown"], "a result count appeared without a search"


def test_the_search_button_is_disabled_until_there_is_something_to_search(page_state):
    before, typed_only, *_ = page_state

    assert before["searchDisabled"], "an empty query could be submitted"
    assert not typed_only["searchDisabled"], "a typed query could not be submitted"


def test_enter_submits_the_search(page_state):
    """One line of JavaScript that nothing else would notice losing."""
    before, _, after_enter, *_ = page_state

    assert after_enter["countShown"], "Enter did not run the search"
    assert after_enter["rows"] < before["rows"], "Enter did not narrow the list"
    assert after_enter["rows"] > 0


def test_every_result_actually_matches(page_state):
    _, _, after_enter, *_ = page_state

    assert after_enter["bodies"], "no note bodies rendered"
    for body in after_enter["bodies"]:
        assert TERM.lower() in body.lower(), f"non-matching note in results: {body!r}"


def test_the_count_reports_the_total_not_the_page(page_state):
    """`total` comes from the API; the page must render it, not len(items)."""
    _, _, after_enter, *_ = page_state

    assert after_enter["countText"].endswith("."), f"odd count text: {after_enter['countText']!r}"
    assert "matching note" in after_enter["countText"]
    # Singular and plural are both spelled out in the page; whichever applies
    # here, the number in it has to agree with what was rendered.
    assert str(after_enter["rows"]) in after_enter["countText"]


def test_the_button_and_enter_agree(page_state):
    _, _, after_enter, _, after_button, _ = page_state

    assert after_button["rows"] == after_enter["rows"]
    assert after_button["countText"] == after_enter["countText"]


def test_clear_search_restores_the_whole_archive(page_state):
    before, _, _, after_clear, _, _ = page_state

    assert after_clear["query"] == "", "the field still holds the old query"
    assert after_clear["rows"] == before["rows"], "clearing did not restore every note"
    assert not after_clear["countShown"], "the results count survived Clear Search"


def test_a_query_matching_nothing_says_so(page_state):
    *_, after_nonsense = page_state

    assert after_nonsense["rows"] == 0
    assert after_nonsense["emptyState"], "no empty state rendered"
    # The archive is not empty — the *search* is — and the wording has to say
    # which, or it reads as "you have never written a note".
    assert "match" in after_nonsense["emptyState"].lower()
