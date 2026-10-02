"""Recurring packing list schedules: a packing list put on days by a repeating rule.

A schedule creates ordinary days through the summary's lookahead, the first
time anybody lists days. What is pinned here is the contract the UI promises:
the rule lands where ``rally.recurrence`` says it does, start and end dates
bound it, a removed day never comes back, pausing, editing or deleting a
schedule leaves the days it already made alone (except a new label, which
relabels them from today on), and a packing list repeats on one schedule at most.
"""

from datetime import UTC, date, datetime

import pytest

from rally import packing_lists as logic
from rally.models import PackingListDay, PackingListTemplateSchedule

# Thursday, 1 October 2026, mid-morning in Chicago. The lookahead runs through
# Thursday the 8th.
NOW = datetime(2026, 10, 1, 15, 0, tzinfo=UTC)
TODAY = "2026-10-01"
WEEKDAYS = {"freq": "weekly", "interval": 1, "weekdays": [0, 1, 2, 3, 4]}
SCHOOL_WEEK = ["2026-10-01", "2026-10-02", "2026-10-05", "2026-10-06", "2026-10-07", "2026-10-08"]


@pytest.fixture(autouse=True)
def _frozen(frozen_now, local_timezone):
    local_timezone("America/Chicago")
    frozen_now(NOW)


@pytest.fixture
def backpack(client):
    response = client.post("/api/packing-list-templates", json={"name": "School backpack"})
    assert response.status_code == 201, response.text
    return response.json()


def _schedule(client, packing_list_template_id, **extra):
    payload = {
        "packing_list_template_id": packing_list_template_id,
        "recurrence_type": "custom",
        "custom_rule": WEEKDAYS,
    }
    response = client.post("/api/packing-list-template-schedules", json={**payload, **extra})
    assert response.status_code == 201, response.text
    return response.json()


def _upcoming_dates(client):
    return [d["date"] for d in client.get("/api/packing-list-days").json()]


def test_every_weekday_fills_the_lookahead(client, backpack):
    schedule = _schedule(client, backpack["id"], label="Emma")

    days = client.get("/api/packing-list-days").json()
    assert [d["date"] for d in days] == SCHOOL_WEEK
    assert {d["label"] for d in days} == {"Emma"}
    assert {d["schedule_id"] for d in days} == {schedule["id"]}


def test_listing_again_creates_nothing_new(client, backpack, db_session):
    _schedule(client, backpack["id"])
    _upcoming_dates(client)
    _upcoming_dates(client)
    assert db_session.query(PackingListDay).count() == len(SCHOOL_WEEK)


def test_a_removed_day_stays_removed(client, backpack):
    _schedule(client, backpack["id"])
    friday = next(
        d for d in client.get("/api/packing-list-days").json() if d["date"] == "2026-10-02"
    )

    assert client.delete(f"/api/packing-list-days/{friday['id']}").status_code == 204
    assert "2026-10-02" not in _upcoming_dates(client)


def test_start_and_end_dates_bound_the_days(client, backpack):
    _schedule(client, backpack["id"], start_date="2026-10-05", end_date="2026-10-06")
    assert _upcoming_dates(client) == ["2026-10-05", "2026-10-06"]


def test_a_day_added_by_hand_is_kept_not_duplicated(client, backpack):
    manual = client.post(
        "/api/packing-list-days",
        json={
            "packing_list_template_id": backpack["id"],
            "date": "2026-10-02",
            "label": "Field trip",
        },
    ).json()
    _schedule(client, backpack["id"])

    friday = [d for d in client.get("/api/packing-list-days").json() if d["date"] == "2026-10-02"]
    assert [(d["id"], d["label"], d["schedule_id"]) for d in friday] == [
        (manual["id"], "Field trip", None)
    ]


def test_pausing_stops_new_days_and_leaves_the_made_ones(client, backpack, frozen_now):
    schedule = _schedule(client, backpack["id"])
    _upcoming_dates(client)

    paused = client.put(
        f"/api/packing-list-template-schedules/{schedule['id']}", json={"active": False}
    )
    assert paused.json()["active"] is False

    # A week later: the days made before the pause are past or still there,
    # and nothing new has appeared.
    frozen_now(datetime(2026, 10, 7, 15, 0, tzinfo=UTC))
    assert _upcoming_dates(client) == ["2026-10-07", "2026-10-08"]

    client.put(f"/api/packing-list-template-schedules/{schedule['id']}", json={"active": True})
    assert _upcoming_dates(client) == [
        "2026-10-07",
        "2026-10-08",
        "2026-10-09",
        "2026-10-12",
        "2026-10-13",
        "2026-10-14",
    ]


