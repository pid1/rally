/* What the packing list pages share: escaping, how dates and progress are
 * worded, how a packing list's items are grouped, and a day's card.
 *
 * A packing list is grouped one of two ways — by owner (who owns what) or by bag
 * (what goes in each bag) — chosen once for the page. Pages pass that choice
 * around as a *lens*: `{ view: 'owner' | 'bag', members, bags }`, the family
 * in Rally's order and the household's bags A to Z. The item order is the
 * server's (`rally.packing_lists.ordered_items`) and arrives sorted; `viewSections`
 * only cuts it into groups, so no two views can disagree about it.
 */
(function () {
    'use strict';

    const EVERYONE = 'Everyone';
    const NO_BAG = 'No bag';
    // The key of the group for no owner, or no bag.
    const NONE_KEY = 'none';
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

    /* A packing list template's lead time, on its row. */
    function packDaysLabel(daysBefore) {
        if (daysBefore === 0) return 'Pack the day of';
        if (daysBefore === 1) return 'Pack the day before';
        return `Pack ${daysBefore} days before`;
    }

    /* Cut a packing list's items into the groups of the lens's view: owners in
     * the family's order then Everyone, or bags A to Z then No bag. Only
     * groups with something in them are kept, and every group is headed, even
     * when it is the only one. An owner or bag that no longer exists reads as
     * none. */
    function viewSections(items, lens) {
        const byOwner = lens.view !== 'bag';
        const named = (byOwner ? lens.members : lens.bags)
            .map(entry => ({ key: String(entry.id), id: entry.id, name: entry.name, items: [] }));
        const none = { key: NONE_KEY, id: null, name: byOwner ? EVERYONE : NO_BAG, items: [] };
        const byId = new Map(named.map(section => [section.id, section]));
        items.forEach(item => {
            const id = byOwner ? item.owner_id : item.bag_id;
            (byId.get(id) || none).items.push(item);
        });
        return [...named, none].filter(section => section.items.length > 0);
    }

    /* The other half of an item, under its name: its bag when grouped by
     * owner, its owner when grouped by bag — "No bag" or "Everyone" when it
     * has none, so every item reads the same way. */
    function otherDimension(item, lens) {
        const byOwner = lens.view !== 'bag';
        const list = byOwner ? lens.bags : lens.members;
        const id = byOwner ? item.bag_id : item.owner_id;
        const found = list.find(entry => entry.id === id);
        if (found) return found.name;
        return byOwner ? NO_BAG : EVERYONE;
    }

    function keyToId(key) {
        return key === NONE_KEY ? null : parseInt(key, 10);
    }

    /* When a day's packing list is packed: its date is the day box's to state.
     * The weekday alone the day before; with more lead, the date too, since a
     * weekday name alone could be either side of a weekend. */
    function packLine(day) {
        if (day.pack_days_before === 0) return 'Pack the day of';
        if (day.pack_days_before === 1) return `Pack ${weekdayOf(day.pack_date)}`;
        const pack = parseDay(day.pack_date);
        return `Pack ${weekdayOf(day.pack_date)}, ${MONTHS[pack.getMonth()].slice(0, 3)} ${pack.getDate()}`;
    }

    function sectionCountLabel(section) {
        return section.name
            ? progressLabel(section.items.filter(i => i.checked).length, section.items.length)
            : null;
    }

    // Notes are linked for phone numbers where the page loads phone_links.js.
    function noteHtml(note) {
        return typeof escapeHtmlWithPhoneLinks === 'function'
            ? escapeHtmlWithPhoneLinks(note)
            : escapeHtml(note);
    }

    /* What makes a day's item differ from its template, if anything: read
     * beside its name, in parentheses. */
    function dayItemMark(item) {
        if (item.source === 'day') return 'added';
        if (item.changed) return 'changed';
        return '';
    }

    /* Whether a day has a template behind it. A templateless day — its
     * template was deleted — has only its own items, and nothing to differ
     * from, so it carries no marks and no "changed" note. */
    function isTemplated(day) {
        return day.packing_list_template_id != null;
    }

    /* A day row's id for drag_reorder.js: a template item and one of the
     * day's own come from different tables, so the id carries its source. */
    function dayRowId(row) {
        return `${row.dataset.source}:${row.dataset.id}`;
    }

    function dayItemRowHtml(day, item, readOnly, lens) {
        const other = otherDimension(item, lens);
        const mark = isTemplated(day) ? dayItemMark(item) : '';
        const markHtml = mark ? ` <span class="item-mark">(${mark})</span>` : '';
        const edit = readOnly ? '' : `
                <div class="editable-item-actions">
                    <button type="button" class="btn btn--sm" data-edit-day-item="${item.id}"
                            data-source="${item.source}" data-day="${day.id}">Edit</button>
                    <button type="button" class="btn drag-handle"
                            aria-label="Reorder ${escapeAttr(item.name)}"
                            title="Drag to reorder, or onto another owner or bag to move it there. Arrow keys work too.">
                        <span aria-hidden="true">⠿</span>
                    </button>
                </div>`;
        return `
            <div class="editable-item ${item.checked ? 'completed' : ''} ${readOnly ? 'is-read-only' : ''}" data-id="${item.id}" data-source="${item.source}">
                <label class="item-checkbox">
                    <input type="checkbox" ${item.checked ? 'checked' : ''} ${readOnly ? 'disabled' : ''}
                           data-day="${day.id}" data-item="${item.id}" data-source="${item.source}"
                           aria-label="Packed ${escapeAttr(item.name)}">
                </label>
                <div class="editable-item-content">
                    <div class="editable-item-title">${escapeHtml(item.name)}${markHtml}</div>
                    ${other ? `<div class="editable-item-description">${escapeHtml(other)}</div>` : ''}
                    ${item.note ? `<div class="editable-item-description">${noteHtml(item.note)}</div>` : ''}
                </div>${edit}
            </div>
        `;
    }

    /* One day's packing list as an entry in its day's box: what it is, when to
     * pack it, how far along, and — under a collapsed "View more" — the
     * packing list itself. The date is the box's to state, not the entry's.
     *
     * Checked rows stay where they are, dimmed: packing goes in list order,
     * and a row that jumps to the bottom loses people's place. `readOnly` is
     * the archive: the boxes still say what was packed, but cannot change,
     * and the day's controls are left out.
     */
    function dayCardHtml(day, { readOnly = false, open = false, lens } = {}) {
        // A fully packed day dims, to say it is done — except in the archive,
        // where every day is past and dimming them all only makes them hard to
        // read. Packed items inside are still muted there, row by row.
        const done = !readOnly && day.total > 0 && day.checked === day.total;
        const label = day.label ? ` <span class="assignee-label">— ${escapeHtml(day.label)}</span>` : '';
        // ⧉ says the list comes from a template, so editing it there reaches
        // this day too; ↻ that a schedule put it here. A templateless day
        // carries neither.
        const fromTemplate = isTemplated(day)
            ? ' <span class="title-indicator" title="From a packing list template">⧉</span>'
            : '';
        const repeats = day.schedule_id
            ? ' <span class="title-indicator" title="Added by a recurring schedule">↻</span>'
            : '';
        // Group keys carry the day's id: every day's groups share one drag
        // container, and a drop has to know whose group it landed in.
        const items = day.items.length === 0
            ? '<div class="container-empty-state">Nothing on this day yet.</div>'
            : viewSections(day.items, lens).map(section => listGroupHtml({
                key: `${day.id}:${section.key}`,
                name: section.name,
                countLabel: sectionCountLabel(section),
                rowsHtml: section.items.map(item => dayItemRowHtml(day, item, readOnly, lens)).join(''),
            })).join('');
        const actions = readOnly ? '' : `
            <div class="editable-item-actions packing-list-day-actions">
                <button type="button" class="btn btn--sm btn--secondary" data-check-all-day="${day.id}">Check All</button>
                <button type="button" class="btn btn--sm btn--secondary" data-reset-day="${day.id}">Uncheck All</button>
                <button type="button" class="btn btn--sm btn--secondary" data-add-day-item="${day.id}">Add Item</button>
                <button type="button" class="btn btn--sm btn--quiet" data-remove-day="${day.id}">${isTemplated(day) ? 'Remove from day' : 'Delete'}</button>
            </div>`;
        // How far this day has drifted from its template, beside its Edit.
        // A templateless day's count is always 0, so it never shows.
        const changes = day.changed_count
            ? `<span class="editable-item-description day-changes">${day.changed_count} item${day.changed_count === 1 ? '' : 's'} changed</span>`
            : '';
        const edit = readOnly ? '' : `
                <div class="editable-item-actions">
                    ${changes}
                    <button type="button" class="btn btn--sm" data-edit-day="${day.id}">Edit</button>
                </div>`;
        return `
            <div class="day-box-entry ${done ? 'completed' : ''}" data-day-card="${day.id}">
                <div class="editable-item-content">
                    <div class="editable-item-title">${escapeHtml(day.name)}${label}${fromTemplate}${repeats}</div>
                    <div class="editable-item-meta">${escapeHtml(packLine(day))}</div>
                    <div class="editable-item-meta packing-list-progress" data-progress role="status" aria-live="polite">${escapeHtml(progressLabel(day.checked, day.total))}</div>
                </div>${edit}
                <details class="disclosure" data-day="${day.id}"${open ? ' open' : ''}>
                    <summary><span class="disclosure-more">View more</span><span class="disclosure-less">View less</span></summary>
                    <div class="disclosure-body">${items}${actions}</div>
                </details>
            </div>
        `;
    }

    /* A day box's footer: "Today" and "Tomorrow" in place of the date, as the
     * Meal Planner and Notes read; the long date otherwise. */
    function boxDateLabel(value, today) {
        if (today && value === today) return 'Today';
        if (today && value === addDays(today, 1)) return 'Tomorrow';
        return formatLongDate(value, today);
    }

    /* Days' packing lists as day boxes, one per date, the Meal Planner's shape:
     * every packing list on that date stacked inside, the date once as the
     * footer, and a hairline between packing lists (`--divided`) so an opened
     * one does not run into the next. `days` arrive in date order (both
     * listings are sorted by date), so a box closes when the date changes.
     * `isOpen(day)` says which entries were open before a re-render. */
    function dayBoxesHtml(days, { today, readOnly = false, isOpen = () => false, lens } = {}) {
        const boxes = [];
        days.forEach(day => {
            const last = boxes[boxes.length - 1];
            if (last && last.date === day.date) last.days.push(day);
            else boxes.push({ date: day.date, days: [day] });
        });
        return boxes.map(box => `
            <section class="day-box day-box--divided" data-day-box="${box.date}">
                ${box.days.map(day => dayCardHtml(day, { readOnly, open: isOpen(day), lens })).join('')}
                <div class="date-label">${escapeHtml(boxDateLabel(box.date, today))}</div>
            </section>
        `).join('');
    }

    /* Bring a card's counts up to date without re-rendering its rows, which
     * would take focus off the box that was just ticked. */
    function updateDayCardCounts(card, day, lens) {
        const done = day.total > 0 && day.checked === day.total;
        card.classList.toggle('completed', done);
        card.querySelector('[data-progress]').textContent = progressLabel(day.checked, day.total);
        viewSections(day.items, lens).forEach(section => {
            const count = card.querySelector(`.list-group[data-group="${day.id}:${section.key}"] .list-group-count`);
            if (count) count.textContent = sectionCountLabel(section);
        });
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

    window.PackingLists = {
        EVERYONE,
        NO_BAG,
        escapeHtml,
        escapeAttr,
        formatLongDate,
        addDays,
        weekdayOf,
        itemCount,
        packDaysLabel,
        viewSections,
        otherDimension,
        keyToId,
        noteHtml,
        isTemplated,
        dayRowId,
        dayBoxesHtml,
        updateDayCardCounts,
        requestJson,
    };
})();
