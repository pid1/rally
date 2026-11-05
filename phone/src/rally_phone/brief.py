"""The call brief: everything the agent knows about one call, and nothing more.

A call never starts without a brief written by Claude for a call Jonathan asked
for. The brief is validated strictly here, before any request leaves the
machine:

* numbers are E.164, in an allowed country code, and not premium-rate or
  N11 service numbers;
* every free-text field has a length cap, and lists have item caps;
* free text may not carry what the agent must never say on a call: Social
  Security numbers, long digit runs (card, account, licence numbers), street
  addresses. A brief that needs one is refused with the field name, so Claude
  can tell Jonathan the call needs him instead.

The brief becomes the agent's dynamic variables (``to_dynamic_variables``).
ElevenLabs accepts only strings, numbers and booleans there, so lists are
rendered as numbered lines.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, time, timedelta
from typing import Annotated
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

E164 = re.compile(r"^\+[1-9]\d{7,14}$")
# NANP numbers that must never be dialled by an agent: premium rate (900, 976),
# N11 service codes and invalid area codes or exchanges.
NANP_BLOCKED_AREA_CODES = {"900", "976"}
# +1 also covers Caribbean and Atlantic countries billed as international calls
# and common in toll fraud; only the US (with territories) and Canada are dialled.
NANP_NON_US_CA = {
    "242",
    "246",
    "264",
    "268",
    "284",
    "345",
    "441",
    "473",
    "649",
    "658",
    "664",
    "721",
    "758",
    "767",
    "784",
    "809",
    "829",
    "849",
    "868",
    "869",
    "876",
}
SSN = re.compile(r"\b\d{3}[- ]\d{2}[- ]\d{4}\b")
LONG_DIGITS = re.compile(r"(?:\d[ -]?){9,}")
STREET = re.compile(
    r"\b\d{1,6}\s+(?:[A-Za-z0-9.'-]+\s+){1,4}"
    r"(?:st|street|ave|avenue|rd|road|blvd|boulevard|dr|drive|ln|lane|ct|court|way|"
    r"pl|place|pkwy|parkway|hwy|highway|cir|circle|ter|terrace|trl|trail)\b\.?",
    re.IGNORECASE,
)

MAX_WINDOW_HOURS = 13  # the whole 08:00-21:00 TCPA day
MAX_SCHEDULE_DAYS = 14
MIN_LEAD = timedelta(seconds=60)
MIN_WINDOW_LEFT = timedelta(minutes=10)
# TCPA calling hours, in the callee's local time: 8:00 a.m. to 9:00 p.m.
# (https://elevenlabs.io/docs/eleven-agents/legal/tcpa). A call placed now must
# start by 20:45 so the 15-minute cap still ends it by 21:00.
CALL_HOURS_START = time(8, 0)
CALL_HOURS_END = time(21, 0)
LATEST_START_NOW = time(20, 45)

# Always forbidden, whatever the brief says. Rendered into must_not_share.
STANDING_DO_NOT_SHARE = (
    "Any Social Security, driver's licence, passport, account, card, policy or PIN number",
    "Any date of birth",
    "Any home, mailing or street address",
    "Any calendar details, schedules or whereabouts of anyone in the family",
    "Any payment details; never agree to pay or authorise a charge",
)

Short = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
Item = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=240)]
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=600)]


class BriefError(ValueError):
    """The brief was refused. The message names the field and the reason."""


def check_number(value: str, allowed_country_codes: tuple[str, ...] = ("1",)) -> str:
    value = value.strip()
    if not E164.match(value):
        raise BriefError(f"'{value}' is not an E.164 number like +12025550123")
    digits = value[1:]
    if not any(digits.startswith(cc) for cc in allowed_country_codes):
        raise BriefError(
            f"{value}: country code not allowed (allowed: {', '.join(allowed_country_codes)})"
        )
    if digits.startswith("1"):
        if len(digits) != 11:
            raise BriefError(f"{value}: North American numbers have 10 digits after +1")
        area, exchange = digits[1:4], digits[4:7]
        if area in NANP_BLOCKED_AREA_CODES or area[0] in "01" or area[1:] == "11":
            raise BriefError(f"{value}: area code {area} is not callable by the agent")
        if area in NANP_NON_US_CA:
            raise BriefError(f"{value}: area code {area} is outside the US and Canada")
        if exchange[0] in "01" or exchange[1:] == "11":
            raise BriefError(f"{value}: exchange {exchange} is not a valid subscriber number")
    return value


def _check_tz(field: str, value: str) -> str:
    try:
        ZoneInfo(value)
    except ZoneInfoNotFoundError, ValueError:
        raise BriefError(f"{field}: unknown time zone '{value}'") from None
    return value


def check_call_hours(now: datetime, tz: str) -> None:
    """Refuse a call placed now outside 8:00-20:45 in the callee's time zone."""
    local = now.astimezone(ZoneInfo(tz)).time()
    if not (CALL_HOURS_START <= local <= LATEST_START_NOW):
        raise BriefError(
            f"it is {local:%H:%M} in {tz}; calls are placed only 08:00-20:45 callee time "
            "(TCPA hours 8 a.m.-9 p.m.). Use schedule_call with a window."
        )