def test_a_paused_schedule_keeps_its_cadence(client, backpack, frozen_now):
    # Every two weeks on Thursday, starting today.
    rule = {"freq": "weekly", "interval": 2, "weekdays": [3]}
    schedule = _schedule(client, backpack["id"], custom_rule=rule)
    assert _upcoming_dates(client) == [TODAY]

    client.put(f"/api/packing-list-template-schedules/{schedule['id']}", json={"active": False})
    frozen_now(datetime(2026, 10, 9, 15, 0, tzinfo=UTC))
    client.put(f"/api/packing-list-template-schedules/{schedule['id']}", json={"active": True})
    # The 15th, two weeks on from the 1st, rather than restarting from today.
    assert _upcoming_dates(client) == ["2026-10-15"]


def test_a_schedule_resumed_after_a_missed_date_steps_along_its_cadence(
    client, backpack, frozen_now, db_session
):
    # Every two weeks on Thursday, starting today: the 1st, then the 15th, 29th.
    rule = {"freq": "weekly", "interval": 2, "weekdays": [3]}
    schedule = _schedule(client, backpack["id"], custom_rule=rule)
    assert _upcoming_dates(client) == [TODAY]

    client.put(f"/api/packing-list-template-schedules/{schedule['id']}", json={"active": False})
    # Paused through the 15th, which is skipped rather than put on a past day.
    # The 23rd looks ahead to the 30th, so the 29th is in reach.
    frozen_now(datetime(2026, 10, 23, 15, 0, tzinfo=UTC))
    client.put(f"/api/packing-list-template-schedules/{schedule['id']}", json={"active": True})
    assert _upcoming_dates(client) == ["2026-10-29"]
    # And nothing was put on the missed 15th, where it would sit in the archive.
    made = db_session.query(PackingListDay.date).filter(
        PackingListDay.schedule_id == schedule["id"]
    )
    assert sorted(d for (d,) in made) == [TODAY, "2026-10-29"]


def test_editing_leaves_made_days_and_shapes_later_ones(client, backpack, frozen_now):
    schedule = _schedule(client, backpack["id"])
    _upcoming_dates(client)

    response = client.put(
        f"/api/packing-list-template-schedules/{schedule['id']}",
        json={"recurrence_type": "weekly", "recurrence_day": 0, "custom_rule": None},
    )
    assert response.status_code == 200, response.text
    assert _upcoming_dates(client) == SCHOOL_WEEK

    frozen_now(datetime(2026, 10, 8, 15, 0, tzinfo=UTC))
    assert _upcoming_dates(client) == ["2026-10-08", "2026-10-12"]


def test_deleting_a_schedule_keeps_its_days(client, backpack, db_session):
    schedule = _schedule(client, backpack["id"])
    _upcoming_dates(client)

    assert (
        client.delete(f"/api/packing-list-template-schedules/{schedule['id']}").status_code == 204
    )
    days = client.get("/api/packing-list-days").json()
    assert [d["date"] for d in days] == SCHOOL_WEEK
    assert {d["schedule_id"] for d in days} == {None}


def test_deleting_the_packing_list_takes_its_schedules(client, backpack, db_session):
    _schedule(client, backpack["id"])
    assert client.delete(f"/api/packing-list-templates/{backpack['id']}").status_code == 204
    assert db_session.query(PackingListTemplateSchedule).count() == 0


def test_list_is_by_template_name_and_includes_paused(client, backpack):
    beach = client.post("/api/packing-list-templates", json={"name": "Beach day"}).json()
    first = _schedule(client, backpack["id"])
    _schedule(client, beach["id"], recurrence_type="weekly", recurrence_day=5, custom_rule=None)
    client.put(f"/api/packing-list-template-schedules/{first['id']}", json={"active": False})

    rows = client.get("/api/packing-list-template-schedules").json()
    assert [(r["template_name"], r["active"]) for r in rows] == [
        ("Beach day", True),
        ("School backpack", False),
    ]


@pytest.mark.parametrize(
    "payload",
    [
        {"recurrence_type": "weekly"},
        {"recurrence_type": "monthly", "recurrence_day": 32},
        {"recurrence_type": "custom"},
        {"recurrence_type": "custom", "custom_rule": {"freq": "weekly", "weekdays": []}},
        {"recurrence_type": "yearly"},
        {"recurrence_type": "daily", "start_date": "2026-10-09", "end_date": "2026-10-08"},
        {"recurrence_type": "daily", "start_date": "10/9/2026"},
    ],
)
def test_a_rule_nothing_can_read_is_422(client, backpack, payload):
    response = client.post(
        "/api/packing-list-template-schedules",
        json={"packing_list_template_id": backpack["id"], **payload},
    )
    assert response.status_code == 422


def test_an_edit_is_checked_against_the_merged_schedule(client, backpack):
    schedule = _schedule(client, backpack["id"], end_date="2026-12-31")
    base = f"/api/packing-list-template-schedules/{schedule['id']}"
    assert client.put(base, json={"recurrence_type": "weekly"}).status_code == 422
    assert client.put(base, json={"start_date": "2027-01-01"}).status_code == 422
    assert client.put(base, json={"packing_list_template_id": 999}).status_code == 422
    assert client.put(base, json={"end_date": None}).status_code == 200


