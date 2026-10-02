"""Item history counts what was packed (#252).

``times_added`` is how many past days a name was on a packing list. Typing a
name makes it suggestible at 0; ``count_packed_days`` adds each day once its
date is over, as the day finally reads. Because a day only counts once it is
over — and a past day cannot be deleted — nothing ever has to be undone.
"""

from datetime import UTC, date, datetime, timedelta

import pytest

from rally import packing_lists as logic
from rally.models import (
    PackingListDay,
    PackingListDayItem,
    PackingListItemHistory,
    Setting,
)

# Thursday, 1 October 2026, mid-morning in Chicago.
NOW = datetime(2026, 10, 1, 15, 0, tzinfo=UTC)
TODAY = date(2026, 10, 1)
MARKER = logic.COUNTED_THROUGH_SETTING


@pytest.fixture(autouse=True)
def _frozen(frozen_now, local_timezone):
    local_timezone("America/Chicago")
    frozen_now(NOW)


@pytest.fixture
def swim(client, make_member):
    emma = make_member(name="Emma")
    template = client.post("/api/packing-list-templates", json={"name": "Swim"}).json()
    for name in ("Goggles", "Towel"):
        client.post(
            f"/api/packing-list-templates/{template['id']}/items",
            json={"name": name, "owner_id": emma.id, "bag": "Pool bag"},
        )
    return template


def _counts(db_session):
    db_session.expire_all()
    return {r.name_key: r.times_added for r in db_session.query(PackingListItemHistory)}


def _marker(db_session, value):
    row = db_session.get(Setting, MARKER)
    if row:
        row.value = value
    else:
        db_session.add(Setting(key=MARKER, value=value))
    db_session.commit()


def _day(db_session, template_id, day):
    row = PackingListDay(packing_list_template_id=template_id, date=day)
    db_session.add(row)
    db_session.commit()
    return row


def test_a_day_counts_once_its_date_is_over(db_session, swim):
    _marker(db_session, "2026-09-25")
    _day(db_session, swim["id"], "2026-09-26")
    _day(db_session, swim["id"], "2026-10-01")  # today: not over
    _day(db_session, swim["id"], "2026-10-03")

    assert logic.count_packed_days(db_session, TODAY) == 1

    assert _counts(db_session) == {"goggles": 1, "towel": 1}
    assert db_session.get(Setting, MARKER).value == "2026-09-30"


def test_each_day_counts_exactly_once(db_session, swim):
    _marker(db_session, "2026-09-25")
    _day(db_session, swim["id"], "2026-09-26")
    logic.count_packed_days(db_session, TODAY)

    assert logic.count_packed_days(db_session, TODAY) == 0
    assert logic.count_packed_days(db_session, TODAY + timedelta(days=1)) == 0
    assert _counts(db_session) == {"goggles": 1, "towel": 1}


def test_a_quiet_week_is_caught_up_in_one_pass(db_session, swim):
    _marker(db_session, "2026-09-20")
    for day in ("2026-09-21", "2026-09-24", "2026-09-28"):
        _day(db_session, swim["id"], day)

    assert logic.count_packed_days(db_session, TODAY) == 3
    assert _counts(db_session) == {"goggles": 3, "towel": 3}


def test_a_day_counts_as_it_finally_read(client, db_session, swim):
    _marker(db_session, "2026-09-25")
    day = _day(db_session, swim["id"], "2026-09-26")
    goggles, towel = client.get(f"/api/packing-list-templates/{swim['id']}").json()["items"]
    db_session.add_all(
        [
            # Towel removed on this day; goggles renamed to a name in history.
            PackingListDayItem(
                day_id=day.id, template_item_id=towel["id"], name="Towel", removed=True
            ),
            PackingListDayItem(day_id=day.id, template_item_id=goggles["id"], name="Blue goggles"),
            PackingListDayItem(day_id=day.id, name="Sun hat"),
            PackingListDayItem(day_id=day.id, name="Sun hat"),
            PackingListItemHistory(name="Blue goggles", name_key="blue goggles", times_added=0),
            PackingListItemHistory(name="Sun hat", name_key="sun hat", times_added=0),
        ]
    )
    db_session.commit()

    logic.count_packed_days(db_session, TODAY)

    # Unchecked items count too: the count is what was on the list.
    assert _counts(db_session) == {"goggles": 0, "towel": 0, "blue goggles": 1, "sun hat": 2}