def speakable_number(e164: str) -> str:
    """+15125550123 -> 512-555-0123 (other countries unchanged)."""
    if e164.startswith("+1") and len(e164) == 12:
        d = e164[2:]
        return f"{d[:3]}-{d[3:6]}-{d[6:]}"
    return e164


def _check_text(field: str, text: str) -> None:
    if SSN.search(text):
        raise BriefError(f"{field}: looks like a Social Security number; the agent never says one")
    if LONG_DIGITS.search(text):
        raise BriefError(
            f"{field}: contains a long number (card, account or licence numbers are never "
            "shared on a call); put phone numbers in to_number or callback_number"
        )
    if STREET.search(text):
        raise BriefError(f"{field}: looks like a street address; the agent never shares one")


class CallWindow(BaseModel):
    """When the call may be placed, in the business's local time."""

    model_config = ConfigDict(extra="forbid")

    timezone: Annotated[
        str, Field(description="IANA time zone of the business, e.g. America/Chicago")
    ]
    start: Annotated[
        datetime, Field(description="Window start, local wall time, e.g. 2026-11-06T09:00")
    ]
    end: Annotated[
        datetime, Field(description="Window end, local wall time, e.g. 2026-11-06T16:30")
    ]

    @field_validator("timezone")
    @classmethod
    def _tz(cls, v: str) -> str:
        return _check_tz("call_window.timezone", v)

    def bounds_utc(self) -> tuple[datetime, datetime]:
        tz = ZoneInfo(self.timezone)
        start = self.start if self.start.tzinfo else self.start.replace(tzinfo=tz)
        end = self.end if self.end.tzinfo else self.end.replace(tzinfo=tz)
        return start.astimezone(UTC), end.astimezone(UTC)

    def dial_time(self, now: datetime) -> datetime:
        """The UTC moment to dial: the window start, or shortly after now if inside it."""
        start, end = self.bounds_utc()
        if end <= start:
            raise BriefError("call_window: end must be after start")
        if end - start > timedelta(hours=MAX_WINDOW_HOURS):
            raise BriefError(f"call_window: at most {MAX_WINDOW_HOURS} hours long")
        local_start = start.astimezone(ZoneInfo(self.timezone))
        local_end = end.astimezone(ZoneInfo(self.timezone))
        if (
            local_start.date() != local_end.date()
            or local_start.time() < CALL_HOURS_START
            or local_end.time() > CALL_HOURS_END
        ):
            raise BriefError(
                "call_window: must lie within 08:00-21:00 on one day in the callee's time "
                "zone (TCPA calling hours)"
            )
        if start - now > timedelta(days=MAX_SCHEDULE_DAYS):
            raise BriefError(f"call_window: must start within {MAX_SCHEDULE_DAYS} days")
        dial = max(start, now + MIN_LEAD)
        if end - dial < MIN_WINDOW_LEFT:
            raise BriefError("call_window: the window has ended or is about to end")
        return dial

    def contains(self, now: datetime) -> bool:
        start, end = self.bounds_utc()
        return start <= now <= end - MIN_WINDOW_LEFT


