/* How Rally's pages read, step and write dates, in one place.
 *
 * A date is a wall-calendar day, `YYYY-MM-DD`, read as local midnight and never
 * through a time zone. `formatDate()` is the one formatter: each page asks for
 * the parts it shows, so the same date cannot be written two ways by two copies
 * of a helper.
 *
 * Nothing here decides what "today" is. `formatDate()` reads no clock: when it
 * needs today (`relative`, or `year: 'unlessThisYear'`) the caller hands it in
 * and there is no fallback. `todayLocal()` reads the clock in the zone it is
 * handed. Which clock a page uses is that page's choice, made at its call sites.
 *
 * Loaded by base.html on every page, next to modal.js and dialogs.js, and not
 * deferred, so the shared scripts and every page's own script can use it.
 *
 *   parseIsoDate(value)          → Date      `YYYY-MM-DD` → local midnight
 *   isoDate(date)                → string    a Date → `YYYY-MM-DD`
 *   addDays(date, n)             → Date      `n` calendar days on (or back)
 *   shiftIsoDate(value, n)       → string    the same, `YYYY-MM-DD` in and out
 *   todayLocal(timeZone)         → string    today's `YYYY-MM-DD` in that zone
 *   ordinal(n)                   → string    30 → "30th"
 *   WEEKDAYS, WEEKDAYS_SHORT     Sunday … / Sun …
 *   MONTHS, MONTHS_SHORT         January … / Jan … (Sep for September)
 *   parseTimestamp(value)        → Date      a stored timestamp; one with no
 *                                            zone is UTC (Invalid Date if unreadable)
 *   formatDate(value, options)   → string
 *       value: `YYYY-MM-DD` or a Date (an instant is read in the device's zone)
 *       weekday:  true writes "Friday, " first                (default false)
 *       month:    'long' (October) | 'short' (Oct)            (default 'long')
 *       day:      false leaves the day out: "October 2026"    (default true)
 *       year:     'always' | 'unlessThisYear' | 'never'       (default 'always')
 *       ordinal:  true writes the day as "30th"               (default false)
 *       relative: 'upcoming' → Today / Tomorrow,
 *                 'past'     → Today / Yesterday              (default none)
 *       today:    today as `YYYY-MM-DD`; required by `relative` and
 *                 `year: 'unlessThisYear'`
 *
 *   formatDateRange(start, end, options) → string
 *       Two dates as one span, each end written by formatDate() with `options`
 *       (not `relative` or `day`). The year is stated once when both ends share
 *       it and on both ends when they do not; without `weekday`, the month is
 *       stated once when the span stays in one month.
 *   formatTime(date)             → string    "4:00 AM", in the device's zone
 *   formatUtcTimestamp(value)    → string    "2026-10-28 15:52:30 UTC"
 *
 *   e.g. formatDate('2026-10-30', { weekday: true, month: 'short', year: 'never' })
 *        → "Friday, Oct 30"
 *        formatDateRange('2026-12-28', '2027-01-03', { month: 'long' })
 *        → "December 28, 2026 – January 3, 2027"
 */