def test_a_templateless_day_counts(client, db_session, swim):
    _marker(db_session, "2026-09-25")
    day = _day(db_session, swim["id"], "2026-09-26")
    client.delete(f"/api/packing-list-templates/{swim['id']}")
    assert db_session.get(PackingListDay, day.id).packing_list_template_id is None

    logic.count_packed_days(db_session, TODAY)

    assert _counts(db_session) == {"goggles": 1, "towel": 1}


def test_counting_creates_no_row_and_leaves_what_was_typed(client, db_session, swim):
    _marker(db_session, "2026-09-25")
    day = _day(db_session, swim["id"], "2026-09-26")
    db_session.add(PackingListDayItem(day_id=day.id, name="Snacks"))  # never typed
    row = db_session.query(PackingListItemHistory).filter_by(name_key="towel").one()
    suggestion = client.get("/api/packing-list-items/suggestions?q=goggles").json()[0]
    client.delete(f"/api/packing-list-items/suggestions/{suggestion['id']}")  # forgotten
    before = (row.owner_id, row.bag_id, row.last_added_at)
    db_session.commit()

    logic.count_packed_days(db_session, TODAY)

    assert _counts(db_session) == {"towel": 1}
    row = db_session.query(PackingListItemHistory).filter_by(name_key="towel").one()
    assert (row.owner_id, row.bag_id, row.last_added_at) == before


def test_without_a_marker_it_starts_at_yesterday(db_session, swim):
    _day(db_session, swim["id"], "2026-09-26")

    assert logic.count_packed_days(db_session, TODAY) == 0
    assert db_session.get(Setting, MARKER).value == "2026-09-30"
    assert _counts(db_session) == {"goggles": 0, "towel": 0}


def test_listing_the_days_runs_the_pass(client, db_session, swim):
    _marker(db_session, "2026-09-25")
    _day(db_session, swim["id"], "2026-09-26")

    client.get("/api/packing-list-days")

    assert _counts(db_session) == {"goggles": 1, "towel": 1}


def test_nothing_else_changes_a_count(client, db_session, swim):
    """Taking a list off a day, moving one, editing an owner or bag, checking,
    reordering and converting all leave counts alone — a day only counts once
    it is over."""
    day = client.post(
        "/api/packing-list-days",
        json={"packing_list_template_id": swim["id"], "date": "2026-10-03"},
    ).json()
    other = client.post(
        "/api/packing-list-days",
        json={"packing_list_template_id": swim["id"], "date": "2026-10-04"},
    ).json()
    goggles = day["items"][0]
    base = f"/api/packing-list-days/{day['id']}"
    client.put(f"{base}/template-items/{goggles['id']}", json={"checked": True, "bag": "Car"})
    client.put(base, json={"date": "2026-10-05"})
    client.post(
        f"{base}/items/reorder",
        json={"view": "owner", "key": None, "items": [{"source": "template", "id": goggles["id"]}]},
    )
    client.delete(f"/api/packing-list-days/{other['id']}")
    client.delete(f"/api/packing-list-templates/{swim['id']}")
    client.get("/api/packing-list-days")

    assert _counts(db_session) == {"goggles": 0, "towel": 0}


def test_suggestions_rank_by_days_packed(client, db_session, swim):
    _marker(db_session, "2026-09-25")
    lone = client.post("/api/packing-list-templates", json={"name": "Gym"}).json()
    client.post(f"/api/packing-list-templates/{lone['id']}/items", json={"name": "Gloves"})
    for day in ("2026-09-26", "2026-09-27"):
        _day(db_session, swim["id"], day)
    _day(db_session, lone["id"], "2026-09-28")
    client.get("/api/packing-list-days")

    found = client.get("/api/packing-list-items/suggestions?q=g").json()
    # Both prefix matches; goggles was packed on two days, gloves on one.
    assert [(s["name"], s["times_added"]) for s in found] == [("Goggles", 2), ("Gloves", 1)]
