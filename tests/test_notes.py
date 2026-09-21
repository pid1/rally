"""Tests for Notes: the markdown renderer's subset, the write-time markup
rejection, the CRUD API, the today/past boundary, and the dashboard card.

The renderer tests are the ones worth reading twice. The configuration is the
whole security and behavior boundary for this feature, and every rule in it was
chosen rather than inherited — ``"zero"`` because ``enable()`` cannot subtract,
``breaks`` + ``newline`` because a family pressing Enter expects a line break.
A test per property is what keeps a later "tidy-up" of that one line honest.
"""

from datetime import date, timedelta

from rally import markdown
from rally.models import Note, Setting


def _today() -> str:
    return date.today().strftime("%Y-%m-%d")


def _days(n: int) -> str:
    return (date.today() + timedelta(days=n)).strftime("%Y-%m-%d")


def _create(client, date_str=None, body="A note"):
    resp = client.post("/api/notes", json={"date": date_str or _today(), "body": body})
    assert resp.status_code == 201, resp.text
    return resp.json()


# --- The markdown subset -------------------------------------------------------


def test_renders_the_four_things_a_note_may_contain():
    assert "<strong>" in markdown.render("**bold**")
    assert "<em>" in markdown.render("*italic*")
    assert "<ul>" in markdown.render("- a\n- b")
    assert "<ol>" in markdown.render("1. a\n2. b")


def test_everything_outside_the_subset_stays_literal():
    """The `"zero"` preset is what holds this.

    `enable()` only ever adds rules, so a renderer built on `"commonmark"`
    would render every one of these however carefully the enable list were
    written. If this test fails, check the preset before anything else.
    """
    assert "<h1>" not in markdown.render("# Heading")
    assert "# Heading" in markdown.render("# Heading")
    assert "<a " not in markdown.render("[text](http://example.com)")
    assert "<img" not in markdown.render("![alt](http://example.com/i.png)")
    assert "<code>" not in markdown.render("`code`")
    assert "<pre>" not in markdown.render("```\nblock\n```")
    assert "<blockquote>" not in markdown.render("> quoted")
    assert "<hr" not in markdown.render("---")


def test_a_single_newline_is_a_visible_line_break():
    """`breaks` and the `newline` rule, together.

    Neither works alone: `breaks` changes what `newline` emits, and `"zero"`
    starts with `newline` disabled. A family typing two lines must see two.
    """
    assert "<br>" in markdown.render("Soccer at 5\nPack the bag")


def test_html_in_a_note_is_escaped_not_rendered():
    """The backstop behind the write-time rejection.

    Reached only if a note carrying markup gets stored anyway — a validator
    bug, or a row written by something other than the API.
    """
    out = markdown.render("<script>alert(1)</script>")
    assert "<script>" not in out
    assert "&lt;script&gt;" in out

    out = markdown.render('<img src=x onerror="alert(1)">')
    assert "<img" not in out
    assert "&lt;img" in out


def test_empty_and_whitespace_render_as_nothing():
    """What the dashboard's render-or-omit rule tests against."""
    assert markdown.render("") == ""
    assert markdown.render("   \n  ") == ""
    assert markdown.render(None) == ""


# --- Rejecting markup before it is stored --------------------------------------


def test_markup_is_rejected(client):
    for body in [
        "<script>alert(1)</script>",
        "<img src=x onerror=alert(1)>",
        "</div>",
        "< b >hello< /b >",
        "use <name> as the placeholder",
    ]:
        resp = client.post("/api/notes", json={"date": _today(), "body": body})
        assert resp.status_code == 422, f"should have been rejected: {body!r}"


def test_comparisons_are_not_mistaken_for_markup(client):
    """The reason the rule is tag-shaped rather than the `<` character.

    Eating "temp < 40" would be exactly the corruption this check exists to
    prevent, just relocated from the database to the family's ability to save.
    """
    for offset, body in enumerate(
        [
            "wear layers if temp < 40",
            "the 5<6 rule",
            "bring 2 < 3 bags",
            "cost < $20, pickup at 5",
        ]
    ):
        resp = client.post("/api/notes", json={"date": _days(offset), "body": body})
        assert resp.status_code == 201, f"should have been accepted: {body!r}"


