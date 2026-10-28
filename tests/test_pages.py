"""Smoke tests for the HTML page routes, redirects, and the no-cache static mount."""

import pathlib
import re

import pytest

TEMPLATES = pathlib.Path(__file__).resolve().parents[1] / "templates"
STATIC = TEMPLATES.parent / "static"


@pytest.mark.parametrize(
    "path",
    [
        "/todo",
        "/todo/completed",
        "/shopping",
        "/shopping/purchased",
        "/meal-planner",
        "/meal-planner/previous",
        "/settings",
        "/calendar",
    ],
)
def test_page_renders_html(client, path):
    resp = client.get(path)
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")


@pytest.mark.parametrize(
    "path",
    [
        "/dashboard",
        "/todo",
        "/todo/completed",
        "/shopping",
        "/shopping/purchased",
        "/meal-planner",
        "/meal-planner/previous",
        "/settings",
    ],
)
def test_nav_links_to_shopping(client, path):
    """Every page renders the shared sidebar, so every page carries the link."""
    assert 'href="/shopping"' in client.get(path).text


@pytest.mark.parametrize("path", ["/meal-planner", "/meal-planner/previous"])
def test_meal_pages_include_shared_edit_modal(client, path):
    """Both meal pages render the shared edit modal partial and load its JS, so
    the edit experience has a single source of truth."""
    html = client.get(path).text
    assert 'id="modal-overlay"' in html  # markup from _meal_edit_modal.html
    assert 'id="plan-form"' in html
    assert "/static/meal_edit_modal.js" in html


def test_root_redirects_to_dashboard(client):
    resp = client.get("/", follow_redirects=False)
    assert resp.status_code in (307, 308)
    assert resp.headers["location"] == "/dashboard"


@pytest.mark.parametrize("path", ["/dinner-planner", "/meal-history"])
def test_old_meal_page_urls_are_gone(client, path):
    """Deliberately not redirected: nothing inside Rally links to them."""
    assert client.get(path, follow_redirects=False).status_code == 404


def test_meal_planner_links_to_previous_meals_and_back(client):
    assert 'href="/meal-planner/previous"' in client.get("/meal-planner").text
    assert 'href="/meal-planner"' in client.get("/meal-planner/previous").text


def test_static_css_sets_no_cache(client):
    resp = client.get("/static/styles.css")
    assert resp.status_code == 200
    assert resp.headers["cache-control"] == "no-cache"


def test_modals_are_opened_only_through_the_shared_helper():
    """Setting `display` on an overlay by hand skips everything the helper does.

    `showModalOverlay()` resets the scroll position and settles the fade that
    signals more content below. Nineteen call sites across four templates set
    `.style.display` directly instead, so those modals reopened wherever they
    were last left — Add Item came back scrolled past its own first label — and
    their fade state was whatever it happened to be. The behavior has to be
    the same for every modal, which means one way in and one way out.
    """
    direct = re.compile(r"getElementById\(['\"][a-z-]*modal-overlay['\"]\)\.style\.display")
    offenders = [
        f"{path.name}:{i}"
        for path in sorted(TEMPLATES.glob("*.html"))
        for i, line in enumerate(path.read_text().splitlines(), 1)
        if direct.search(line)
    ]
    assert not offenders, (
        f"modals must open with showModalOverlay() and close with hideModalOverlay(): {offenders}"
    )


def _page_sources():
    return sorted(TEMPLATES.glob("*.html")) + sorted(STATIC.glob("*.js"))


def test_questions_and_failures_go_through_the_shared_dialog():
    """#272 — confirm(), alert() and prompt() are the browser's, in its font
    and chrome, and take plain text only. Every question and failure goes
    through confirmModal() / alertModal() in static/dialogs.js instead, and a
    form check or a saved notice reads in place in a `.form-message`."""
    # A call has an argument; prose about "confirm()" in a comment does not.
    call = re.compile(r"(?<![\w.])(?:window\.)?(?:confirm|alert|prompt)\((?!\))")
    offenders = sorted(path.name for path in _page_sources() if call.search(path.read_text()))
    assert not offenders, f"use confirmModal() / alertModal(), not the browser's: {offenders}"