def test_unknown_packing_list_or_schedule(client):
    payload = {"packing_list_template_id": 9, "recurrence_type": "daily"}
    assert client.post("/api/packing-list-template-schedules", json=payload).status_code == 422
    assert client.get("/api/packing-list-template-schedules/9").status_code == 404
    assert client.delete("/api/packing-list-template-schedules/9").status_code == 404


def test_the_summary_sees_scheduled_days(client, backpack, db_session):
    client.post(f"/api/packing-list-templates/{backpack['id']}/items", json={"name": "Lunchbox"})
    _schedule(client, backpack["id"])
    logic.process_schedules(db_session, date(2026, 10, 1))

    text = logic.summary_text(db_session, date(2026, 10, 1))
    assert '"School backpack" for Thursday, October 1' in text
    assert "Lunchbox" in text


def test_a_packing_list_repeats_on_one_schedule_at_most(client, backpack):
    first = _schedule(client, backpack["id"])
    second = client.post(
        "/api/packing-list-template-schedules",
        json={"packing_list_template_id": backpack["id"], "recurrence_type": "daily"},
    )
    assert second.status_code == 409
    assert second.json()["detail"]["id"] == first["id"]

    beach = client.post("/api/packing-list-templates", json={"name": "Beach day"}).json()
    other = _schedule(client, beach["id"])
    moved = client.put(
        f"/api/packing-list-template-schedules/{other['id']}",
        json={"packing_list_template_id": backpack["id"]},
    )
    assert moved.status_code == 409


def test_the_packing_list_listing_carries_its_schedule(client, backpack):
    schedule = _schedule(client, backpack["id"], label="Emma")
    row = client.get("/api/packing-list-templates").json()[0]
    assert row["schedule"]["id"] == schedule["id"]
    assert (row["schedule"]["label"], row["schedule"]["active"]) == ("Emma", True)

    client.delete(f"/api/packing-list-template-schedules/{schedule['id']}")
    assert client.get("/api/packing-list-templates").json()[0]["schedule"] is None


def test_a_one_off_day_and_a_schedule_live_side_by_side(client, backpack):
    """Swim at Nana's every Sunday, and this Saturday too."""
    _schedule(client, backpack["id"], recurrence_type="weekly", recurrence_day=6, custom_rule=None)
    saturday = client.post(
        "/api/packing-list-days",
        json={
            "packing_list_template_id": backpack["id"],
            "date": "2026-10-03",
            "label": "Cousins visiting",
        },
    ).json()

    days = client.get("/api/packing-list-days").json()
    assert [(d["date"], d["schedule_id"] is not None) for d in days] == [
        ("2026-10-03", False),
        ("2026-10-04", True),
    ]
    assert days[0]["id"] == saturday["id"]


def test_a_new_label_relabels_the_days_it_added_from_today_on(client, backpack, db_session):
    schedule = _schedule(client, backpack["id"], label="Emma")
    _upcoming_dates(client)
    past = PackingListDay(
        packing_list_template_id=backpack["id"],
        date="2026-09-30",
        label="Emma",
        schedule_id=schedule["id"],
    )
    by_hand = client.post(
        "/api/packing-list-days",
        json={
            "packing_list_template_id": backpack["id"],
            "date": "2026-10-09",
            "label": "Field trip",
        },
    ).json()
    db_session.add(past)
    db_session.commit()

    client.put(
        f"/api/packing-list-template-schedules/{schedule['id']}", json={"label": "Emma and Jake"}
    )

    days = client.get("/api/packing-list-days").json()
    scheduled = {d["label"] for d in days if d["schedule_id"] == schedule["id"]}
    assert scheduled == {"Emma and Jake"}
    assert next(d for d in days if d["id"] == by_hand["id"])["label"] == "Field trip"
    db_session.refresh(past)
    assert past.label == "Emma"

    # A change that leaves the label alone leaves the days' labels alone.
    client.put(f"/api/packing-list-template-schedules/{schedule['id']}", json={"active": False})
    days = client.get("/api/packing-list-days").json()
    assert {d["label"] for d in days if d["schedule_id"] == schedule["id"]} == {"Emma and Jake"}


def test_daily_weekdays_only_skips_the_weekend(client, backpack):
    _schedule(client, backpack["id"], recurrence_type="daily", custom_rule={"weekdays_only": True})
    assert _upcoming_dates(client) == SCHOOL_WEEK


def test_custom_every_n_days_weekdays_only_moves_a_weekend_to_monday(client, backpack):
    # Every 2 days from Thursday: Thu 1, Sat 3 -> Mon 5, Wed 7.
    rule = {"freq": "daily", "interval": 2, "weekdays_only": True}
    _schedule(client, backpack["id"], custom_rule=rule)
    assert _upcoming_dates(client) == ["2026-10-01", "2026-10-05", "2026-10-07"]