class CallBrief(BaseModel):
    """What Claude writes before any call. Every field is shown to the agent."""

    model_config = ConfigDict(extra="forbid")

    callee_name: Annotated[
        Short, Field(description="Who is being called, e.g. 'Travis County Tax Office'")
    ]
    to_number: Annotated[str, Field(description="Number to dial, E.164, e.g. +15125550123")]
    topic: Annotated[
        Short,
        Field(
            description="Plain-language topic for the opening line, e.g. 'a licence plate renewal'"
        ),
    ]
    goal: Annotated[Text, Field(description="What a successful call achieves")]
    questions: Annotated[
        list[Item], Field(max_length=10, description="Questions to ask, in order (max 10)")
    ] = []
    may_share: Annotated[
        list[Item],
        Field(max_length=10, description="Facts the agent may share if asked (max 10)"),
    ] = []
    must_not_share: Annotated[
        list[Item],
        Field(
            max_length=10,
            description="Extra things never to share; the standing list is always added",
        ),
    ] = []
    give_up_when: Annotated[
        Text,
        Field(
            description="When to stop and say Jonathan will follow up, e.g. 'they need the "
            "owner in person or need ID'"
        ),
    ]
    callback_number: Annotated[
        str | None,
        Field(
            description="Number the callee may call back on (E.164), only if Jonathan gave one; "
            "otherwise the assistant's own number is offered"
        ),
    ] = None
    callee_timezone: Annotated[
        str | None,
        Field(
            description="IANA time zone of the business, e.g. America/Chicago. Required for "
            "place_call unless call_window is given (TCPA calling hours)."
        ),
    ] = None
    call_window: CallWindow | None = None
    rally_todo_id: Annotated[
        int | None, Field(description="Related Rally to-do, for Claude's bookkeeping only")
    ] = None

    @field_validator("to_number", "callback_number")
    @classmethod
    def _numbers(cls, v: str | None) -> str | None:
        return None if v is None else check_number(v)

    @field_validator("callee_timezone")
    @classmethod
    def _callee_tz(cls, v: str | None) -> str | None:
        return None if v is None else _check_tz("callee_timezone", v)

    @property
    def timezone(self) -> str | None:
        return self.call_window.timezone if self.call_window else self.callee_timezone

    def check_content(self) -> None:
        for name in ("callee_name", "topic", "goal", "give_up_when"):
            _check_text(name, getattr(self, name))
        for name in ("questions", "may_share", "must_not_share"):
            for i, item in enumerate(getattr(self, name)):
                _check_text(f"{name}[{i}]", item)


def _numbered(items: list[str] | tuple[str, ...], empty: str) -> str:
    if not items:
        return empty
    return "\n".join(f"{i}. {item}" for i, item in enumerate(items, 1))


def to_dynamic_variables(
    brief: CallBrief, owner_name: str, assistant_number: str
) -> dict[str, str]:
    window = "none (call placed now)"
    if brief.call_window:
        w = brief.call_window
        window = f"{w.start:%A %Y-%m-%d %H:%M} to {w.end:%H:%M} ({w.timezone})"
    return {
        "owner_name": owner_name,
        "callee_name": brief.callee_name,
        "call_topic": brief.topic,
        "call_goal": brief.goal,
        "questions": _numbered(brief.questions, "(no specific questions)"),
        "may_share": _numbered(brief.may_share, "(nothing beyond the topic and goal)"),
        "must_not_share": _numbered(
            [*STANDING_DO_NOT_SHARE, *brief.must_not_share], "(standing list only)"
        ),
        "give_up_when": brief.give_up_when,
        "callback_number": speakable_number(brief.callback_number or assistant_number),
        "assistant_number": speakable_number(assistant_number),
        "call_window": window,
    }


def parse_brief(data: dict, now: datetime | None = None) -> CallBrief:
    """Validate a brief dict, raising BriefError with a readable reason."""
    from pydantic import ValidationError

    try:
        brief = CallBrief.model_validate(data)
    except ValidationError as e:
        parts = []
        for err in e.errors():
            loc = ".".join(str(p) for p in err["loc"]) or "brief"
            msg = err["msg"].removeprefix("Value error, ")
            parts.append(f"{loc}: {msg}")
        raise BriefError("; ".join(parts)) from None
    return check_brief(brief, now)


def check_brief(brief: CallBrief, now: datetime | None = None) -> CallBrief:
    """Checks beyond the schema: content rules, numbers, the window."""
    brief.check_content()
    if brief.callback_number and brief.callback_number == brief.to_number:
        raise BriefError("callback_number: must differ from to_number")
    if brief.call_window:
        if brief.callee_timezone and brief.callee_timezone != brief.call_window.timezone:
            raise BriefError("callee_timezone: must match call_window.timezone")
        brief.call_window.dial_time(now or datetime.now(UTC))
    elif not brief.callee_timezone:
        raise BriefError("callee_timezone: required (IANA zone of the business) for TCPA hours")
    return brief
