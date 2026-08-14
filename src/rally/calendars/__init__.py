"""One normalized view of every calendar Rally can read or own.

Sources produce ``Occurrence`` objects; ``merge`` combines and orders them; the
generator and the `/calendar` page consume the result. Nothing outside this
package should reach for an ICS component or a CalDAV client directly.
"""

from rally.calendars.merge import merge_occurrences
from rally.calendars.native import (
    MAX_OCCURRENCES_PER_EVENT,
    RecurrenceError,
    collect_native,
    expand_event,
    series_end_date,
    validate_rrule,
)
from rally.calendars.occurrence import (
    SOURCE_CALDAV_APPLE,
    SOURCE_CALDAV_GOOGLE,
    SOURCE_ICS,
    SOURCE_NATIVE,
    FetchResult,
    Occurrence,
    all_day_bounds,
    dates_covered,
    local_midnight_utc,
    resolve_local,
    to_utc,
)
from rally.calendars.sources import collect_occurrences, default_window, window_bounds

__all__ = [
    "MAX_OCCURRENCES_PER_EVENT",
    "SOURCE_CALDAV_APPLE",
    "SOURCE_CALDAV_GOOGLE",
    "SOURCE_ICS",
    "SOURCE_NATIVE",
    "FetchResult",
    "Occurrence",
    "RecurrenceError",
    "all_day_bounds",
    "collect_native",
    "collect_occurrences",
    "dates_covered",
    "default_window",
    "expand_event",
    "local_midnight_utc",
    "merge_occurrences",
    "resolve_local",
    "series_end_date",
    "to_utc",
    "validate_rrule",
    "window_bounds",
]