def test_every_page_carries_the_shared_dialog(client):
    """The dialog is in base.html, after the content, so it draws over any page
    modal, and modal.js and dialogs.js load on every page."""
    html = client.get("/preparedness").text
    overlay = html[html.index('id="dialog-modal-overlay"') :]
    assert 'class="modal-content modal-content--narrow"' in overlay[:200]
    assert 'class="btn btn--primary" id="btn-dialog-modal-confirm"' in overlay
    assert 'class="btn" id="btn-dialog-modal-cancel">Cancel</button>' in overlay
    # Last of the overlays: an overlay later in the document draws on top.
    overlays = re.findall(r'class="modal-overlay" id="([^"]+)"', html)
    assert len(overlays) > 1 and overlays[-1] == "dialog-modal-overlay"
    assert html.count("/static/modal.js") == 1
    assert html.count("/static/dialogs.js") == 1


def test_no_button_is_secondary():
    """#272 — plain `.btn` is the outlined button now; `--secondary` is retired."""
    offenders = [p.name for p in _page_sources() if "btn--secondary" in p.read_text()]
    assert not offenders, f"btn--secondary is retired; use plain btn: {offenders}"


def test_every_full_size_save_is_primary():
    """#272 — the button that commits a form or a modal is `.btn--primary`, so
    Save looks the same on every page. A compact Save in a Manage row is not
    the modal's commit and stays plain."""
    save = re.compile(r'<button[^>]*class="([^"]*)"[^>]*>Save</button>')
    offenders = [
        f"{path.name}: {classes}"
        for path in _page_sources()
        for classes in save.findall(path.read_text())
        if "btn--sm" not in classes.split() and "btn--primary" not in classes.split()
    ]
    assert not offenders, f"a full-size Save must be btn--primary: {offenders}"


def test_preparedness_asks_and_refuses_in_place(client):
    """#272 — Preparedness asks in the shared dialog with the name in bold, and
    its form checks read in a `.form-message` rather than an alert."""
    html = client.get("/preparedness").text
    for message_id in ("item-message", "item-date-message", "location-add-message"):
        assert f'<div class="form-message" id="{message_id}" role="alert" hidden></div>' in html
    assert html.count("confirmModal({") == 2
    assert "Delete <strong>${escapeHtml(" in html
    # Locations are renamed in place, as stores are on /shopping: a refusal
    # (empty, taken) reads under the row with the typed name kept, and any
    # other failure is the failure dialog.
    assert "prompt(" not in html
    assert 'id="location-message-${l.id}" role="alert" hidden' in html
    assert "refuseBlank(input, messageId, BLANK_NAME)" in html
    assert "showFormMessage(messageId, 'A location with that name already exists.');" in html
    assert "Could not rename the location." in html
    assert "Could not delete the location." in html


def test_calendar_offers_view_mode_and_range_as_separate_controls():
    """One dropdown could not say "a week, drawn as a calendar".

    `View` and `Range` are orthogonal — the renderer and the slice of time —
    and conflating them is why `Day` and `Week` both rendered agenda lists for
    as long as they existed. The two selectors are the whole feature, so the
    markup carrying them is worth pinning.
    """
    html = (TEMPLATES / "calendar.html").read_text()
    assert 'id="view-select"' in html
    assert 'id="range-select"' in html
    for option in ('value="calendar"', 'value="agenda"'):
        assert option in html, f"the View selector is missing {option}"
    for option in (
        'value="day"',
        'value="week"',
        'value="month"',
        'value="rolling3"',
        'value="rolling30"',
    ):
        assert option in html, f"the Range selector is missing {option}"


def test_calendar_renders_a_time_grid_and_not_only_lists():
    """The grid is what a list cannot be: duration as height, overlap as position."""
    html = (TEMPLATES / "calendar.html").read_text()
    assert "function timeGridHtml()" in html
    assert "function layoutColumns(" in html, "overlapping events must split the column"
    assert "calendar-timegrid-allday" in html, "all-day events need their own band"


