"""Read an RRULE back as the phrase somebody would have picked in the form.

This is the one place that turns a stored rule into words, and it now has three
callers: the event modal's read-back while a rule is being built, the
occurrence detail row, and the ``This event repeats …`` sentence in a Pushover
change notice. Only the last is a notification, which is why it lives here
beside the other rule-reading code rather than in ``notifications``.

The contract that matters: a rule richer than this vocabulary degrades to a
phrase that is **vague but true**, never a confidently wrong one. Saying
``daily`` about a weekdays-only series is worse than saying nothing, because the
reader has no way to tell it is wrong.
"""

from __future__ import annotations

import re
from calendar import month_abbr
from datetime import datetime

_WEEKDAY_NAMES = {
    "MO": "Monday",
    "TU": "Tuesday",
    "WE": "Wednesday",
    "TH": "Thursday",
    "FR": "Friday",
    "SA": "Saturday",
    "SU": "Sunday",
}

# Singular for an interval of one, plural for the rest: "weekly" against "every
# 2 weeks".
_FREQ_WORDS = {
    "DAILY": ("daily", "days"),
    "WEEKLY": ("weekly", "weeks"),
    "MONTHLY": ("monthly", "months"),
    "YEARLY": ("yearly", "years"),
}

# The positions the monthly "relative weekday" mode offers. `-1` is the only
# negative worth a word: `-2` and beyond are legal RRULE and never produced by
# the form, so they fall through to the plain cadence rather than to an
# invented phrase like "second to last".
_ORDINAL_WORDS = {1: "first", 2: "second", 3: "third", 4: "fourth", 5: "fifth", -1: "last"}

_WEEKDAYS_ONLY = frozenset({"MO", "TU", "WE", "TH", "FR"})

# "1SU", "-1FR", or a bare "SU".
_BYDAY_TOKEN = re.compile(r"^(-?\d+)?([A-Z]{2})$")


def _rrule_parts(rrule: str) -> dict[str, str]:
    parts: dict[str, str] = {}
    for chunk in rrule.split(";"):
        name, _, value = chunk.partition("=")
        if name.strip():
            parts[name.strip().upper()] = value.strip()
    return parts


def _join_names(names: list[str]) -> str:
    if len(names) == 1:
        return names[0]
    return f"{', '.join(names[:-1])} and {names[-1]}"


def _ordinal(day: int) -> str:
    suffix = "th" if 11 <= day % 100 <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
    return f"{day}{suffix}"


def _byday_codes(value: str) -> list[str]:
    """Just the weekday codes, dropping any ordinal prefix."""
    codes = []
    for token in value.split(","):
        match = _BYDAY_TOKEN.match(token.strip().upper())
        if match and match.group(2) in _WEEKDAY_NAMES:
            codes.append(match.group(2))
    return codes


def _positional_days(value: str) -> list[str]:
    """ "the first Sunday", "the last Friday" — only where an ordinal is present.

    A bare ``BYDAY=SU`` in a monthly rule has no position and means something
    else entirely, so it returns nothing rather than guessing at one.
    """
    phrases = []
    for token in value.split(","):
        match = _BYDAY_TOKEN.match(token.strip().upper())
        if not match:
            return []
        ordinal, code = match.group(1), match.group(2)
        if ordinal is None or code not in _WEEKDAY_NAMES:
            return []
        word = _ORDINAL_WORDS.get(int(ordinal))
        if word is None:
            return []
        phrases.append(f"{word} {_WEEKDAY_NAMES[code]}")
    return phrases


def _until_date(raw: str) -> str:
    """``UNTIL`` as a readable date, in whichever of the three forms it arrived."""
    text = raw.strip().rstrip("Z")
    for fmt in ("%Y%m%dT%H%M%S", "%Y%m%d"):
        try:
            parsed = datetime.strptime(text, fmt)
        except ValueError:
            continue
        return f"{month_abbr[parsed.month]} {parsed.day}, {parsed.year}"
    return ""


def _describe_bound(parts: dict[str, str]) -> str:
    """How the series stops, if it says so. ``UNTIL`` and ``COUNT`` are exclusive."""
    until = parts.get("UNTIL", "")
    if until:
        readable = _until_date(until)
        return f"until {readable}" if readable else ""

    count = parts.get("COUNT", "")
    if count.isdigit():
        times = int(count)
        return "once" if times == 1 else f"{times} times"
    return ""


def describe_recurrence(rrule: str | None) -> str:
    """How often a series repeats, in the vocabulary the event form offers.

    The form compiles its choices to RRULE and this reads them back, so a notice
    describes the choice somebody actually made rather than the syntax it was
    stored as. Anything the form cannot express — an imported rule, or one typed
    by hand — degrades rather than guesses.
    """
    if not rrule:
        return ""

    parts = _rrule_parts(rrule)
    freq = parts.get("FREQ", "").upper()
    words = _FREQ_WORDS.get(freq)
    if not words:
        return "on a custom schedule"

    singular, plural = words
    try:
        interval = int(parts.get("INTERVAL", "1"))
    except ValueError:
        interval = 1
    phrase = singular if interval <= 1 else f"every {interval} {plural}"
    byday = parts.get("BYDAY", "")

    if freq == "DAILY" and byday:
        # `FREQ=DAILY;BYDAY=MO,TU,WE,TH,FR` is "every weekday" and emphatically
        # not "daily" — the whole point of the rule is the two days it omits.
        codes = set(_byday_codes(byday))
        if codes == _WEEKDAYS_ONLY and interval <= 1:
            phrase = "every weekday"
        else:
            named = [_WEEKDAY_NAMES[code] for code in _byday_codes(byday)]
            if named:
                phrase = f"{phrase} on {_join_names(named)}"

    elif freq == "WEEKLY":
        # An ordinal prefix cannot occur in a weekly rule; only the day matters.
        named = [_WEEKDAY_NAMES[code] for code in _byday_codes(byday)]
        if named:
            phrase = f"{phrase} on {_join_names(named)}"

    elif freq == "MONTHLY":
        monthday = parts.get("BYMONTHDAY", "").strip()
        positional = _positional_days(byday) if byday else []
        if monthday == "-1":
            phrase = f"{phrase} on the last day"
        elif monthday.isdigit():
            phrase = f"{phrase} on the {_ordinal(int(monthday))}"
        elif positional:
            phrase = f"{phrase} on the {_join_names(positional)}"

    bound = _describe_bound(parts)
    return f"{phrase}, {bound}" if bound else phrase
