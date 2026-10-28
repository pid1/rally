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
 *   formatDate(value, options)   → string
 *       value: `YYYY-MM-DD` or a Date (an instant is read in the device's zone)
 *       weekday:  true writes "Friday, " first                (default false)
 *       month:    'long' (October) | 'short' (Oct)            (default 'long')
 *       year:     'always' | 'unlessThisYear' | 'never'       (default 'always')
 *       ordinal:  true writes the day as "30th"               (default false)
 *       relative: 'upcoming' → Today / Tomorrow,
 *                 'past'     → Today / Yesterday              (default none)
 *       today:    today as `YYYY-MM-DD`; required by `relative` and
 *                 `year: 'unlessThisYear'`
 *
 *   e.g. formatDate('2026-10-30', { weekday: true, month: 'short', year: 'never' })
 *        → "Friday, Oct 30"
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

    function formatDate(value, options = {}) {
        const {
            weekday = false,
            month = 'long',
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

        const date = value instanceof Date ? value : parseIsoDate(value);
        if (relative) {
            const day = isoDate(date);
            if (day === today) return 'Today';
            const { step, word } = RELATIVE[relative];
            if (day === shiftIsoDate(today, step)) return word;
        }

        const dayOfMonth = asOrdinal ? ordinal(date.getDate()) : date.getDate();
        let text = `${months[date.getMonth()]} ${dayOfMonth}`;
        if (weekday) text = `${WEEKDAYS[date.getDay()]}, ${text}`;
        const withYear = year === 'always'
            || (year === 'unlessThisYear' && date.getFullYear() !== parseIsoDate(today).getFullYear());
        return withYear ? `${text}, ${date.getFullYear()}` : text;
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
    window.formatDate = formatDate;
})();