def test_calendar_agenda_iterates_the_selected_window():
    """The agenda looped a hard-coded 30 days regardless of the view, and only
    showed fewer for Day and Week as a side effect of the narrower fetch. With
    real ranges that is a warm cache away from rendering 30 headings on a day.
    """
    html = (TEMPLATES / "calendar.html").read_text()
    body = html.split("function agendaHtml()", 1)[1].split("function ", 1)[0]
    assert "rangeDayCount()" in body
    assert "AGENDA_DAYS" not in body, "the agenda must not loop a fixed day count"


def test_calendar_has_no_dead_viewport_override():
    """`applyViewportView()` was a stub returning False, still called from
    init() and wired to a matchMedia listener. Width picks the landing view and
    is not consulted again."""
    html = (TEMPLATES / "calendar.html").read_text()
    assert "applyViewportView" not in html


# Pages that show text a family member typed — a task's details, a note on a
# shopping item, an event's location. Each of those can carry a phone number.
FREE_TEXT_PAGES = [
    "/todo",
    "/todo/completed",
    "/shopping",
    "/shopping/purchased",
    "/preparedness",
    "/go-list",
    "/calendar",
]


@pytest.mark.parametrize("path", FREE_TEXT_PAGES)
def test_pages_that_show_family_text_load_the_phone_linker(client, path):
    """The helper is loaded in the head, before the inline script that calls it."""
    html = client.get(path).text
    assert "/static/phone_links.js" in html


def test_family_written_text_is_rendered_through_the_phone_linker():
    """A description, a note or a location is where a number to call gets written.

    Rendering one with a bare `escapeHtml()` puts the number on the screen as
    text, which on a phone means copying it out by hand to dial it. Every one
    of these fields goes through `escapeHtmlWithPhoneLinks()` instead — which
    escapes exactly the same way and marks up the numbers it finds — so a field
    added later cannot quietly lose its links.
    """
    plain = re.compile(
        r"\bescapeHtml\("
        r"(?:todo\.description|item\.note|item\.notes|occurrence\.(?:location|description))\)"
    )
    offenders = [
        f"{path.name}:{i}"
        for path in sorted(TEMPLATES.glob("*.html"))
        for i, line in enumerate(path.read_text().splitlines(), 1)
        if plain.search(line)
    ]
    assert not offenders, f"free text must render through escapeHtmlWithPhoneLinks(): {offenders}"


# --- Per-device behavioral defaults --------------------------------------------


@pytest.mark.parametrize("path", ["/calendar", "/settings"])
def test_pages_that_resolve_a_device_load_the_device_helper(client, path):
    """Both pages answer "which device is this, and whose?" the same way.

    Rally has no logins, so both halves are things only the browser knows. Two
    copies of that logic is how Settings and the calendar end up disagreeing
    about which device they are on.
    """
    assert "/static/device_member.js" in client.get(path).text


def test_the_calendar_is_server_rendered_with_the_behavior_catalog(client):
    """The landing view has to be decided before the first paint.

    Fetching the catalog would mean drawing the month grid and then swapping it
    for somebody's agenda — a flicker on a screen and a full repaint on e-ink.
    """
    html = client.get("/calendar").text
    assert "calendar_default_view" in html
    assert "function applyLandingView(" in html


def test_the_calendar_owns_what_auto_means(client):
    """`auto` names Rally's rule rather than a view, so only the page holding
    the rule can resolve it — and it must hold it in exactly one place."""
    html = client.get("/calendar").text
    assert "const NARROW = '(max-width: 767px)'" in html
    assert html.count("matchMedia(NARROW)") == 1


def test_settings_scopes_its_defaults_to_this_device(client):
    """One answer per person per setting, for the device in front of you, plus
    the list that makes the other devices visible and forgettable."""
    html = client.get("/settings").text
    assert 'id="member-prefs-list"' in html
    assert 'id="device-member"' in html
    assert 'id="device-label"' in html
    assert 'id="device-list"' in html
    assert "RallyDevice.deviceId()" in html


