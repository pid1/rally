"""Tests for ``rally.recurrence`` — recurrence date math and instance generation.

The public helpers (`get_last/next/first_recurrence_date`) and the custom-rule
helpers (`_next/_last/_first_custom`, `_find_nth_weekday_in_month`) are pure
functions of a date, so they're unit-tested without a DB. `process_recurring_todos`
is exercised against the in-memory database with a frozen clock.

Date anchors used below (2026 is not a leap year):
- 2026-01-01 is a Thursday; Mondays that month fall on Jan 5, 12, 19, 26.
- 2026-02 has 28 days; Mondays fall on Feb 2, 9, 16, 23 (only four).
- 2025-12 has five Mondays: Dec 1, 8, 15, 22, 29.
"""

from datetime import UTC, date, datetime

import pytest

from rally.models import RecurringTodo, Todo
from rally.recurrence import (
    _find_nth_weekday_in_month,
    _first_custom,
    _last_custom,
    _next_custom,
    get_first_recurrence_date,
    get_last_recurrence_date,
    get_next_recurrence_date,
    process_recurring_todos,
)


def rt(recurrence_type="daily", *, recurrence_day=None, custom_rule=None) -> RecurringTodo:
    """Build an unsaved RecurringTodo for the pure-function tests."""
    return RecurringTodo(
        title="T",
        recurrence_type=recurrence_type,
        recurrence_day=recurrence_day,
        custom_rule=custom_rule,
    )


# --- _find_nth_weekday_in_month ------------------------------------------------


def test_find_nth_weekday_ordinals():
    # Mondays (weekday 0) in Jan 2026: 5, 12, 19, 26.
    assert _find_nth_weekday_in_month(2026, 1, "first", 0) == date(2026, 1, 5)
    assert _find_nth_weekday_in_month(2026, 1, "second", 0) == date(2026, 1, 12)
    assert _find_nth_weekday_in_month(2026, 1, "third", 0) == date(2026, 1, 19)
    assert _find_nth_weekday_in_month(2026, 1, "fourth", 0) == date(2026, 1, 26)


def test_find_nth_weekday_last_with_five_occurrences():
    # Dec 2025 has five Mondays; "last" must resolve to the fifth (Dec 29).
    assert _find_nth_weekday_in_month(2025, 12, "last", 0) == date(2025, 12, 29)


def test_find_nth_weekday_last_with_four_occurrences():
    # Feb 2026 has four Mondays; "last" is Feb 23.
    assert _find_nth_weekday_in_month(2026, 2, "last", 0) == date(2026, 2, 23)


@pytest.mark.xfail(
    reason="Docstring promises a nonexistent ordinal falls back to the last "
    "occurrence, but 'fifth' is absent from ordinal_map so it collapses to "
    "index 0 (first). Likely a missing 'fifth': 4 map entry.",
)
def test_find_nth_weekday_fifth_falls_back_to_last():
    # Feb 2026 has only four Mondays, so a requested "fifth" Monday should fall
    # back to the last valid occurrence (Feb 23) per the documented behaviour.
    assert _find_nth_weekday_in_month(2026, 2, "fifth", 0) == date(2026, 2, 23)


# --- get_last_recurrence_date --------------------------------------------------


def test_last_daily_is_today():
    assert get_last_recurrence_date(rt("daily"), date(2026, 1, 7)) == date(2026, 1, 7)


def test_last_weekly_walks_back_to_weekday():
    # today Wed Jan 7; most recent Monday (day 0) is Jan 5.
    assert get_last_recurrence_date(rt("weekly", recurrence_day=0), date(2026, 1, 7)) == date(
        2026, 1, 5
    )


def test_last_monthly_current_month_when_day_passed():
    assert get_last_recurrence_date(rt("monthly", recurrence_day=15), date(2026, 1, 20)) == date(
        2026, 1, 15
    )


def test_last_monthly_previous_month_when_day_not_yet_reached():
    assert get_last_recurrence_date(rt("monthly", recurrence_day=15), date(2026, 1, 10)) == date(
        2025, 12, 15
    )


# --- get_next_recurrence_date (strictly after) ---------------------------------


def test_next_daily_advances_one_day():
    assert get_next_recurrence_date(rt("daily"), date(2026, 1, 1)) == date(2026, 1, 2)


def test_next_weekly_same_weekday_advances_a_full_week():
    # after Mon Jan 5, next Monday is strictly after -> Jan 12, not Jan 5.
    assert get_next_recurrence_date(rt("weekly", recurrence_day=0), date(2026, 1, 5)) == date(
        2026, 1, 12
    )


def test_next_monthly_same_month_when_day_still_ahead():
    assert get_next_recurrence_date(rt("monthly", recurrence_day=15), date(2026, 1, 10)) == date(
        2026, 1, 15
    )


