"""Reading a rule back as words.

The rule this file exists to defend: a phrase is either right or vague, never
confidently wrong. `FREQ=DAILY;BYDAY=MO,TU,WE,TH,FR` described as "daily" is the
failure mode — the reader cannot tell it is wrong, and acts on it.

The `notifications` suite covers the same function through the push it ends up
in; these are the unit-level cases, including the ones no notice happens to use.
"""

from __future__ import annotations

import pytest

from rally.calendars.describe import describe_recurrence

# --- The five choices the form has always offered ------------------------------


@pytest.mark.parametrize(
    ("rrule", "expected"),
    [
        ("FREQ=DAILY", "daily"),
        ("FREQ=WEEKLY;BYDAY=FR", "weekly on Friday"),
        ("FREQ=WEEKLY;INTERVAL=2;BYDAY=FR", "every 2 weeks on Friday"),
        ("FREQ=MONTHLY;BYMONTHDAY=14", "monthly on the 14th"),
        ("FREQ=YEARLY", "yearly"),
        ("FREQ=WEEKLY;BYDAY=MO,WE,FR", "weekly on Monday, Wednesday and Friday"),
    ],
)
def test_the_existing_vocabulary_is_unchanged(rrule, expected):
    assert describe_recurrence(rrule) == expected


# --- What the custom form adds -------------------------------------------------


@pytest.mark.parametrize(
    ("rrule", "expected"),
    [
        ("FREQ=MONTHLY;BYDAY=1SU", "monthly on the first Sunday"),
        ("FREQ=MONTHLY;BYDAY=3TH", "monthly on the third Thursday"),
        ("FREQ=MONTHLY;INTERVAL=2;BYDAY=-1FR", "every 2 months on the last Friday"),
        ("FREQ=MONTHLY;BYMONTHDAY=-1", "monthly on the last day"),
        ("FREQ=MONTHLY;INTERVAL=3;BYMONTHDAY=-1", "every 3 months on the last day"),
        ("FREQ=YEARLY;INTERVAL=2", "every 2 years"),
        ("FREQ=DAILY;INTERVAL=3", "every 3 days"),
    ],
)
def test_the_custom_vocabulary_reads_back(rrule, expected):
    assert describe_recurrence(rrule) == expected


def test_a_weekdays_only_series_is_never_called_daily():
    """The one case where the old fallback was wrong rather than merely vague."""
    assert describe_recurrence("FREQ=DAILY;BYDAY=MO,TU,WE,TH,FR") == "every weekday"


def test_a_partial_weekday_set_is_not_every_weekday():
    assert (
        describe_recurrence("FREQ=DAILY;BYDAY=MO,WE,FR") == "daily on Monday, Wednesday and Friday"
    )


# --- Bounds --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("rrule", "expected"),
    [
        ("FREQ=WEEKLY;BYDAY=SA;UNTIL=20261114", "weekly on Saturday, until Nov 14, 2026"),
        ("FREQ=WEEKLY;BYDAY=SA;UNTIL=20261120T235959Z", "weekly on Saturday, until Nov 20, 2026"),
        ("FREQ=WEEKLY;BYDAY=SA;UNTIL=20261120T235959", "weekly on Saturday, until Nov 20, 2026"),
        ("FREQ=WEEKLY;COUNT=12", "weekly, 12 times"),
        ("FREQ=WEEKLY;COUNT=1", "weekly, once"),
        ("FREQ=MONTHLY;BYDAY=1SU;COUNT=6", "monthly on the first Sunday, 6 times"),
    ],
)
def test_a_bounded_series_says_when_it_stops(rrule, expected):
    assert describe_recurrence(rrule) == expected


def test_an_unreadable_until_is_dropped_rather_than_guessed():
    assert describe_recurrence("FREQ=WEEKLY;BYDAY=SA;UNTIL=not-a-date") == "weekly on Saturday"


# --- Degrading rather than guessing --------------------------------------------


@pytest.mark.parametrize("rrule", ["", None])
def test_no_rule_describes_nothing(rrule):
    assert describe_recurrence(rrule) == ""


def test_a_frequency_the_form_does_not_offer_stays_vague():
    assert describe_recurrence("FREQ=HOURLY;INTERVAL=6") == "on a custom schedule"


def test_a_monthly_position_beyond_the_forms_choices_falls_back_to_the_cadence():
    """`-2FR` is legal RRULE the form never writes; "second to last" is invented."""
    assert describe_recurrence("FREQ=MONTHLY;BYDAY=-2FR") == "monthly"


def test_a_bare_weekday_in_a_monthly_rule_claims_no_position():
    assert describe_recurrence("FREQ=MONTHLY;BYDAY=SU") == "monthly"


def test_a_malformed_interval_is_treated_as_one():
    assert describe_recurrence("FREQ=WEEKLY;INTERVAL=x;BYDAY=FR") == "weekly on Friday"