def test_the_event_modal_asks_what_to_pack(client):
    """#270: the event modal's packing lists reuse Manage Bags' add row, and
    `Put on` offers both answers with neither chosen."""
    html = client.get("/calendar").text
    assert 'id="event-packing-group"' in html
    assert '<div class="manage-row" id="event-packing-add-row">' in html
    assert ">What to pack</label>" in html
    assert (
        "Packing lists follow this event when it moves, and go when it&#39;s deleted." in html
        or ("Packing lists follow this event when it moves, and go when it's deleted." in html)
    )
    span = html[html.index('id="event-packing-span-group"') :]
    span = span[: span.index("</div>\n                    </div>")]
    assert 'value="first"> First day' in span
    assert 'value="every"> Every day' in span
    assert "checked" not in span


def test_packing_lists_asks_and_refuses_through_the_shared_dialog(client):
    """#272: Packing Lists asks in the shared dialog, with the name in bold, and
    its own page-level confirmation (#270) is gone. Its form checks read in a
    `.form-message`; joinNames and the form-message helpers come from
    dialogs.js rather than copies of their own."""
    html = client.get("/packing-lists").text
    assert 'id="confirm-modal-overlay"' not in html
    assert "openConfirmModal" not in html
    assert html.count('id="dialog-modal-overlay"') == 1
    assert "function showFormMessage" not in html
    assert "function clearFormMessage" not in html
    assert "joinNames" not in (STATIC / "packing_lists.js").read_text()
    assert html.count("confirmModal({") >= 10
    assert "Take ${day.name} off" not in html
    for message_id in (
        "day-add-actions-message",
        "schedule-actions-message",
        "template-name-message",
        "template-actions-message",
        "day-edit-actions-message",
        "item-actions-message",
        "bag-add-message",
        "bag-edit-message",
    ):
        assert f'<div class="form-message" id="{message_id}" role="alert" hidden></div>' in html
    assert 'id="list-bag-message-${bag.id}"' in html


def test_tasks_ask_and_refuse_through_the_shared_dialog(client):
    """#272: Tasks asks before a delete in the shared dialog, naming the task or
    the recurring task in bold, and a recurring task's start-date refusal reads
    under Starts rather than in an alert."""
    html = client.get("/todo").text
    assert html.count("confirmModal({") == 2
    assert "title: 'Delete task'" in html
    assert "title: 'Delete recurring task'" in html
    assert "Existing generated tasks will remain." in html
    assert "todos will remain" not in html
    assert (
        '<div class="form-message" id="recurring-start-message" role="alert" hidden></div>' in html
    )
    # A missing title is refused in place under it, through the shared
    # requireText(), on both forms, and cleared every time a modal opens.
    assert 'const BLANK_TITLE = "A title can\'t be empty.";' in html
    for message_id in ("todo-title-message", "recurring-title-message"):
        assert f'<div class="form-message" id="{message_id}" role="alert" hidden></div>' in html
    # Every message in a modal is cleared when it opens, through the shared
    # clearFormMessages(): both openers of each modal.
    assert html.count("clearFormMessages('modal-overlay');") == 2
    assert html.count("clearFormMessages('recurring-modal-overlay');") == 2
    assert "requireText('todo-title', 'todo-title-message', BLANK_TITLE)" in html
    assert "requireText('recurring-title', 'recurring-title-message', BLANK_TITLE)" in html
    assert "if (refuseBlankTodoTitle()) return;" in html
    assert "if (refuseBlankRecurringTitle()) return;" in html


def test_shopping_asks_and_refuses_through_the_shared_dialog(client):
    """#272: Shopping asks before deleting an item or a store in the shared
    dialog, naming it in bold, and a store name that is taken reads in place:
    under the add field, or under the row being renamed."""
    html = client.get("/shopping").text
    assert html.count("confirmModal({") == 2
    assert "title: 'Delete item'" in html
    assert "title: 'Delete store'" in html
    assert '<div class="form-message" id="store-add-message" role="alert" hidden></div>' in html
    assert 'id="store-message-${s.id}" role="alert" hidden' in html
    # A missing name is refused in place, through the shared requireText() /
    # refuseBlank(): the item name, a new store and a renamed one.
    assert 'const BLANK_NAME = "A name can\'t be empty.";' in html
    assert '<div class="form-message" id="item-name-message" role="alert" hidden></div>' in html
    assert "requireText('item-name', 'item-name-message', BLANK_NAME)" in html
    assert "requireText('store-add-name', 'store-add-message', BLANK_NAME)" in html
    assert "if (refuseBlankItemName()) return;" in html
    assert "if (refuseBlankStoreName()) return;" in html
    assert "refuseBlank(input, `store-message-${storeId}`, BLANK_NAME)" in html


