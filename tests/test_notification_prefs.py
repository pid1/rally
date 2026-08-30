"""Tests for who hears about which kind of notification.

The resolution is the whole feature: five gates in a fixed order, an absent row
meaning the kind's default, and — the rule worth guarding hardest — a
preference that can only ever *narrow*. Ticking every box must not start
sending somebody other people's appointments.
"""

import pathlib
import sqlite3
from datetime import UTC, datetime

import pytest

from rally.models import MemberNotificationPref
from rally.notification_prefs import (
    EVENT_CHANGE,
    EVENT_REMINDER,
    KIND_KEYS,
    KINDS,
    PREP_REFRESH,
    SHOPPING_ADDED,
    TASK_ASSIGNMENT,
    defaults,
    filter_recipients,
    overview,
    preferences,
    prefers,
    set_preferences,
    subscribers,
    switch_enabled,
    wants,
)


@pytest.fixture
def token(make_setting):
    make_setting("pushover_app_token", "app-token")
    return "app-token"


@pytest.fixture
def reachable(make_member):
    return make_member("Emma", pushover_user_key="emma-key")


@pytest.fixture
def unreachable(make_member):
    return make_member("Jake")


def _pref(db_session, member, kind, enabled):
    db_session.add(MemberNotificationPref(family_member_id=member.id, kind=kind, enabled=enabled))
    db_session.commit()


# --- The catalog -------------------------------------------------------------


def test_the_catalogue_covers_every_kind_rally_sends():
    assert KIND_KEYS == (
        EVENT_REMINDER,
        EVENT_CHANGE,
        TASK_ASSIGNMENT,
        PREP_REFRESH,
        SHOPPING_ADDED,
    )


def test_defaults_preserve_what_rally_did_before_this_existed():
    """Everything on except the kind that did not exist yet."""
    assert defaults() == {
        EVENT_REMINDER: True,
        EVENT_CHANGE: True,
        TASK_ASSIGNMENT: True,
        PREP_REFRESH: True,
        SHOPPING_ADDED: False,
    }


def test_every_kind_carries_a_sentence_naming_its_audience():
    """The Settings list renders these verbatim; a blank one answers nothing."""
    for kind in KINDS:
        assert kind.label
        assert kind.audience.endswith(".")


# --- Resolution ----------------------------------------------------------------


def test_an_absent_row_resolves_to_the_kinds_default(db_session, reachable):
    assert prefers(db_session, reachable.id, EVENT_REMINDER) is True
    assert prefers(db_session, reachable.id, SHOPPING_ADDED) is False


def test_an_explicit_row_wins_over_the_default(db_session, reachable):
    _pref(db_session, reachable, EVENT_REMINDER, False)
    _pref(db_session, reachable, SHOPPING_ADDED, True)

    assert prefers(db_session, reachable.id, EVENT_REMINDER) is False
    assert prefers(db_session, reachable.id, SHOPPING_ADDED) is True


def test_preferences_fill_in_the_defaults_for_everything_unsaid(db_session, reachable):
    _pref(db_session, reachable, EVENT_CHANGE, False)

    assert preferences(db_session, reachable.id) == {**defaults(), EVENT_CHANGE: False}


def test_a_member_with_no_key_wants_nothing(db_session, unreachable):
    """Their preferences are held, but the gate before them is shut."""
    _pref(db_session, unreachable, SHOPPING_ADDED, True)

    assert wants(db_session, unreachable, EVENT_REMINDER) is False
    assert wants(db_session, unreachable, SHOPPING_ADDED) is False
    # Held, not cleared: adding a key later takes effect with no second trip.
    assert preferences(db_session, unreachable.id)[SHOPPING_ADDED] is True


def test_an_install_wide_switch_off_overrides_a_ticked_box(db_session, reachable, make_setting):
    _pref(db_session, reachable, TASK_ASSIGNMENT, True)
    make_setting("todo_notify_enabled", "false")

    assert switch_enabled(db_session, TASK_ASSIGNMENT) is False
    assert wants(db_session, reachable, TASK_ASSIGNMENT) is False


def test_a_kind_with_no_install_wide_switch_is_always_enabled(db_session):
    assert switch_enabled(db_session, EVENT_REMINDER) is True
    assert switch_enabled(db_session, EVENT_CHANGE) is True


def test_shopping_additions_are_off_at_the_install_level_until_asked_for(
    db_session, reachable, make_setting
):
    _pref(db_session, reachable, SHOPPING_ADDED, True)
    assert wants(db_session, reachable, SHOPPING_ADDED) is False

    make_setting("shopping_notify_enabled", "true")
    assert wants(db_session, reachable, SHOPPING_ADDED) is True


def test_an_unknown_kind_resolves_to_nothing_rather_than_raising(db_session, reachable):
    assert prefers(db_session, reachable.id, "no_such_kind") is False
    assert wants(db_session, reachable, "no_such_kind") is False
    assert switch_enabled(db_session, "no_such_kind") is False


# --- Narrowing, never widening -------------------------------------------------


def test_ticking_every_box_does_not_add_anybody_to_an_audience(
    db_session, client, token, make_member, make_event, mock_pushover, frozen_now
):
    """The gate a preference cannot open.

    Emma wants every kind Rally sends. She is still not on Jon's dentist
    appointment, so she hears nothing about it — the audience rule runs first
    and the preference only ever narrows what it chose.
    """
    frozen_now(datetime(2026, 8, 11, 12, tzinfo=UTC))
    emma = make_member("Emma", pushover_user_key="emma-key")
    for key in KIND_KEYS:
        _pref(db_session, emma, key, True)
    jon = make_member("Jon", pushover_user_key="jon-key")

    event = make_event("Dentist", attendees=[jon])
    client.post(f"/api/events/{event.id}/notify", json={})

    assert [push["user"] for push in mock_pushover.sent] == ["jon-key"]


