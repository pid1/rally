"""The per-device behavioral settings catalog and its storage.

The rules worth pinning are the ones that are invisible when they break: an
absent row resolving to ``auto`` (which is what Rally already did), a partial
write leaving the other settings alone, and one device's answers never reaching
another.
"""

from __future__ import annotations

from rally import member_prefs
from rally.models import Device, MemberPreference

KEY = member_prefs.CALENDAR_DEFAULT_VIEW
PHONE = "device-phone"
TABLET = "device-tablet"


# --- The catalog ---------------------------------------------------------------


def test_every_setting_offers_auto_and_defaults_to_it():
    """A concrete default would move a screen nobody had configured.

    The module raises on import if this is ever false, so this asserts the
    guard is doing its job rather than only that today's catalog happens to
    pass. `auto` being a real option also means "let Rally pick" is something a
    person can say, not only something they get by staying silent.
    """
    for setting in member_prefs.CATALOG:
        values = {choice.value for choice in setting.choices}
        assert member_prefs.AUTO in values
        assert setting.default == member_prefs.AUTO


def test_auto_leads_the_choice_list():
    """It is the default, so it is the first thing a dropdown offers."""
    assert member_prefs.CALENDAR_VIEW_CHOICES[0].value == member_prefs.AUTO


def test_every_calendar_choice_is_a_view_and_range_the_toolbar_can_express():
    """The stored value *is* the pair the calendar draws, so it has to parse.

    `auto` is the exception: it names Rally's own rule rather than a view, and
    only the calendar can resolve it.
    """
    views = {"calendar", "agenda"}
    ranges = {"day", "week", "month", "rolling30"}

    for choice in member_prefs.CALENDAR_VIEW_CHOICES:
        if choice.value == member_prefs.AUTO:
            continue
        view, _, span = choice.value.partition(":")
        assert view in views, choice.value
        assert span in ranges, choice.value


def test_the_grid_is_never_offered_a_range_it_cannot_draw():
    """`calendar:rolling30` would be a landing view the page silently changes.

    `syncRangeOptions()` hides `Next 30 days` in Calendar mode, so offering it
    here means somebody picks it and lands on Month without being told.
    """
    values = {choice.value for choice in member_prefs.CALENDAR_VIEW_CHOICES}
    assert "calendar:rolling30" not in values
    assert "agenda:rolling30" in values


def test_is_valid_rejects_unknown_keys_and_values():
    assert member_prefs.is_valid(KEY, "agenda:week")
    assert member_prefs.is_valid(KEY, member_prefs.AUTO)
    assert not member_prefs.is_valid("nope", "agenda:week")
    assert not member_prefs.is_valid(KEY, "agenda:decade")


# --- Storage -------------------------------------------------------------------


def test_a_device_with_no_rows_resolves_to_auto(db_session, make_member):
    """Which is exactly what Rally did before this table existed."""
    member = make_member("Jon")
    assert member_prefs.preferences(db_session, member.id, PHONE) == {KEY: member_prefs.AUTO}


def test_a_device_that_has_never_been_seen_still_answers(db_session, make_member):
    """A brand new browser asking what to do is the ordinary first request."""
    make_member("Jon")
    resolved = member_prefs.preferences_for_device(db_session, "never-heard-of-it")
    assert all(values == member_prefs.defaults() for values in resolved.values())


def test_an_answer_on_one_device_never_reaches_another(db_session, make_member):
    """The whole point of keying on the device rather than on its width."""
    member = make_member("Jon")

    member_prefs.set_preferences(db_session, member.id, PHONE, {KEY: "agenda:day"})

    assert member_prefs.preferences(db_session, member.id, PHONE)[KEY] == "agenda:day"
    assert member_prefs.preferences(db_session, member.id, TABLET)[KEY] == member_prefs.AUTO


def test_one_members_answers_never_reach_another_on_the_same_device(db_session, make_member):
    """Two people share the kitchen tablet; they do not share its settings."""
    jon = make_member("Jon")
    emma = make_member("Emma")

    member_prefs.set_preferences(db_session, jon.id, TABLET, {KEY: "agenda:week"})

    assert member_prefs.preferences(db_session, emma.id, TABLET)[KEY] == member_prefs.AUTO


def test_writing_the_same_setting_twice_updates_one_row(db_session, make_member):
    """The unique index is what makes "upsert one answer" mean one answer."""
    member = make_member("Jon")

    member_prefs.set_preferences(db_session, member.id, PHONE, {KEY: "agenda:week"})
    member_prefs.set_preferences(db_session, member.id, PHONE, {KEY: "agenda:month"})

    rows = db_session.query(MemberPreference).all()
    assert len(rows) == 1
    assert rows[0].value == "agenda:month"


def test_choosing_auto_is_still_written(db_session, make_member):
    """The row records that somebody *chose* Rally's rule.

    Without it, changing what `auto` resolves to in a later release could not
    tell a family who had picked it on purpose from one who never looked.
    """
    member = make_member("Jon")

    member_prefs.set_preferences(db_session, member.id, PHONE, {KEY: member_prefs.AUTO})

    assert db_session.query(MemberPreference).count() == 1