def test_meal_planner_asks_through_the_shared_dialog():
    """#272: the shared meal modal asks before deleting a meal, and before
    moving a rated one onto the planner, in the shared dialog with the meal's
    name in bold."""
    js = (STATIC / "meal_edit_modal.js").read_text()
    assert js.count("await confirmModal({") == 2
    assert "title: 'Delete meal'" in js
    assert "title: 'Move to planner'" in js
    assert "Moving <strong>${escapeHtml(this.editingPlan.plan)}</strong> to the planner" in js


def test_a_missing_value_is_refused_through_the_shared_component():
    """A required text field's missing value is refused in place by
    requireText() in dialogs.js. A page that took the browser's `invalid`
    event itself would be a second copy of it to drift."""
    js = (STATIC / "dialogs.js").read_text()
    assert "window.requireText = requireText;" in js
    assert "window.refuseBlank = refuseBlank;" in js
    offenders = [
        p.name
        for p in _page_sources()
        if p.name != "dialogs.js" and "addEventListener('invalid'" in p.read_text()
    ]
    assert not offenders, f"use requireText() from dialogs.js: {offenders}"


def test_the_meal_modal_refuses_a_missing_name_in_place(client):
    """The shared meal modal refuses a missing Meal Name under it, on both
    pages it is on, and clears the message whenever it opens."""
    js = (STATIC / "meal_edit_modal.js").read_text()
    assert "requireText('plan-text', 'plan-text-message', \"A name can't be empty.\")" in js
    assert "if (this.refuseBlankName()) return;" in js
    assert js.count("clearFormMessages('modal-overlay');") == 2
    for path in ("/meal-planner", "/meal-planner/previous"):
        html = client.get(path).text
        assert '<div class="form-message" id="plan-text-message" role="alert" hidden></div>' in html


@pytest.mark.parametrize(
    ("path", "fields"),
    [
        ("/notes", [("bodyField", "note-body-message")]),
        (
            "/preparedness",
            [("'item-name'", "item-name-message"), ("'location-add-name'", "location-add-message")],
        ),
        (
            "/packing-lists",
            [
                ("'day-add-name'", "day-add-name-message"),
                ("'day-edit-name-input'", "day-edit-name-message"),
                ("'template-name'", "template-name-message"),
                ("'item-name'", "item-name-message"),
                ("'bag-add-name'", "bag-add-message"),
            ],
        ),
    ],
)
def test_required_names_are_refused_in_place(client, path, fields):
    """#272: every required text field on these pages refuses a missing value
    under itself through requireText(), never the browser's bubble."""
    html = client.get(path).text
    assert "setCustomValidity" not in html
    for field, message_id in fields:
        assert f"requireText({field}, '{message_id}'," in html
        assert f'id="{message_id}" role="alert" hidden' in html


@pytest.mark.parametrize(
    ("path", "overlay_id", "delete_call"),
    [
        (
            "/preparedness",
            "location-modal-overlay",
            "await fetch(`/api/preparedness/locations/${id}`",
        ),
        ("/shopping", "store-modal-overlay", "await fetch(`/api/shopping/stores/${storeId}`"),
        (
            "/packing-lists",
            "bags-modal-overlay",
            "await requestJson(`/api/packing-list-bags/${bagId}`",
        ),
    ],
)
def test_a_confirmed_delete_clears_the_manage_modals_messages(
    client, path, overlay_id, delete_call
):
    """#272: confirming a delete in a "Manage …" modal settles it, so no
    refusal is left standing: the shared clearFormMessages() clears the whole
    modal after the answer and before the request. Cancel leaves every message
    where it was."""
    html = client.get(path).text
    confirmed = html.index("if (!confirmed) return;", html.index(delete_call) - 600)
    between = html[confirmed : html.index(delete_call, confirmed)]
    assert f"clearFormMessages('{overlay_id}');" in between
    js = (STATIC / "dialogs.js").read_text()
    assert "window.clearFormMessages = clearFormMessages;" in js