def test_an_empty_note_is_rejected(client):
    for body in ["", "   ", "\n\n"]:
        resp = client.post("/api/notes", json={"date": _today(), "body": body})
        assert resp.status_code == 422


# --- CRUD and the one-per-day rule ---------------------------------------------


def test_create_and_read_back(client):
    body = _create(client, body="Soccer at 5")
    assert body["body"] == "Soccer at 5"
    assert "<p>Soccer at 5</p>" in body["body_html"]


def test_response_carries_source_and_rendered_html(client):
    """Both shapes travel together: the modal edits one, the page renders the other."""
    body = _create(client, body="Pack the **bag**")
    assert body["body"] == "Pack the **bag**"
    assert "<strong>bag</strong>" in body["body_html"]


def test_a_second_note_for_one_day_is_a_409_naming_the_first(client):
    first = _create(client, body="First")
    resp = client.post("/api/notes", json={"date": _today(), "body": "Second"})
    assert resp.status_code == 409
    # The id is what lets the modal switch to editing rather than refusing.
    assert resp.json()["detail"]["id"] == first["id"]


def test_moving_a_note_onto_an_occupied_day_is_a_409(client):
    _create(client, date_str=_days(1), body="Tomorrow")
    second = _create(client, date_str=_days(2), body="Day after")
    resp = client.put(f"/api/notes/{second['id']}", json={"date": _days(1)})
    assert resp.status_code == 409


def test_update_body_and_date(client):
    note = _create(client, body="Before")
    resp = client.put(f"/api/notes/{note['id']}", json={"body": "After", "date": _days(3)})
    assert resp.status_code == 200
    assert resp.json()["body"] == "After"
    assert resp.json()["date"] == _days(3)


def test_update_rejects_markup(client):
    note = _create(client)
    resp = client.put(f"/api/notes/{note['id']}", json={"body": "<b>no</b>"})
    assert resp.status_code == 422


def test_delete(client):
    note = _create(client)
    assert client.delete(f"/api/notes/{note['id']}").status_code == 200
    assert client.get(f"/api/notes/{note['id']}").status_code == 404


# --- The today/past boundary ---------------------------------------------------


def test_listing_covers_today_onward_with_no_upper_bound(client, db_session):
    db_session.add(Note(date=_days(-1), body="Yesterday"))
    db_session.add(Note(date=_days(400), body="Next year"))
    db_session.commit()
    _create(client, body="Today")

    dates = [n["date"] for n in client.get("/api/notes").json()]
    assert _days(-1) not in dates
    assert _days(400) in dates, "the planner has no upper bound, matching Meal Planner"


def test_previous_is_the_exact_complement(client, db_session):
    db_session.add(Note(date=_days(-1), body="Yesterday"))
    db_session.commit()
    _create(client, body="Today")

    previous = client.get("/api/notes/previous").json()
    assert [n["date"] for n in previous["items"]] == [_days(-1)]
    assert previous["total"] == 1


def test_a_past_note_cannot_be_edited_or_deleted(client, db_session):
    """Read-only is enforced at the endpoint, not only by hiding the button."""
    note = Note(date=_days(-2), body="Old")
    db_session.add(note)
    db_session.commit()
    db_session.refresh(note)

    assert client.put(f"/api/notes/{note.id}", json={"body": "New"}).status_code == 403
    assert client.delete(f"/api/notes/{note.id}").status_code == 403


def test_a_note_cannot_be_created_in_or_moved_into_the_past(client):
    assert client.post("/api/notes", json={"date": _days(-1), "body": "x"}).status_code == 403
    note = _create(client)
    assert client.put(f"/api/notes/{note['id']}", json={"date": _days(-1)}).status_code == 403


