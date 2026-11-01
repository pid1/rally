"""The shared dialog's `choices` (#313): a question with an answer to pick.

`confirmModal()` without `choices` is pinned wherever a page asks; what is
pinned here is the radios: drawn with nothing chosen, the verb waiting for one,
and the promise resolving to the value picked.
"""

from __future__ import annotations

import pytest

ASK = """() => {
    window.__answer = 'pending';
    confirmModal({
        title: 'Remove packing list',
        messageHtml: '"<strong>Beach day</strong>" is on Saturday, Oct 10, 2026.',
        confirmLabel: 'Save',
        choices: [
            { value: 'keep', labelHtml: 'Keep it on Saturday' },
            { value: 'remove', labelHtml: 'Remove <strong>it</strong>' },
        ],
    }).then(answer => { window.__answer = answer; });
}"""


@pytest.fixture
def page(browser, live_server):
    context = browser.new_context(viewport={"width": 1280, "height": 900})
    page = context.new_page()
    try:
        page.goto(live_server + "/styleguide", wait_until="networkidle")
        yield page
    finally:
        page.close()
        context.close()


def test_choices_wait_for_one_and_answer_with_it(page):
    page.evaluate(ASK)
    page.wait_for_selector("#dialog-modal-overlay", state="visible")
    radios = page.locator('#dialog-modal-choices input[type="radio"]')
    assert radios.count() == 2
    assert (
        page.evaluate(
            "() => [...document.querySelectorAll('#dialog-modal-choices input')].some(i => i.checked)"
        )
        is False
    )
    assert page.is_disabled("#btn-dialog-modal-confirm")
    # A label carries markup, as the message does.
    assert page.inner_text("#dialog-modal-choices strong") == "it"

    radios.nth(1).check()
    assert page.is_enabled("#btn-dialog-modal-confirm")
    page.click("#btn-dialog-modal-confirm")
    page.wait_for_function("window.__answer !== 'pending'")
    assert page.evaluate("window.__answer") == "remove"
    assert not page.is_visible("#dialog-modal-overlay")


def test_cancel_answers_false_and_the_next_question_starts_unchosen(page):
    page.evaluate(ASK)
    page.locator('#dialog-modal-choices input[value="keep"]').check()
    page.click("#btn-dialog-modal-cancel")
    page.wait_for_function("window.__answer !== 'pending'")
    assert page.evaluate("window.__answer") is False

    page.evaluate(ASK)
    assert page.is_disabled("#btn-dialog-modal-confirm")
    assert not page.is_checked('#dialog-modal-choices input[value="keep"]')


def test_a_question_without_choices_is_unchanged(page):
    page.evaluate(ASK)
    page.click("#btn-dialog-modal-cancel")
    page.click("#sg-dialog-confirm")
    page.wait_for_selector("#dialog-modal-overlay", state="visible")
    assert not page.is_visible("#dialog-modal-choices")
    assert page.is_enabled("#btn-dialog-modal-confirm")