def test_filter_recipients_names_the_muted_rather_than_dropping_them(db_session, make_member):
    emma = make_member("Emma", pushover_user_key="emma-key")
    jon = make_member("Jon", pushover_user_key="jon-key")
    _pref(db_session, emma, EVENT_CHANGE, False)

    allowed, muted = filter_recipients(db_session, [emma, jon], EVENT_CHANGE)

    assert [m.name for m in allowed] == ["Jon"]
    assert muted == ["Emma"]


def test_filter_recipients_leaves_a_keyless_member_out_of_both_lists(
    db_session, make_member, unreachable
):
    """No key is *skipped*, and every caller reports that already."""
    emma = make_member("Emma", pushover_user_key="emma-key")

    allowed, muted = filter_recipients(db_session, [emma, unreachable], EVENT_REMINDER)

    assert [m.name for m in allowed] == ["Emma"]
    assert muted == []


def test_subscribers_are_only_the_people_who_asked(db_session, make_member, make_setting):
    make_setting("shopping_notify_enabled", "true")
    dad = make_member("Dad", pushover_user_key="dad-key")
    make_member("Mom", pushover_user_key="mom-key")
    make_member("Jake")  # eleven, no phone
    _pref(db_session, dad, SHOPPING_ADDED, True)

    assert [m.name for m in subscribers(db_session, SHOPPING_ADDED)] == ["Dad"]


# --- Writing preferences -------------------------------------------------------


def test_set_preferences_leaves_unmentioned_kinds_alone(db_session, reachable):
    set_preferences(db_session, reachable.id, {EVENT_CHANGE: False})
    set_preferences(db_session, reachable.id, {SHOPPING_ADDED: True})

    resolved = preferences(db_session, reachable.id)
    assert resolved[EVENT_CHANGE] is False
    assert resolved[SHOPPING_ADDED] is True
    assert resolved[EVENT_REMINDER] is True


def test_set_preferences_rewrites_an_existing_row_rather_than_adding_one(db_session, reachable):
    set_preferences(db_session, reachable.id, {EVENT_CHANGE: False})
    set_preferences(db_session, reachable.id, {EVENT_CHANGE: True})

    rows = (
        db_session.query(MemberNotificationPref)
        .filter(MemberNotificationPref.family_member_id == reachable.id)
        .all()
    )
    assert len(rows) == 1
    assert rows[0].enabled is True


def test_set_preferences_ignores_a_kind_rally_does_not_send(db_session, reachable):
    set_preferences(db_session, reachable.id, {"no_such_kind": True})

    assert db_session.query(MemberNotificationPref).count() == 0


# --- The overview --------------------------------------------------------------


def test_overview_splits_receiving_muted_and_keyless(db_session, make_member, make_setting):
    make_setting("shopping_notify_enabled", "true")
    dad = make_member("Dad", pushover_user_key="dad-key")
    emma = make_member("Emma", pushover_user_key="emma-key")
    make_member("Jake")
    _pref(db_session, dad, SHOPPING_ADDED, True)
    _pref(db_session, emma, EVENT_CHANGE, False)

    rows = {row["kind"]: row for row in overview(db_session)}

    assert rows[SHOPPING_ADDED]["receiving"] == ["Dad"]
    assert rows[SHOPPING_ADDED]["muted"] == ["Emma"]
    assert rows[SHOPPING_ADDED]["no_key"] == ["Jake"]
    assert rows[EVENT_CHANGE]["receiving"] == ["Dad"]
    assert rows[EVENT_CHANGE]["muted"] == ["Emma"]


def test_overview_reports_an_install_wide_switch_that_is_off(db_session, make_member, make_setting):
    make_member("Dad", pushover_user_key="dad-key")
    make_setting("prep_notify_enabled", "false")

    rows = {row["kind"]: row for row in overview(db_session)}

    assert rows[PREP_REFRESH]["enabled"] is False
    assert rows[PREP_REFRESH]["receiving"] == []
    assert rows[PREP_REFRESH]["muted"] == ["Dad"]


# --- The migration -------------------------------------------------------------
#
# Migrations are ordinarily verified by running them, but this one carries the
# rule the whole feature rests on — it writes **no rows**, so upgrading changes
# nobody's behavior — and that is worth an assertion rather than a habit.


def _load_migration():
    import importlib.util

    path = (
        pathlib.Path(__file__).resolve().parents[1]
        / "migrations"
        / "migrate_027_add_member_notification_prefs.py"
    )
    spec = importlib.util.spec_from_file_location("migrate_027", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_migration_creates_the_table_and_runs_twice_cleanly(tmp_path, monkeypatch):
    db_path = tmp_path / "rally.db"
    sqlite3.connect(db_path).close()  # a pre-existing install, without the table
    monkeypatch.setenv("RALLY_DB_PATH", str(db_path))
    migration = _load_migration()

    assert migration.migrate() is True
    assert migration.migrate() is True

    conn = sqlite3.connect(db_path)
    try:
        indexes = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' "
                "AND tbl_name='member_notification_prefs'"
            )
        }
        assert "ix_member_notification_prefs_unique" in indexes
        # No rows: shipping the feature is not the same as turning it on.
        assert conn.execute("SELECT COUNT(*) FROM member_notification_prefs").fetchone()[0] == 0
    finally:
        conn.close()


def test_the_migration_is_a_no_op_without_a_database(tmp_path, monkeypatch):
    monkeypatch.setenv("RALLY_DB_PATH", str(tmp_path / "missing.db"))

    assert _load_migration().migrate() is True
