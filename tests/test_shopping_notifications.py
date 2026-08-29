"""Tests for the push that goes out when things are added to the shopping list.

The transport is always stubbed — no test may reach the real API. What is under
test is the batching: nine items added while walking the pantry are one push,
not nine, and the watermark is the send-once guarantee that makes a pass safe
to run every minute forever.
"""

from datetime import UTC, datetime, timedelta

import pytest

from rally.models import MemberNotificationPref
from rally.notification_prefs import SHOPPING_ADDED
from rally.shopping_notifications import (
    WATERMARK_KEY,
    build_message,
    run_once_per_minute,
    scan_once,
    watermark,
)

NOW = datetime(2026, 8, 19, 17, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _tz(local_timezone):
    local_timezone("America/Chicago")


@pytest.fixture
def enabled(make_setting):
    """The install-wide switch, which is off until somebody turns it on."""
    make_setting("shopping_notify_enabled", "true")


@pytest.fixture
def token(make_setting):
    make_setting("pushover_app_token", "app-token")
    return "app-token"


@pytest.fixture
def subscriber(db_session, make_member):
    """Dad, who ticked *Shopping list additions*."""
    member = make_member("Dad", pushover_user_key="dad-key")
    db_session.add(
        MemberNotificationPref(
            family_member_id=member.id, kind=SHOPPING_ADDED, enabled=True
        )
    )
    db_session.commit()
    return member


@pytest.fixture
def watermarked(make_setting):
    """A watermark far enough back that the fixture items are all after it."""

    def _set(moment: datetime = NOW - timedelta(hours=1)):
        make_setting(WATERMARK_KEY, moment.isoformat())
        return moment

    return _set


def _added(make_shopping_item, name, minutes_ago, **kwargs):
    """An item created ``minutes_ago`` before ``NOW``."""
    return make_shopping_item(
        name,
        created_at=(NOW - timedelta(minutes=minutes_ago)).replace(tzinfo=None),
        **kwargs,
    )


# --- Batching ------------------------------------------------------------------


def test_a_burst_is_held_until_the_adding_stops(
    db_session,
    enabled,
    token,
    subscriber,
    watermarked,
    make_shopping_item,
    mock_pushover,
):
    watermarked()
    _added(make_shopping_item, "Milk", 6)
    _added(make_shopping_item, "Eggs", 1)  # still adding

    result = scan_once(db_session, NOW)

    assert mock_pushover.sent == []
    assert result.skipped_reason == "the batch is still settling"
    assert watermark(db_session) == NOW - timedelta(hours=1)


def test_a_settled_batch_sends_exactly_one_push(
    db_session,
    enabled,
    token,
    subscriber,
    watermarked,
    make_shopping_item,
    mock_pushover,
):
    watermarked()
    for offset, name in enumerate(["Milk", "Eggs", "Bread"]):
        _added(make_shopping_item, name, 10 + offset)

    result = scan_once(db_session, NOW)

    assert len(mock_pushover.sent) == 1
    assert mock_pushover.sent[0]["title"] == "Shopping list"
    assert mock_pushover.sent[0]["message"] == "Bread, Eggs and Milk added"
    assert result.sent_to == ["Dad"]


def test_the_watermark_advances_past_the_batch_so_it_sends_once(
    db_session,
    enabled,
    token,
    subscriber,
    watermarked,
    make_shopping_item,
    mock_pushover,
):
    watermarked()
    newest = _added(make_shopping_item, "Milk", 10)

    scan_once(db_session, NOW)
    mock_pushover.sent.clear()
    scan_once(db_session, NOW)

    assert mock_pushover.sent == []
    assert watermark(db_session) == newest.created_at.replace(tzinfo=UTC)


def test_an_item_purchased_inside_the_settle_window_is_left_out(
    db_session,
    enabled,
    token,
    subscriber,
    watermarked,
    make_shopping_item,
    mock_pushover,
):
    """Bought before anybody heard about it is not news."""
    watermarked()
    _added(make_shopping_item, "Milk", 10)
    _added(make_shopping_item, "Eggs", 9, completed=True)

    scan_once(db_session, NOW)

    assert mock_pushover.sent[0]["message"] == "Milk added"


def test_a_batch_emptied_by_purchases_sends_nothing_and_still_advances(
    db_session,
    enabled,
    token,
    subscriber,
    watermarked,
    make_shopping_item,
    mock_pushover,
):
    watermarked()
    newest = _added(make_shopping_item, "Milk", 10, completed=True)

    result = scan_once(db_session, NOW)

    assert mock_pushover.sent == []
    assert result.skipped_reason == "everything added was already purchased"
    assert watermark(db_session) == newest.created_at.replace(tzinfo=UTC)


def test_the_settle_window_is_configurable(
    db_session,
    enabled,
    token,
    subscriber,
    watermarked,
    make_setting,
    make_shopping_item,
    mock_pushover,
):
    watermarked()
    make_setting("shopping_notify_settle_minutes", "30")
    _added(make_shopping_item, "Milk", 10)

    assert scan_once(db_session, NOW).skipped_reason == "the batch is still settling"

    make_setting("shopping_notify_settle_minutes", "5")
    assert scan_once(db_session, NOW).sent_to == ["Dad"]


# --- The message ---------------------------------------------------------------


def test_a_small_batch_names_everything(db_session, make_shopping_item):
    items = [make_shopping_item(name) for name in ["Milk", "Eggs", "Bread"]]

    assert build_message(db_session, items) == "Milk, Eggs and Bread added"


def test_one_item_reads_as_one_item(db_session, make_shopping_item):
    assert build_message(db_session, [make_shopping_item("Milk")]) == "Milk added"


def test_a_long_batch_names_three_and_counts_the_rest(db_session, make_shopping_item):
    items = [make_shopping_item(f"Item {n}") for n in range(9)]

    assert build_message(db_session, items) == (
        "Item 0, Item 1, Item 2 and 6 more added — open Rally for the full list"
    )


def test_a_batch_that_shares_a_store_says_where(
    db_session, make_store, make_shopping_item
):
    store = make_store("Costco")
    items = [make_shopping_item(name, store_id=store.id) for name in ["Milk", "Eggs"]]

    assert build_message(db_session, items) == "Milk and Eggs added · at Costco"


def test_a_mixed_batch_names_no_store(db_session, make_store, make_shopping_item):
    store = make_store("Costco")
    items = [
        make_shopping_item("Milk", store_id=store.id),
        make_shopping_item("Eggs"),
    ]

    assert build_message(db_session, items) == "Milk and Eggs added"


def test_the_catch_all_is_not_a_place(db_session, make_shopping_item):
    """Every item shares ``store_id IS NULL``, but "at Anywhere" is not a store."""
    items = [make_shopping_item(name) for name in ["Milk", "Eggs"]]

    assert build_message(db_session, items) == "Milk and Eggs added"


def test_a_very_long_batch_stays_under_the_pushover_ceiling(
    db_session, make_shopping_item
):
    items = [make_shopping_item("x" * 600) for _ in range(4)]

    assert len(build_message(db_session, items)) <= 1024


# --- Clean no-ops --------------------------------------------------------------


def test_the_switch_off_sends_nothing(
    db_session, token, subscriber, watermarked, make_shopping_item, mock_pushover
):
    watermarked()
    _added(make_shopping_item, "Milk", 10)

    result = scan_once(db_session, NOW)

    assert mock_pushover.sent == []
    assert result.skipped_reason == "shopping notifications are turned off"


def test_no_application_token_holds_the_batch_rather_than_dropping_it(
    db_session,
    enabled,
    subscriber,
    watermarked,
    make_shopping_item,
    mock_pushover,
    make_setting,
):
    """A configuration gap is temporary; the batch is still owed."""
    watermarked()
    _added(make_shopping_item, "Milk", 10)

    result = scan_once(db_session, NOW)
    assert result.skipped_reason == "no Pushover application token configured"
    assert watermark(db_session) == NOW - timedelta(hours=1)

    make_setting("pushover_app_token", "app-token")
    assert scan_once(db_session, NOW).sent_to == ["Dad"]


def test_nobody_opted_in_advances_rather_than_hoarding_a_backlog(
    db_session,
    enabled,
    token,
    make_member,
    watermarked,
    make_shopping_item,
    mock_pushover,
):
    """An empty audience is an answer, not a failure.

    The first person to tick the box hears about what happens next, rather than
    inheriting every item added since the switch went on.
    """
    make_member("Mom", pushover_user_key="mom-key")
    watermarked()
    newest = _added(make_shopping_item, "Milk", 10)

    result = scan_once(db_session, NOW)

    assert mock_pushover.sent == []
    assert result.skipped_reason == "nobody has asked for shopping list pushes"
    assert watermark(db_session) == newest.created_at.replace(tzinfo=UTC)


def test_a_failed_send_leaves_the_watermark_so_the_next_pass_retries(
    db_session,
    enabled,
    token,
    subscriber,
    watermarked,
    make_shopping_item,
    mock_pushover,
):
    watermarked()
    _added(make_shopping_item, "Milk", 10)
    mock_pushover.fail_with("service unavailable")

    result = scan_once(db_session, NOW)
    assert result.failed == ["Dad"]
    assert watermark(db_session) == NOW - timedelta(hours=1)

    mock_pushover.succeed()
    assert scan_once(db_session, NOW).sent_to == ["Dad"]


def test_nothing_added_since_the_last_pass_is_silent(
    db_session, enabled, token, subscriber, watermarked, mock_pushover
):
    watermarked()

    assert (
        scan_once(db_session, NOW).skipped_reason == "nothing added since the last pass"
    )
    assert mock_pushover.sent == []


def test_the_first_pass_starts_from_now_rather_than_announcing_the_existing_list(
    db_session, enabled, token, subscriber, make_shopping_item, mock_pushover
):
    """Turning the switch on is not a request to hear about last month."""
    _added(make_shopping_item, "Milk", 600)

    result = scan_once(db_session, NOW)

    assert mock_pushover.sent == []
    assert result.skipped_reason == "starting from now"
    assert watermark(db_session) == NOW


# --- The opportunistic hook ----------------------------------------------------


def test_the_write_path_runs_at_most_one_pass_a_minute(
    db_session,
    enabled,
    token,
    subscriber,
    watermarked,
    make_shopping_item,
    mock_pushover,
):
    watermarked()
    _added(make_shopping_item, "Milk", 10)

    assert run_once_per_minute(db_session, NOW).sent_to == ["Dad"]

    _added(make_shopping_item, "Eggs", 10)
    second = run_once_per_minute(db_session, NOW)

    assert second.skipped_reason == "already checked this minute"
    assert len(mock_pushover.sent) == 1


def test_adding_an_item_announces_the_batch_before_it(
    client,
    db_session,
    enabled,
    token,
    subscriber,
    watermarked,
    make_shopping_item,
    mock_pushover,
):
    """The honest hook: a write, not a read.

    The pass runs *before* the insert, so the item being added does not keep
    its own batch warm forever — which is what would happen if the hook ran
    afterwards, leaving a ``dev``-served instance permanently silent.
    """
    watermarked()
    _added(make_shopping_item, "Milk", 10)

    response = client.post("/api/shopping/items", json={"name": "Eggs"})

    assert response.status_code == 201
    assert [push["message"] for push in mock_pushover.sent] == ["Milk added"]


def test_adding_an_item_never_pushes_that_item_immediately(
    client, db_session, enabled, token, subscriber, watermarked, mock_pushover
):
    """The settle window is the whole point: one push per burst, not per item."""
    watermarked()

    client.post("/api/shopping/items", json={"name": "Milk"})

    assert mock_pushover.sent == []