def test_next_monthly_clamps_to_short_month_end():
    # day 31 after Jan 31 -> February, clamped to Feb 28 (2026 not leap).
    assert get_next_recurrence_date(rt("monthly", recurrence_day=31), date(2026, 1, 31)) == date(
        2026, 2, 28
    )


def test_next_monthly_rolls_over_year():
    assert get_next_recurrence_date(rt("monthly", recurrence_day=15), date(2026, 12, 20)) == date(
        2027, 1, 15
    )


# --- get_first_recurrence_date -------------------------------------------------


def test_first_daily_is_today():
    assert get_first_recurrence_date(rt("daily"), date(2026, 1, 7)) == date(2026, 1, 7)


def test_first_weekly_upcoming_weekday_in_current_week():
    # today Wed Jan 7; next Friday (day 4) is Jan 9.
    assert get_first_recurrence_date(rt("weekly", recurrence_day=4), date(2026, 1, 7)) == date(
        2026, 1, 9
    )


def test_first_monthly_uses_current_period_clamped():
    # today Feb 10, day 31 -> clamped to Feb 28 (current month, not backdated).
    assert get_first_recurrence_date(rt("monthly", recurrence_day=31), date(2026, 2, 10)) == date(
        2026, 2, 28
    )


# --- Custom rules: _next_custom ------------------------------------------------


def test_next_custom_daily_interval():
    assert _next_custom({"freq": "daily", "interval": 2}, date(2026, 1, 1)) == date(2026, 1, 3)


def test_next_custom_daily_weekdays_only_skips_weekend():
    # after Fri Jan 2 -> Sat Jan 3 skipped forward to Mon Jan 5.
    rule = {"freq": "daily", "interval": 1, "weekdays_only": True}
    assert _next_custom(rule, date(2026, 1, 2)) == date(2026, 1, 5)


def test_next_custom_weekly_later_this_week():
    rule = {"freq": "weekly", "weekdays": [0, 2, 4], "interval": 1}
    # after Mon Jan 5 -> next listed weekday this week is Wed Jan 7.
    assert _next_custom(rule, date(2026, 1, 5)) == date(2026, 1, 7)


def test_next_custom_weekly_wraps_to_next_cycle_with_interval():
    rule = {"freq": "weekly", "weekdays": [0, 2, 4], "interval": 2}
    # after Fri Jan 9, none later this week -> first weekday two weeks on: Jan 19.
    assert _next_custom(rule, date(2026, 1, 9)) == date(2026, 1, 19)


def test_next_custom_monthly_day_advances_and_clamps():
    rule = {"freq": "monthly", "mode": "day", "day": 31, "interval": 1}
    assert _next_custom(rule, date(2026, 1, 31)) == date(2026, 2, 28)


def test_next_custom_monthly_weekday():
    rule = {"freq": "monthly", "mode": "weekday", "ordinal": "second", "weekday": 0, "interval": 1}
    # after Jan 20, second Monday of Jan (12th) has passed -> second Monday of Feb: Feb 9.
    assert _next_custom(rule, date(2026, 1, 20)) == date(2026, 2, 9)


# --- Custom rules: _last_custom ------------------------------------------------


def test_last_custom_daily_is_today():
    assert _last_custom({"freq": "daily"}, date(2026, 1, 7)) == date(2026, 1, 7)


def test_last_custom_weekly_walks_back_within_week():
    rule = {"freq": "weekly", "weekdays": [0, 2, 4], "interval": 1}
    # today Sun Jan 11; most recent listed weekday is Fri Jan 9.
    assert _last_custom(rule, date(2026, 1, 11)) == date(2026, 1, 9)


def test_last_custom_weekly_wraps_to_previous_cycle():
    rule = {"freq": "weekly", "weekdays": [2, 4], "interval": 1}
    # today Mon Jan 5; none on/before Monday this week -> previous cycle's last: Fri Jan 2.
    assert _last_custom(rule, date(2026, 1, 5)) == date(2026, 1, 2)


def test_last_custom_monthly_day_previous_month():
    rule = {"freq": "monthly", "mode": "day", "day": 15}
    assert _last_custom(rule, date(2026, 1, 10)) == date(2025, 12, 15)


def test_last_custom_monthly_weekday():
    rule = {"freq": "monthly", "mode": "weekday", "ordinal": "second", "weekday": 0}
    # today Jan 20; second Monday of Jan (12th) is on/before today.
    assert _last_custom(rule, date(2026, 1, 20)) == date(2026, 1, 12)


def test_last_custom_monthly_weekday_previous_month():
    rule = {"freq": "monthly", "mode": "weekday", "ordinal": "second", "weekday": 0}
    # today Jan 5; second Monday of Jan (12th) is after today -> Dec 2025's second Monday.
    assert _last_custom(rule, date(2026, 1, 5)) == date(2025, 12, 8)


