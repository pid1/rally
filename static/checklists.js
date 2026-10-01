/* What the three checklist pages share: escaping, how dates and progress are
 * worded, and the order a checklist's items read in.
 *
 * The order is decided by the server (`rally.checklists.ordered_items`) and
 * arrives already sorted; `checklistSections` only cuts that list into its
 * groups, so the editor and a day's checklist can never disagree about it.
 */
(function () {
    'use strict';

    const GENERAL = 'General';
    const GENERAL_KEY = 'general';
    const WEEKDAYS = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];
    const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July',
        'August', 'September', 'October', 'November', 'December'];

    function escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text == null ? '' : String(text);
        return div.innerHTML;
    }

    function escapeAttr(text) {
        return escapeHtml(text).replace(/"/g, '&quot;');
    }

    // Dates are wall-calendar days (YYYY-MM-DD), so they are read as local
    // midnight and never through a timezone.
    function parseDay(value) {
        const [y, m, d] = value.split('-').map(Number);
        return new Date(y, m - 1, d);
    }

    /* "Saturday, October 3" — the year only when it is not this one. */
    function formatLongDate(value, today) {
        const day = parseDay(value);
        const base = `${WEEKDAYS[day.getDay()]}, ${MONTHS[day.getMonth()]} ${day.getDate()}`;
        const thisYear = today ? parseDay(today).getFullYear() : new Date().getFullYear();
        return day.getFullYear() === thisYear ? base : `${base}, ${day.getFullYear()}`;
    }

    function addDays(value, days) {
        const day = parseDay(value);
        day.setDate(day.getDate() + days);
        const pad = n => String(n).padStart(2, '0');
        return `${day.getFullYear()}-${pad(day.getMonth() + 1)}-${pad(day.getDate())}`;
    }

    function weekdayOf(value) {
        return WEEKDAYS[parseDay(value).getDay()];
    }

    function itemCount(n) {
        return `${n} item${n === 1 ? '' : 's'}`;
    }

    function progressLabel(checked, total) {
        if (total === 0) return 'Nothing to pack yet';
        if (checked === total) return `All ${total} packed`;
        return `${checked} of ${total} packed`;
    }

    function packTimingLabel(timing) {
        return timing === 'day_before' ? 'Pack the day before' : 'Pack the day of';
    }

    /* Cut a checklist's items into the sections a page renders.
     *
     * A checklist nobody has grouped is one section with no name, so it reads
     * as a plain list. Otherwise every group is a section, in order, and
     * General follows. `keepEmpty` keeps sections with nothing in them — the
     * editor needs them as drop targets; a day's checklist does not show them.
     */
    function checklistSections(groups, items, { keepEmpty = false } = {}) {
        if (groups.length === 0) {
            return [{ key: GENERAL_KEY, groupId: null, name: null, items: items.slice() }];
        }
        const sections = groups.map(g => ({ key: String(g.id), groupId: g.id, name: g.name, items: [] }));
        const general = { key: GENERAL_KEY, groupId: null, name: GENERAL, items: [] };
        const byId = new Map(sections.map(s => [s.groupId, s]));
        items.forEach(item => (byId.get(item.group_id) || general).items.push(item));
        sections.push(general);
        return keepEmpty ? sections : sections.filter(s => s.items.length > 0);
    }

    function groupIdForKey(key) {
        return key === GENERAL_KEY ? null : parseInt(key, 10);
    }

    async function requestJson(url, options = {}) {
        const init = { ...options };
        if (init.body !== undefined && typeof init.body !== 'string') {
            init.headers = { 'Content-Type': 'application/json', ...(init.headers || {}) };
            init.body = JSON.stringify(init.body);
        }
        const response = await fetch(url, init);
        let body = null;
        if (response.status !== 204) {
            body = await response.json().catch(() => null);
        }
        if (!response.ok) {
            const detail = body && body.detail;
            const error = new Error(typeof detail === 'string' ? detail : (detail && detail.message) || 'Request failed');
            error.status = response.status;
            error.detail = detail;
            throw error;
        }
        return body;
    }

    window.Checklists = {
        GENERAL,
        GENERAL_KEY,
        escapeHtml,
        escapeAttr,
        formatLongDate,
        addDays,
        weekdayOf,
        itemCount,
        progressLabel,
        packTimingLabel,
        checklistSections,
        groupIdForKey,
        requestJson,
    };
})();