@pytest.mark.parametrize(
    ("path", "wiring"),
    [
        ("/notes", ["requireDate(dateField, 'note-date-message');"]),
        (
            "/packing-lists",
            [
                "requireDate('day-date', 'day-add-message');",
                "requireDate('day-edit-date', 'day-edit-message');",
            ],
        ),
    ],
)
def test_an_empty_required_date_is_refused_in_place(client, path, wiring):
    """#272: an empty required date reads "Date is a required field." under
    it, through the shared requireDate(), never the browser's bubble."""
    html = client.get(path).text
    for line in wiring:
        assert line in html
    js = (STATIC / "dialogs.js").read_text()
    assert "const REQUIRED_DATE = 'Date is a required field.';" in js
    assert "window.requireDate = requireDate;" in js
    # Only a missing value is taken from the browser: a date before its `min`
    # keeps the browser's own message rather than failing silently.
    assert "if (!el.validity.valueMissing) return;" in js
    # Several fields refused by one Save: the cursor goes to the first.
    assert "function takeFocus(el) {" in js
    # A Save stopped by one field still says every other refusal in the form:
    # each requireText() field's spaces check runs, skipping fields not in use.
    assert "form.addEventListener('invalid', () => {" in js
    assert (
        "if (el.disabled || el.readOnly || !el.required || !el.getClientRects().length) return;"
        in js
    )


def test_changing_a_notes_date_clears_its_messages(client):
    """A new date on Notes is a new attempt: every message from the last Save
    clears, on `input` so the taken-date notice (on `change`) still shows."""
    html = client.get("/notes").text
    assert "clearFormMessagesOn(dateField, form, 'input');" in html


def test_the_meal_modal_refuses_an_empty_date_in_place(client):
    js = (STATIC / "meal_edit_modal.js").read_text()
    assert "requireDate('plan-date', 'plan-date-message');" in js
    html = client.get("/meal-planner").text
    assert '<div class="form-message" id="plan-date-message" role="alert" hidden></div>' in html


def test_calendar_asks_and_refuses_through_the_shared_dialog(client):
    """#272: the calendar asks before deleting an event, and before a move
    adopts a packing list already on the day, in the shared dialog with the
    names in bold; its form checks read under the field they are about."""
    html = client.get("/calendar").text
    assert "title: 'Delete event'" in html
    assert "moves ? 'Move event' : 'Use packing list'" in html
    assert "function joinNames" not in html
    for message_id in (
        "event-title-message",
        "event-start-time-message",
        "event-start-date-message",
        "event-calendar-message",
        "event-packing-span-message",
        "event-packing-message",
        "event-form-message",
    ):
        assert f'<div class="form-message" id="{message_id}" role="alert" hidden></div>' in html
    assert "requireText('event-title', 'event-title-message'," in html
    assert "requireChoice('event-calendar', 'event-calendar-message'," in html
    assert "requireDate('event-start-time', 'event-start-time-message');" in html
    assert "requireDate('event-start-date', 'event-start-date-message');" in html
    # The start that is showing is the required one.
    assert "document.getElementById('event-start-time').required = !allDay;" in html
    assert html.count("clearFormMessages('event-modal-overlay');") == 2


def test_a_template_already_on_a_date_is_refused_in_place(client):
    """Add Packing List (kept in sync) and Edit Packing List refuse a date the
    template is already on, with a message under Date, rather than closing and
    opening the other day without saying why."""
    html = client.get("/packing-lists").text
    for message_id in ("day-add-message", "day-edit-message"):
        assert f'<div class="form-message" id="{message_id}" role="alert" hidden></div>' in html
    assert html.count("alreadyOnMessage(") == 3  # the helper and its two callers
    assert "revealDay(error.detail.id)" not in html


