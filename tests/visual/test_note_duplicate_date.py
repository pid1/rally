"""Picking a day that already has a note, driven in a real browser.

One note per day is enforced by a unique index, so "add a note to a day that
has one" has to resolve to *something*. Refusing would cost the family whatever
they had typed; overwriting would lose what was already there. The page loads
the existing note for editing instead, appends anything typed, and saves
nothing until Save is pressed — so what will be written is on screen first.

None of that is reachable from the API tests. The `409` they cover is only the
race guard; the ordinary path never reaches the server, because the page knows
which days are taken from the list it already loaded. The behavior only exists
in the page, so the page is what has to be driven.

The `required` textarea is why the collision is caught on the date change
rather than on Save: an empty body cannot submit, so a "nothing typed yet"
collision would otherwise be unreachable.
"""

from __future__ import annotations

import json
import urllib.request

import pytest

# Far enough ahead to stay on the Notes page (today onward) however long this
# suite lives, and unique enough to find again for cleanup.
NOTE_DATE = "2099-04-07"
EXISTING = "rally-visual-probe existing note"
TYPED = "rally-visual-probe typed text"
MARKER = "rally-visual-probe"

MEASURE_JS = r"""() => {
  const body = document.getElementById('note-body');
  const message = document.getElementById('note-message');
  return {
    overlayShown:
      getComputedStyle(document.getElementById('note-modal-overlay')).display !== 'none',
    title: document.getElementById('note-modal-title').textContent.trim(),
    editId: document.getElementById('note-edit-id').value,
    date: document.getElementById('note-date').value,
    body: body.value,
    selectionStart: body.selectionStart,
    selectionEnd: body.selectionEnd,
    focused: document.activeElement ? document.activeElement.id : null,
    messageShown: !message.hidden,
    messageText: message.textContent.trim(),
    messageRole: message.getAttribute('role'),
    deleteShown:
      getComputedStyle(document.getElementById('btn-delete-note')).display !== 'none',
  };
}"""


def _notes(live_server: str) -> list[dict]:
    with urllib.request.urlopen(f"{live_server}/api/notes", timeout=5) as resp:
        return json.load(resp)


def _delete_probe_notes(live_server: str) -> None:
    """This suite shares one session-scoped database; leave it as it was found."""
    for note in _notes(live_server):
        if MARKER in (note.get("body") or ""):
            req = urllib.request.Request(f"{live_server}/api/notes/{note['id']}", method="DELETE")
            urllib.request.urlopen(req, timeout=5).close()


@pytest.fixture(scope="module")
def collision(browser, live_server):
    """Seed one note, then hit its date twice: once empty, once with text typed.

    Returns (untouched, after_typing, notes_after).
    """
    _delete_probe_notes(live_server)
    payload = json.dumps({"date": NOTE_DATE, "body": EXISTING}).encode()
    req = urllib.request.Request(
        f"{live_server}/api/notes",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    urllib.request.urlopen(req, timeout=5).close()

    context = browser.new_context(viewport={"width": 1440, "height": 900})
    page = context.new_page()
    try:
        page.goto(live_server + "/notes", wait_until="networkidle")

        # 1. Nothing typed: picking the taken day loads its note as-is.
        page.click("#btn-add-note")
        page.fill("#note-date", NOTE_DATE)
        page.wait_for_function(
            "() => document.getElementById('note-modal-title').textContent.trim() === 'Edit Note'"
        )
        untouched = page.evaluate(MEASURE_JS)

        # 2. Typed first, then the collision: the text is kept, not dropped.
        page.click("#btn-cancel-note")
        page.click("#btn-add-note")
        page.fill("#note-body", TYPED)
        page.fill("#note-date", NOTE_DATE)
        page.wait_for_function(
            "() => document.getElementById('note-modal-title').textContent.trim() === 'Edit Note'"
        )
        after_typing = page.evaluate(MEASURE_JS)

        page.click("#btn-cancel-note")
        yield untouched, after_typing, _notes(live_server)
    finally:
        page.close()
        context.close()
        _delete_probe_notes(live_server)


def test_picking_a_taken_day_switches_to_editing_that_note(collision):
    untouched, _, _ = collision

    assert untouched["overlayShown"], "the modal closed instead of switching mode"
    assert untouched["title"] == "Edit Note"
    assert untouched["editId"], "still in add mode — a save here would create a second note"
    assert untouched["date"] == NOTE_DATE
    assert untouched["deleteShown"], "edit mode must offer Delete"


def test_with_nothing_typed_the_note_loads_unchanged(collision):
    untouched, _, _ = collision
    assert untouched["body"] == EXISTING


def test_the_caret_lands_at_the_end_of_the_loaded_note(collision):
    """Typing continues the note rather than overwriting from its start."""
    untouched, _, _ = collision

    assert untouched["focused"] == "note-body", "the textarea did not take focus"
    end = len(untouched["body"])
    assert untouched["selectionStart"] == end
    assert untouched["selectionEnd"] == end, "text is selected — typing would replace the note"


def test_typed_text_is_appended_rather_than_discarded(collision):
    _, after_typing, _ = collision

    assert after_typing["body"].startswith(EXISTING), "the existing note was overwritten"
    assert after_typing["body"].endswith(TYPED), "what was typed did not survive the collision"
    assert TYPED in after_typing["body"]


def test_the_caret_lands_after_the_appended_text(collision):
    _, after_typing, _ = collision

    end = len(after_typing["body"])
    assert after_typing["selectionStart"] == end
    assert after_typing["selectionEnd"] == end


def test_both_cases_say_what_happened(collision):
    """Silently swapping the modal's identity would be the worst version of this."""
    untouched, after_typing, _ = collision

    for state in (untouched, after_typing):
        assert state["messageShown"], "the modal changed what it held without saying so"
        assert state["messageText"], "the message box is empty"
        # A status report about something already done, not a rejected save.
        assert state["messageRole"] == "status"

    assert untouched["messageText"] != after_typing["messageText"], (
        "the two cases differ in what happened to the typed text and must say so"
    )


def test_nothing_is_written_until_save(collision):
    """The whole flow is a change of what the modal holds, not a write.

    Both collisions above were cancelled, so the seeded note must be exactly as
    it started and no second note may exist for that day.
    """
    _, _, notes_after = collision

    probes = [n for n in notes_after if MARKER in (n.get("body") or "")]
    assert len(probes) == 1, f"expected the one seeded note, found {len(probes)}"
    assert probes[0]["body"] == EXISTING, "cancelling still changed the stored note"
