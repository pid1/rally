"""Combine occurrences from every source into one ordered, deduplicated list."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace

from rally.calendars.occurrence import Occurrence, source_rank


def merge_occurrences(groups: Iterable[Iterable[Occurrence]]) -> list[Occurrence]:
    """Deduplicate across calendars and sort chronologically.

    Two things happen here that used to be tangled together in the generator's
    prompt builder:

    **Dedupe.** One event on two family members' feeds is one event. The key is
    ``Occurrence.dedupe_key`` — UID plus instants, falling back to title plus
    instants. Because the instants are part of the key, two soccer practices on
    the same Saturday stay two rows; the old ``(date, title)`` key kept only
    the first.

    **Attendance.** The members whose calendars carried a duplicate are exactly
    the people attending it, so the union of ``attendees`` is collected while
    deduplicating rather than in a second pass over a parallel map.

    The representative is the highest-ranked source in the group — native
    first, because it is the only one the UI can edit and the only one carrying
    a reminder.
    """
    chosen: dict[tuple, Occurrence] = {}
    attendees: dict[tuple, list[str]] = {}

    for group in groups:
        for occurrence in group:
            key = occurrence.dedupe_key
            names = attendees.setdefault(key, [])
            for name in occurrence.attendees or ():
                if name and name not in names:
                    names.append(name)

            incumbent = chosen.get(key)
            if incumbent is None or source_rank(occurrence.source) < source_rank(
                incumbent.source
            ):
                chosen[key] = occurrence

    merged = [
        replace(occurrence, attendees=tuple(attendees.get(key, ())))
        for key, occurrence in chosen.items()
    ]
    merged.sort(key=lambda occurrence: occurrence.sort_key)
    return merged