# --- Custom rules: _first_custom -----------------------------------------------


def test_first_custom_weekly_upcoming_this_week():
    rule = {"freq": "weekly", "weekdays": [0, 2, 4]}
    # today Wed Jan 7 -> Wed itself qualifies (>= current weekday).
    assert _first_custom(rule, date(2026, 1, 7)) == date(2026, 1, 7)


def test_first_custom_monthly_day_advances_when_passed():
    rule = {"freq": "monthly", "mode": "day", "day": 15, "interval": 1}
    # today Jan 20, the 15th has passed -> next month's 15th.
    assert _first_custom(rule, date(2026, 1, 20)) == date(2026, 2, 15)


def test_first_custom_monthly_weekday_current_month():
    rule = {"freq": "monthly", "mode": "weekday", "ordinal": "second", "weekday": 0, "interval": 1}
    # today Jan 1; second Monday of Jan (12th) is still ahead -> Jan 12.
    assert _first_custom(rule, date(2026, 1, 1)) == date(2026, 1, 12)


# --- process_recurring_todos (integration) -------------------------------------

FROZEN = datetime(2026, 3, 15, 12, 0, tzinfo=UTC)  # a Sunday


def _instances(db, rt_id):
    return db.query(Todo).filter(Todo.recurring_todo_id == rt_id).all()


def test_process_generates_first_instance(db_session, make_recurring_todo, frozen_now):
    frozen_now(FROZEN)
    template = make_recurring_todo("Vitamins", recurrence_type="daily", has_due_date=True)

    created = process_recurring_todos(db_session)

    assert created == 1
    instances = _instances(db_session, template.id)
    assert len(instances) == 1
    assert instances[0].due_date == "2026-03-15"
    assert instances[0].completed is False
    assert template.last_generated_date == "2026-03-15"


def test_process_no_due_date_when_template_has_none(db_session, make_recurring_todo, frozen_now):
    frozen_now(FROZEN)
    template = make_recurring_todo("Stretch", recurrence_type="daily", has_due_date=False)

    process_recurring_todos(db_session)

    assert _instances(db_session, template.id)[0].due_date is None


def test_process_skips_when_open_instance_exists(
    db_session, make_recurring_todo, make_todo, frozen_now
):
    frozen_now(FROZEN)
    template = make_recurring_todo("Vitamins", recurrence_type="daily", has_due_date=True)
    make_todo("Vitamins", completed=False, completed_at=None, recurring_todo_id=template.id)

    created = process_recurring_todos(db_session)

    assert created == 0
    # Still just the single open instance we seeded.
    assert len(_instances(db_session, template.id)) == 1


def test_process_advances_from_last_generated_date(db_session, make_recurring_todo, frozen_now):
    frozen_now(FROZEN)
    # Previous instance was completed; last_generated_date tracks Mar 14.
    template = make_recurring_todo(
        "Vitamins",
        recurrence_type="daily",
        has_due_date=True,
        last_generated_date="2026-03-14",
    )

    created = process_recurring_todos(db_session)

    assert created == 1
    assert template.last_generated_date == "2026-03-15"
    due_dates = {t.due_date for t in _instances(db_session, template.id)}
    assert due_dates == {"2026-03-15"}


def test_process_reference_advances_past_completed_reschedule(
    db_session, make_recurring_todo, make_todo, frozen_now
):
    frozen_now(FROZEN)
    # last_generated_date lags behind a completed instance that was rescheduled
    # to a later due date; the reference must advance to that later date.
    template = make_recurring_todo(
        "Vitamins",
        recurrence_type="daily",
        has_due_date=True,
        last_generated_date="2026-03-10",
    )
    make_todo(
        "Vitamins",
        completed=True,
        due_date="2026-03-14",
        recurring_todo_id=template.id,
    )

    created = process_recurring_todos(db_session)

    assert created == 1
    # next daily occurrence after the rescheduled Mar 14, not after Mar 10.
    assert template.last_generated_date == "2026-03-15"
    due_dates = {t.due_date for t in _instances(db_session, template.id)}
    assert due_dates == {"2026-03-14", "2026-03-15"}


def test_process_counts_multiple_and_ignores_inactive(db_session, make_recurring_todo, frozen_now):
    frozen_now(FROZEN)
    make_recurring_todo("Active A", recurrence_type="daily", has_due_date=True)
    make_recurring_todo("Active B", recurrence_type="daily", has_due_date=True)
    make_recurring_todo("Inactive", recurrence_type="daily", has_due_date=True, active=False)

    created = process_recurring_todos(db_session)

    assert created == 2
    assert db_session.query(Todo).count() == 2