def test_custom_schedule_is_detached_rather_than_hidden(client):
    """Safari ignores `hidden` on an <option>, so `Custom schedule` was offered
    on every new event. It is detached at load and put back only for a stored
    rule the custom controls cannot show — the VIEW_ONLY_OPTIONS treatment."""
    html = client.get("/calendar").text
    assert '<option value="other">Custom schedule</option>' in html
    assert '<option value="other" hidden>' not in html
    assert "OTHER_REPEAT_OPTION.remove();" in html
    assert "if (choice === 'other') repeatSelect.appendChild(OTHER_REPEAT_OPTION);" in html


def test_only_the_save_button_saves_an_event(client):
    """Enter in a field of the event form must not submit it: the browser's
    implicit submission is turned off, so only the Save button saves."""
    html = client.get("/calendar").text
    guard = html[html.index("document.getElementById('event-form').addEventListener('keydown'") :]
    guard = guard[: guard.index("});")]
    assert "event.key !== 'Enter'" in guard
    assert "field.tagName === 'INPUT' || field.tagName === 'SELECT'" in guard
    assert "event.preventDefault()" in guard


def test_a_rally_calendar_is_not_labelled_as_a_feed(client):
    """A Rally calendar's row reads [Rally], not the [ICS] every type without a
    case of its own used to fall through to."""
    html = client.get("/settings").text
    assert "if (type === 'native') return 'Rally';" in html


def test_settings_asks_and_reports_in_place(client):
    """#272: Settings asks in the shared dialog with the name in bold, says a
    section saved under its Save button, and refuses a modal's missing fields
    under them."""
    html = client.get("/settings").text
    for title in ("Forget device", "Delete family member", "Delete calendar", "Stop following"):
        assert f"title: '{title}'" in html
    # A new calendar type or league is a new attempt: its messages all go.
    assert "clearFormMessagesOn('calendar-type', 'calendar-modal-overlay');" in html
    assert "clearFormMessagesOn('team-league', 'team-modal-overlay');" in html

    for form in (
        "meal-planner",
        "home",
        "tasks",
        "learning",
        "shopping",
        "packing-lists",
        "preparedness",
        "sports",
        "general",
    ):
        assert (
            f'<div class="form-message" id="{form}-form-message" role="status" hidden></div>'
            in html
        )
    for message_id in (
        "member-name-message",
        "calendar-label-message",
        "calendar-url-message",
        "calendar-member-message",
        "calendar-username-message",
        "calendar-password-message",
        "calendar-form-message",
        "team-label-message",
        "team-key-message",
        "team-form-message",
    ):
        assert f'<div class="form-message" id="{message_id}" role="alert" hidden></div>' in html


def test_a_new_packing_list_date_clears_the_modal(client):
    """#272: Add and Edit Packing List clear every message when the date,
    template or start changes, through the shared helper."""
    html = client.get("/packing-lists").text
    assert "clearFormMessagesOn('day-edit-date', 'day-edit-modal-overlay');" in html
    assert "'day-add-modal-overlay');" in html
    assert "clearFormMessage('day-add-message')" not in html
    assert "clearFormMessage('day-edit-message')" not in html


DATE_PAGES = [
    "/dashboard",
    "/todo",
    "/todo/completed",
    "/shopping",
    "/shopping/purchased",
    "/calendar",
    "/notes",
    "/notes/previous",
    "/meal-planner",
    "/meal-planner/previous",
    "/packing-lists",
    "/packing-lists/previous",
    "/preparedness",
    "/go-list",
    "/settings",
    "/styleguide",
]


@pytest.mark.parametrize("path", DATE_PAGES)
def test_every_page_carries_the_shared_date_helpers(client, path):
    """#305: static/dates.js loads once on every page, from base.html, next to
    dialogs.js and not deferred, so the shared scripts and a page's own script
    can write a date as they load."""
    html = client.get(path).text
    assert html.count("/static/dates.js") == 1
    tag = re.search(r"<script[^>]*/static/dates\.js[^>]*>", html).group(0)
    assert "defer" not in tag
    assert html.index("/static/dialogs.js") < html.index("/static/dates.js")