(function () {
    'use strict';

    const WEEKDAYS = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];
    const WEEKDAYS_SHORT = WEEKDAYS.map(name => name.slice(0, 3));
    const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July',
        'August', 'September', 'October', 'November', 'December'];
    const MONTHS_SHORT = MONTHS.map(name => name.slice(0, 3));

    const MONTH_FORMS = { long: MONTHS, short: MONTHS_SHORT };
    const YEAR_RULES = ['always', 'unlessThisYear', 'never'];
    // The day either side of today that `relative` names, and its word.
    const RELATIVE = {
        upcoming: { step: 1, word: 'Tomorrow' },
        past: { step: -1, word: 'Yesterday' },
    };

    function pad(n) {
        return String(n).padStart(2, '0');
    }

    function parseIsoDate(value) {
        const [y, m, d] = value.split('-').map(Number);
        return new Date(y, m - 1, d);
    }

    function isoDate(date) {
        return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
    }

    // By calendar day at local midnight, so a daylight saving change, which
    // makes one day 23 or 25 hours long, cannot land it on the wrong day.
    function addDays(date, n) {
        const next = new Date(date);
        next.setDate(next.getDate() + n);
        return next;
    }

    function shiftIsoDate(value, n) {
        return isoDate(addDays(parseIsoDate(value), n));
    }

    function todayLocal(timeZone) {
        if (!timeZone) throw new Error('todayLocal() needs a time zone');
        return new Intl.DateTimeFormat('en-CA', { timeZone }).format(new Date());
    }

    function ordinal(n) {
        const s = ['th', 'st', 'nd', 'rd'];
        const v = n % 100;
        return n + (s[(v - 20) % 10] || s[v] || s[0]);
    }

    // Stored timestamps come back naive when SQLite hands them over without a
    // zone, and `new Date('2026-08-07T18:00:00')` reads that as local time.
    // Rally stores UTC, so a value with no zone is tagged as UTC.
    function parseTimestamp(value) {
        if (value instanceof Date) return value;
        const text = String(value);
        return new Date(/(?:Z|[+-]\d{2}:?\d{2})$/i.test(text) ? text : `${text}Z`);
    }

    function asDate(value) {
        return value instanceof Date ? value : parseIsoDate(value);
    }

    function formatDate(value, options = {}) {
        const {
            weekday = false,
            month = 'long',
            day = true,
            year = 'always',
            ordinal: asOrdinal = false,
            relative = null,
            today = null,
        } = options;
        const months = MONTH_FORMS[month];
        if (!months) throw new Error(`formatDate(): unknown month form "${month}"`);
        if (!YEAR_RULES.includes(year)) throw new Error(`formatDate(): unknown year rule "${year}"`);
        if (relative && !RELATIVE[relative]) throw new Error(`formatDate(): unknown relative "${relative}"`);
        if ((relative || year === 'unlessThisYear') && !today) {
            throw new Error('formatDate(): pass `today` with `relative` or `year: \'unlessThisYear\'`');
        }
        if (!day && (weekday || asOrdinal || relative)) {
            throw new Error('formatDate(): `day: false` leaves no day for `weekday`, `ordinal` or `relative`');
        }

        const date = asDate(value);
        const withYear = year === 'always'
            || (year === 'unlessThisYear' && date.getFullYear() !== parseIsoDate(today).getFullYear());
        if (!day) return withYear ? `${months[date.getMonth()]} ${date.getFullYear()}` : months[date.getMonth()];
        if (relative) {
            const iso = isoDate(date);
            if (iso === today) return 'Today';
            const { step, word } = RELATIVE[relative];
            if (iso === shiftIsoDate(today, step)) return word;
        }

        const dayOfMonth = asOrdinal ? ordinal(date.getDate()) : date.getDate();
        let text = `${months[date.getMonth()]} ${dayOfMonth}`;
        if (weekday) text = `${WEEKDAYS[date.getDay()]}, ${text}`;
        return withYear ? `${text}, ${date.getFullYear()}` : text;
    }

    function formatDateRange(start, end, options = {}) {
        if (options.relative || options.day === false) {
            throw new Error('formatDateRange(): a span takes neither `relative` nor `day: false`');
        }
        const { weekday = false, year = 'always', ordinal: asOrdinal = false, today = null } = options;
        const from = asDate(start);
        const to = asDate(end);
        if (isoDate(from) === isoDate(to)) return formatDate(from, options);

        const sameYear = from.getFullYear() === to.getFullYear();
        // Whether the span says its year at all; `formatDate()` checks `today`.
        let showYear = year === 'always';
        if (year === 'unlessThisYear') {
            if (!today) throw new Error('formatDateRange(): pass `today` with `year: \'unlessThisYear\'`');
            const thisYear = parseIsoDate(today).getFullYear();
            showYear = from.getFullYear() !== thisYear || to.getFullYear() !== thisYear;
        }
        const left = formatDate(from, { ...options, year: showYear && !sameYear ? 'always' : 'never' });
        const rightYear = showYear ? 'always' : 'never';
        if (!weekday && sameYear && from.getMonth() === to.getMonth()) {
            const dayOfMonth = asOrdinal ? ordinal(to.getDate()) : to.getDate();
            return `${left} – ${rightYear === 'always' ? `${dayOfMonth}, ${to.getFullYear()}` : dayOfMonth}`;
        }
        return `${left} – ${formatDate(to, { ...options, year: rightYear })}`;
    }

    function formatTime(date) {
        const hours = date.getHours();
        return `${hours % 12 || 12}:${pad(date.getMinutes())} ${hours < 12 ? 'AM' : 'PM'}`;
    }

    // The one place Rally shows an exact UTC time (Settings' version history).
    function formatUtcTimestamp(value) {
        const d = parseTimestamp(value);
        return `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())} `
            + `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}:${pad(d.getUTCSeconds())} UTC`;
    }

    window.WEEKDAYS = WEEKDAYS;
    window.WEEKDAYS_SHORT = WEEKDAYS_SHORT;
    window.MONTHS = MONTHS;
    window.MONTHS_SHORT = MONTHS_SHORT;
    window.parseIsoDate = parseIsoDate;
    window.isoDate = isoDate;
    window.addDays = addDays;
    window.shiftIsoDate = shiftIsoDate;
    window.todayLocal = todayLocal;
    window.ordinal = ordinal;
    window.parseTimestamp = parseTimestamp;
    window.formatDate = formatDate;
    window.formatDateRange = formatDateRange;
    window.formatTime = formatTime;
    window.formatUtcTimestamp = formatUtcTimestamp;
})();