def test_unknown_keys_and_values_are_never_stored(db_session, make_member):
    """The API rejects these with a 422; storing them is the failure worth
    avoiding twice, because a preference nothing reads looks exactly like one
    that quietly stopped working."""
    member = make_member("Jon")

    member_prefs.set_preferences(
        db_session, member.id, PHONE, {"not_a_setting": "agenda:day", KEY: "agenda:decade"}
    )

    assert db_session.query(MemberPreference).count() == 0


def test_a_write_without_a_device_stores_nothing(db_session, make_member):
    """Half a key is not a key. A row with no device could never be read back."""
    member = make_member("Jon")

    member_prefs.set_preferences(db_session, member.id, "", {KEY: "agenda:day"})

    assert db_session.query(MemberPreference).count() == 0


def test_a_stored_value_the_catalog_dropped_reads_back_as_the_default(db_session, make_member):
    """A choice removed in a later release, or a hand-edited row.

    Reads report what the app can actually honor — handing a browser an option
    no dropdown has is how a screen ends up showing nothing selected.
    """
    member = make_member("Jon")
    db_session.add(
        MemberPreference(
            family_member_id=member.id,
            device_id=PHONE,
            pref_key=KEY,
            value="calendar:fortnight",
        )
    )
    db_session.commit()

    assert member_prefs.preferences(db_session, member.id, PHONE)[KEY] == member_prefs.AUTO


def test_preferences_for_device_covers_every_member(db_session, make_member):
    jon = make_member("Jon")
    emma = make_member("Emma")
    member_prefs.set_preferences(db_session, jon.id, TABLET, {KEY: "agenda:week"})

    resolved = member_prefs.preferences_for_device(db_session, TABLET)

    assert resolved[jon.id][KEY] == "agenda:week"
    assert resolved[emma.id][KEY] == member_prefs.AUTO


# --- Devices -------------------------------------------------------------------


def test_a_device_registers_itself_on_first_contact(db_session):
    """There is no enrollment: the first request carrying a token creates it."""
    device = member_prefs.touch_device(db_session, PHONE, "iPhone")

    assert device.id == PHONE
    assert device.label == "iPhone"
    assert db_session.query(Device).count() == 1


def test_saying_hello_again_does_not_overwrite_a_name(db_session):
    """A page load must not clobber a name somebody typed."""
    member_prefs.touch_device(db_session, PHONE, "iPhone")
    member_prefs.touch_device(db_session, PHONE, "Jon's phone")

    device = member_prefs.touch_device(db_session, PHONE)

    assert device.label == "Jon's phone"
    assert db_session.query(Device).count() == 1


def test_last_seen_moves_forward_on_contact(db_session):
    first = member_prefs.touch_device(db_session, PHONE, "iPhone").last_seen_at
    later = member_prefs.touch_device(db_session, PHONE).last_seen_at
    assert later >= first


def test_an_empty_device_id_is_never_recorded(db_session):
    assert member_prefs.touch_device(db_session, "") is None
    assert db_session.query(Device).count() == 0


def test_answer_counts_say_what_forgetting_would_throw_away(db_session, make_member):
    jon = make_member("Jon")
    emma = make_member("Emma")
    member_prefs.set_preferences(db_session, jon.id, TABLET, {KEY: "agenda:week"})
    member_prefs.set_preferences(db_session, emma.id, TABLET, {KEY: "agenda:day"})
    member_prefs.set_preferences(db_session, jon.id, PHONE, {KEY: "agenda:day"})

    counts = member_prefs.answer_counts(db_session)

    assert counts[TABLET] == 2
    assert counts[PHONE] == 1


def test_devices_are_listed_most_recently_seen_first(db_session):
    member_prefs.touch_device(db_session, PHONE, "iPhone")
    member_prefs.touch_device(db_session, TABLET, "iPad")

    assert [device.id for device in member_prefs.devices(db_session)][0] == TABLET


# --- Cleanup -------------------------------------------------------------------


def test_deleting_a_member_clears_their_answers_on_every_device(db_session, make_member):
    member = make_member("Jon")
    member_prefs.set_preferences(db_session, member.id, PHONE, {KEY: "agenda:day"})
    member_prefs.set_preferences(db_session, member.id, TABLET, {KEY: "agenda:week"})

    assert member_prefs.delete_member_preferences(db_session, member.id) == 2
    assert member_prefs.delete_member_preferences(db_session, member.id) == 0


def test_forgetting_a_device_clears_its_answers_for_everybody(db_session, make_member):
    jon = make_member("Jon")
    emma = make_member("Emma")
    member_prefs.set_preferences(db_session, jon.id, TABLET, {KEY: "agenda:week"})
    member_prefs.set_preferences(db_session, emma.id, TABLET, {KEY: "agenda:day"})
    member_prefs.set_preferences(db_session, jon.id, PHONE, {KEY: "agenda:day"})

    assert member_prefs.delete_device_preferences(db_session, TABLET) == 2

    # The other device is untouched.
    assert member_prefs.preferences(db_session, jon.id, PHONE)[KEY] == "agenda:day"