# Every page and script that had a date helper of its own before #305, and the
# copies that must not come back. `formatDate` is dates.js's now, so a page that
# defines its own would quietly shadow it.
OLD_DATE_HELPERS = {
    "static/packing_lists.js": [
        "function parseDay",
        "function formatLongDate",
        "function addDays",
        "const WEEKDAYS",
        "const MONTHS",
        "formatLongDate,",
        "addDays,",
    ],
    "static/recurrence_form.js": [
        "function ordinal",
        "function formatLongDate",
        "WEEKDAYS_LONG",
        "MONTHS_LONG",
        "ordinal,",
        "T00:00:00",
    ],
    "static/meal_edit_modal.js": ["function todayLocal", "mealTodayLocal", "Intl.DateTimeFormat"],
    "templates/calendar.html": [
        "function parseIso(",
        "function isoDate",
        "function addDays",
        "const WEEKDAYS",
    ],
    "templates/todo.html": [
        "function parseLocalDate",
        "function formatShortDate",
        "MONTHS_SHORT = [",
        "RecurrenceForm.ordinal",
        "monthNames",
        "dayNames",
        "T00:00:00",
    ],
    "templates/todo_completed.html": ["monthNames", "dayNames", "T00:00:00"],
    "templates/notes.html": [
        "function todayLocal",
        "function getDateInDays",
        "function formatDate",
        "T00:00:00",
        "toLocaleDateString",
        "Intl.DateTimeFormat",
    ],
    "templates/notes_previous.html": [
        "function formatDate",
        "T00:00:00",
        "toLocaleDateString",
        "Intl.DateTimeFormat",
    ],
    "templates/meal_planner.html": [
        "function todayLocal",
        "function getDateInDays",
        "function formatDate",
        "T00:00:00",
        "toLocaleDateString",
        "Intl.DateTimeFormat",
    ],
    "templates/meal_planner_previous.html": [
        "function formatDate",
        "T00:00:00",
        "toLocaleDateString",
    ],
    "templates/packing_lists.html": [
        "function todayLocal",
        "function formatShortDate",
        "formatLongDate",
        "MONTHS_SHORT = [",
        "Intl.DateTimeFormat",
    ],
    "templates/packing_lists_previous.html": ["Intl.DateTimeFormat"],
}


@pytest.mark.parametrize("path", sorted(OLD_DATE_HELPERS))
def test_date_helpers_are_not_copied_onto_pages(path):
    """#305: the per-page date helpers are gone; each page reads, steps and
    writes dates through static/dates.js."""
    source = (TEMPLATES.parent / path).read_text()
    left = [needle for needle in OLD_DATE_HELPERS[path] if needle in source]
    assert not left, f"{path} still has its own date helpers: {left}"


def test_no_page_defines_a_shared_date_helper():
    """#305: no page or script but dates.js defines a function or list by a
    shared helper's name, which would shadow the shared one."""
    # `ordinal` only as a function: the calendar's custom-rule form keeps a
    # local `const ordinal` for a position ("first"), which is not a date.
    functions = "parseIsoDate|isoDate|addDays|shiftIsoDate|todayLocal|ordinal|formatDate"
    names = "parseIsoDate|isoDate|addDays|shiftIsoDate|todayLocal|formatDate"
    names += "|WEEKDAYS|WEEKDAYS_SHORT|MONTHS|MONTHS_SHORT"
    definition = re.compile(rf"function\s+(?:{functions})\s*\(|(?:const|let|var)\s+(?:{names})\s*=")
    offenders = [
        path.name
        for path in _page_sources()
        if path.name != "dates.js" and definition.search(path.read_text())
    ]
    assert not offenders, f"use static/dates.js rather than a copy: {offenders}"


def test_dates_js_reads_no_clock_of_its_own():
    """#305: formatDate() never decides what today is; a page hands it in.
    The only clock in dates.js is todayLocal(), in the zone it is handed."""
    source = (STATIC / "dates.js").read_text()
    assert source.count("new Date()") == 1
    body = source[source.index("function todayLocal") :]
    assert "new Date()" in body[: body.index("\n    }")]
