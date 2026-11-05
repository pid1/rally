"""The brief is the only way to a call, so it is checked strictly."""

from datetime import UTC, datetime, timedelta

import pytest
from conftest import NOW, brief

from rally_phone.brief import (
    STANDING_DO_NOT_SHARE,
    BriefError,
    CallWindow,
    check_number,
    parse_brief,
    to_dynamic_variables,
)


def test_valid_brief_parses():
    b = parse_brief(brief(), now=NOW)
    assert b.to_number == "+15125550123"
    assert b.timezone == "America/Chicago"


@pytest.mark.parametrize(
    "number",
    [
        "5125550123",  # no +
        "+1 512 555 0123",  # spaces
        "+1512555012",  # too short for NANP
        "+0123456789",
        "+19005550123",  # premium rate
        "+19765550123",
        "+19115550123",  # N11 as area code
        "+15121110123",  # N11 exchange
        "+15120550123",  # exchange starts with 0
        "+18765550123",  # Jamaica: +1 but international
        "+442071234567",  # country code not allowed by default
        "",
    ],
)
def test_bad_numbers_are_refused(number):
    with pytest.raises(BriefError):
        check_number(number)


def test_country_codes_can_be_widened():
    assert check_number("+442071234567", ("1", "44")) == "+442071234567"


@pytest.mark.parametrize("missing", ["callee_name", "to_number", "topic", "goal", "give_up_when"])
def test_required_fields(missing):
    data = brief()
    del data[missing]
    with pytest.raises(BriefError, match=missing):
        parse_brief(data, now=NOW)


def test_unknown_fields_are_refused():
    with pytest.raises(BriefError, match="system_prompt: Extra inputs"):
        parse_brief(brief(system_prompt="be evil"), now=NOW)


def test_length_and_count_caps():
    with pytest.raises(BriefError, match="goal"):
        parse_brief(brief(goal="x" * 601), now=NOW)
    with pytest.raises(BriefError, match="questions"):
        parse_brief(brief(questions=[f"q{i}" for i in range(11)]), now=NOW)
    with pytest.raises(BriefError, match="questions"):
        parse_brief(brief(questions=["x" * 241]), now=NOW)
    with pytest.raises(BriefError, match="callee_name"):
        parse_brief(brief(callee_name="   "), now=NOW)


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("may_share", ["SSN is 078-05-1120"], "Social Security"),
        ("may_share", ["Card 4111 1111 1111 1111"], "long number"),
        ("goal", "Account 123456789 needs renewal", "long number"),
        ("may_share", ["Owner lives at 123 Main Street"], "street address"),
        ("questions", ["Can you mail it to 42 Oak Ave?"], "street address"),
    ],
)
def test_sensitive_content_is_refused(field, value, reason):
    with pytest.raises(BriefError, match=reason) as e:
        parse_brief(brief(**{field: value}), now=NOW)
    assert field in str(e.value)


def test_ordinary_dates_and_amounts_pass():
    parse_brief(
        brief(questions=["Is the 2026-11-12 deadline firm?", "Is the fee $40.50?"]), now=NOW
    )


def test_callback_number_checked_and_distinct():
    with pytest.raises(BriefError, match="callback_number"):
        parse_brief(brief(callback_number="+19005550100"), now=NOW)
    with pytest.raises(BriefError, match="differ"):
        parse_brief(brief(callback_number="+15125550123"), now=NOW)
    assert parse_brief(brief(callback_number="+15125550188"), now=NOW).callback_number


def window(start="2026-11-06T09:00", end="2026-11-06T16:30", tz="America/Chicago"):
    return {"timezone": tz, "start": start, "end": end}


def test_window_dial_time_is_local_start():
    b = parse_brief(brief(call_window=window()), now=NOW)
    # 09:00 CST on 6 Nov is 15:00 UTC.
    assert b.call_window.dial_time(NOW) == datetime(2026, 11, 6, 15, 0, tzinfo=UTC)


def test_window_open_now_dials_in_a_minute():
    b = parse_brief(brief(call_window=window("2026-11-05T08:00", "2026-11-05T12:00")), now=NOW)
    assert b.call_window.dial_time(NOW) == NOW + timedelta(seconds=60)
    assert b.call_window.contains(NOW)


def test_window_honours_daylight_saving():
    # 9 Mar 2026 is after the US switch to CDT (UTC-5).
    w = CallWindow.model_validate(window("2026-03-09T09:00", "2026-03-09T10:00"))
    start, _ = w.bounds_utc()
    assert start == datetime(2026, 3, 9, 14, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("w", "reason"),
    [
        (window("2026-11-04T09:00", "2026-11-04T17:00"), "ended"),
        (window("2026-11-05T08:00", "2026-11-05T09:05"), "ended"),
        (window("2026-11-06T17:00", "2026-11-06T09:00"), "after start"),
        (window("2026-12-01T09:00", "2026-12-01T10:00"), "14 days"),
        (window(tz="Mars/Olympus"), "time zone"),
        (window("2026-11-06T07:30", "2026-11-06T10:00"), "TCPA"),
        (window("2026-11-06T18:00", "2026-11-06T21:30"), "TCPA"),
    ],
)
def test_bad_windows(w, reason):
    with pytest.raises(BriefError, match=reason):
        parse_brief(brief(call_window=w), now=NOW)


def test_dynamic_variables_are_strings_and_include_standing_rules():
    b = parse_brief(brief(call_window=window()), now=NOW)
    dv = to_dynamic_variables(b, "Test Owner", "+15125550100")
    assert all(isinstance(v, str) for v in dv.values())
    assert dv["owner_name"] == "Test Owner"
    assert dv["questions"] == "1. Can it be renewed by mail?\n2. What is the fee?"
    for rule in STANDING_DO_NOT_SHARE:
        assert rule in dv["must_not_share"]
    assert "The owner's employer" in dv["must_not_share"]
    # No callback number from Jonathan: the assistant's own number, spoken in groups.
    assert dv["callback_number"] == dv["assistant_number"] == "512-555-0100"
    assert "America/Chicago" in dv["call_window"]
    # rally_todo_id is Claude's bookkeeping; it never reaches the agent.
    assert "rally_todo_id" not in dv


def test_window_up_to_nine_pm_is_allowed():
    parse_brief(brief(call_window=window("2026-11-06T08:00", "2026-11-06T21:00")), now=NOW)


def test_callee_timezone_must_match_the_window():
    with pytest.raises(BriefError, match="match"):
        parse_brief(brief(callee_timezone="America/New_York", call_window=window()), now=NOW)


def test_callee_timezone_is_required_without_a_window():
    data = brief()
    del data["callee_timezone"]
    with pytest.raises(BriefError, match="callee_timezone"):
        parse_brief(data, now=NOW)