def test_the_boundary_follows_the_configured_timezone(client, db_session):
    """Both pages read the same helper, so a note is never on both or neither."""
    db_session.add(Setting(key="local_timezone", value="America/Chicago"))
    db_session.commit()
    _create(client, body="Today in Chicago")
    assert len(client.get("/api/notes").json()) == 1
    assert client.get("/api/notes/previous").json()["total"] == 0


# --- Previous: search and paging -----------------------------------------------


def test_search_matches_the_body_case_insensitively(client, db_session):
    db_session.add(Note(date=_days(-1), body="Soccer bag by the door"))
    db_session.add(Note(date=_days(-2), body="Dentist at 7:15"))
    db_session.commit()

    page = client.get("/api/notes/previous", params={"search": "SOCCER"}).json()
    assert page["total"] == 1
    assert "Soccer" in page["items"][0]["body"]


def test_total_counts_every_match_not_just_the_page(client, db_session):
    """A per-page count would report the page size no matter how many matched."""
    for i in range(1, 6):
        db_session.add(Note(date=_days(-i), body=f"Note {i}"))
    db_session.commit()

    page = client.get("/api/notes/previous", params={"limit": 2}).json()
    assert len(page["items"]) == 2
    assert page["has_more"] is True
    assert page["total"] == 5


def test_paging_walks_newest_first(client, db_session):
    for i in range(1, 4):
        db_session.add(Note(date=_days(-i), body=f"Note {i}"))
    db_session.commit()

    first = client.get("/api/notes/previous", params={"limit": 2}).json()
    assert [n["date"] for n in first["items"]] == [_days(-1), _days(-2)]

    second = client.get("/api/notes/previous", params={"limit": 2, "offset": 2}).json()
    assert [n["date"] for n in second["items"]] == [_days(-3)]
    assert second["has_more"] is False


# --- The dashboard card --------------------------------------------------------


# The card, identified by its header markup rather than by the words "Daily
# Note" — the template carries an explanatory comment containing that phrase,
# and a substring test would match the comment instead of the card.
NOTE_CARD = '<div class="card-header">Daily Note</div>'
WEATHER_CARD = '<div class="card-header">Weather &amp; Preparedness</div>'
SCHEDULE_CARD = '<div class="card-header">Today\'s Schedule</div>'


def test_dashboard_shows_todays_note_between_weather_and_schedule(client, db_session):
    db_session.add(Note(date=_today(), body="Pack the **soccer bag**"))
    db_session.commit()

    html = client.get("/dashboard").text
    assert NOTE_CARD in html
    assert "<strong>soccer bag</strong>" in html
    assert html.index(WEATHER_CARD) < html.index(NOTE_CARD) < html.index(SCHEDULE_CARD)


def test_dashboard_omits_the_card_when_today_has_no_note(client, db_session):
    db_session.add(Note(date=_days(1), body="Tomorrow only"))
    db_session.commit()

    html = client.get("/dashboard").text
    assert NOTE_CARD not in html
    assert "Tomorrow only" not in html
    assert "{{note_section}}" not in html, "the placeholder must always be substituted"


def test_dashboard_note_is_read_live_not_from_the_snapshot(client, db_session):
    """The one card not taken from the snapshot.

    A note written during the day has to appear without waiting for the next
    4 AM generation, which is the whole reason this path bypasses the cache.
    """
    assert NOTE_CARD not in client.get("/dashboard").text

    db_session.add(Note(date=_today(), body="Added at breakfast"))
    db_session.commit()

    html = client.get("/dashboard").text
    assert NOTE_CARD in html
    assert "Added at breakfast" in html


def test_dashboard_never_renders_stored_markup(client, db_session):
    """Defense in depth: a row that bypassed the API still cannot inject."""
    db_session.add(Note(date=_today(), body="<script>alert(1)</script>"))
    db_session.commit()

    html = client.get("/dashboard").text
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


# --- Pages ---------------------------------------------------------------------


def test_both_pages_render(client):
    assert client.get("/notes").status_code == 200
    assert client.get("/notes/previous").status_code == 200
