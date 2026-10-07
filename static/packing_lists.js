/* What the packing list pages share: escaping, how dates and progress are
 * worded, how a packing list's items are grouped, and a day's card.
 *
 * A packing list is grouped one of two ways — by owner (who owns what) or by bag
 * (what goes in each bag) — chosen once for the page. Pages pass that choice
 * around as a *lens*: `{ view: 'owner' | 'bag', members, bags }`, the family
 * in Rally's order and the household's bags A to Z. The item order is the
 * server's (`rally.packing_lists.ordered_items`) and arrives sorted; `viewSections`
 * only cuts it into groups, so no two views can disagree about it.
 *
 * A packing list also carries its bags (`day.bags`, `template.bags`): every
 * bag something on it is in, and every bag those go in, as that list reads
 * them, outermost first. By owner, each person's group opens with their bags
 * (`.list-group-lead`); on a day each has a "grabbed" checkbox. By bag, the
 * groups follow that order and name the bag each goes in.
 */
(function () {
    'use strict';

    const EVERYONE = 'Everyone';
    const NO_BAG = 'No bag';
    // After a bag's name, saying it is a bag to grab rather than an item.
    const BAG_GLYPH = '▣';
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

    function bagCount(n) {
        return `${n} bag${n === 1 ? '' : 's'}`;
    }

    /* A day's second progress line: "1 of 3 bags grabbed", "All 3 bags
     * grabbed" — and "1 of 1 bag grabbed" for a lone bag, since "All 1 bag"
     * does not read. */
    function bagsProgressLabel(checked, total) {
        if (checked === total && total > 1) return `All ${bagCount(total)} grabbed`;
        return `${checked} of ${bagCount(total)} grabbed`;
    }

    /* A packing list template's lead time, on its row. */
    function packDaysLabel(daysBefore) {
        if (daysBefore === 0) return 'Pack the day of';
        if (daysBefore === 1) return 'Pack the day before';
        return `Pack ${daysBefore} days before`;
    }

    /* The household's bags in the order a list reads them: the list's own
     * bags first (outermost first, then the bags inside each), then any other
     * bag A to Z. Each named for the bag it goes in on that list. */
    function bagGroupsInListOrder(lens, listBags) {
        const known = new Set(lens.bags.map(b => b.id));
        const listed = listBags.filter(b => known.has(b.id)).map(b => ({
            id: b.id,
            name: b.parent_bag_id != null && known.has(b.parent_bag_id)
                ? `${bagLabel(b.id, lens)} · in ${bagLabel(b.parent_bag_id, lens)}`
                : bagLabel(b.id, lens),
        }));
        const seen = new Set(listed.map(b => b.id));
        return [...listed, ...lens.bags.filter(b => !seen.has(b.id))
            .map(b => ({ id: b.id, name: bagLabel(b.id, lens) }))];
    }

    /* What a bag is called on the page. A bag is unique by its name and its
     * owner, so two can share a name — Emma's Backpack and Jake's — and then,
     * and only then, the name carries the (household) owner: "Backpack
     * (Emma)". `bags` are the household's, `members` the family. */
    function bagLabelIn(id, bags, members) {
        const bag = bags.find(b => b.id === id);
        if (!bag) return '';
        const key = bag.name.toLowerCase();
        if (!bags.some(other => other.id !== id && other.name.toLowerCase() === key)) return bag.name;
        const owner = members.find(m => m.id === bag.owner_id);
        return `${bag.name} (${owner ? owner.name : EVERYONE})`;
    }

    function bagLabel(id, lens) {
        return bagLabelIn(id, lens.bags, lens.members);
    }

    /* Cut a packing list's items into the groups of the lens's view: owners in
     * the family's order then Everyone, or bags then No bag. Only groups with
     * something in them are kept, and every group is headed, even when it is
     * the only one. An owner or bag that no longer exists reads as none.
     *
     * `listBags` are the list's bags. By owner, each group also carries the
     * bags that person owns (`section.bags`), and a person who owns a bag but
     * nothing in it still has a group. By bag, the groups follow the list's
     * nesting, a bag before the bags inside it. */
    function viewSections(items, lens, listBags = []) {
        const byOwner = lens.view !== 'bag';
        const entries = byOwner ? lens.members : bagGroupsInListOrder(lens, listBags);
        const named = entries
            .map(entry => ({ key: String(entry.id), id: entry.id, name: entry.name, items: [], bags: [] }));
        const none = { key: NONE_KEY, id: null, name: byOwner ? EVERYONE : NO_BAG, items: [], bags: [] };
        const byId = new Map(named.map(section => [section.id, section]));
        items.forEach(item => {
            const id = byOwner ? item.owner_id : item.bag_id;
            (byId.get(id) || none).items.push(item);
        });
        if (byOwner) {
            listBags.forEach(bag => (byId.get(bag.owner_id) || none).bags.push(bag));
        }
        return [...named, none].filter(section => section.items.length > 0 || section.bags.length > 0);
    }

    /* The other half of an item, under its name: its bag when grouped by
     * owner, its owner when grouped by bag — "No bag" or "Everyone" when it
     * has none, so every item reads the same way. */
    function otherDimension(item, lens, listBags = []) {
        const byOwner = lens.view !== 'bag';
        const list = byOwner ? lens.bags : lens.members;
        const id = byOwner ? item.bag_id : item.owner_id;
        const found = list.find(entry => entry.id === id);
        if (!found) return byOwner ? NO_BAG : EVERYONE;
        if (!byOwner) return found.name;
        return bagFromGroup(found.id, groupOwnerOf(item.owner_id, lens), lens, listBags);
    }

    /* Whose group a row is drawn in, By owner: its owner's, or Everyone's
     * when it has none or that member is gone. */
    function groupOwnerOf(ownerId, lens) {
        return lens.members.some(m => m.id === ownerId) ? ownerId : null;
    }

    /* A bag named from inside somebody's group, By owner: an item's bag, or
     * the bag a bag goes in. When that bag is the group's person's own, its
     * name is enough. When somebody else carries it — as this list reads it,
     * since that is who grabs it — the name says who: "Suitcase (Dad)" under
     * Emma, so Emma can see her toiletries bag is going in a bag she will not
     * be grabbing. That also tells apart two bags of one name. */
    function bagFromGroup(bagId, groupOwnerId, lens, listBags) {
        const bag = lens.bags.find(b => b.id === bagId);
        if (!bag) return '';
        const onList = listBags.find(b => b.id === bagId);
        const ownerId = groupOwnerOf(onList ? onList.owner_id : bag.owner_id, lens);
        if (ownerId === groupOwnerId) return bag.name;
        const owner = lens.members.find(m => m.id === ownerId);
        return `${bag.name} (${owner ? owner.name : EVERYONE})`;
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

    /* A day's group header: "2 of 5 packed · 1 of 2 bags", either half
     * alone when the group has only items or only bags. */
    function sectionCountLabel(section) {
        if (!section.name) return null;
        const parts = [];
        if (section.items.length) {
            parts.push(progressLabel(section.items.filter(i => i.checked).length, section.items.length));
        }
        if (section.bags.length) {
            parts.push(`${section.bags.filter(b => b.checked).length} of ${bagCount(section.bags.length)}`);
        }
        return parts.join(' · ');
    }

    /* A template's group header: "5 items · 2 bags". */
    function templateSectionCountLabel(section) {
        if (!section.name) return null;
        const parts = [];
        if (section.items.length) parts.push(itemCount(section.items.length));
        if (section.bags.length) parts.push(bagCount(section.bags.length));
        return parts.join(' · ');
    }

    /* One bag at the head of its owner's group. ▣ says it is a bag, not an
     * item; under it, the bag it goes in on this list. `day` gives it a
     * "grabbed" checkbox; a template's has none. Bags do not drag and have no
     * Edit: Change bags is where a list reads one differently. */
    function bagRowHtml(bag, { listBags, lens, day = null, readOnly = false }) {
        const parent = bag.parent_bag_id != null ? listBags.find(b => b.id === bag.parent_bag_id) : null;
        // The row sits in its owner's group (as this list reads it), so the
        // owner a shared name would carry is already the heading above it.
        // Only a list that gave the bag to somebody else still needs it:
        // Emma's Backpack under Jake reads "Backpack (Emma)".
        const household = lens.bags.find(b => b.id === bag.id);
        const name = household && household.owner_id === bag.owner_id
            ? bag.name
            : bagLabel(bag.id, lens) || bag.name;
        const box = day ? `
                <label class="item-checkbox">
                    <input type="checkbox" ${bag.checked ? 'checked' : ''} ${readOnly ? 'disabled' : ''}
                           data-day="${day.id}" data-bag="${bag.id}"
                           aria-label="Grabbed ${escapeAttr(name)}">
                </label>` : '';
        return `
            <div class="editable-item ${day && bag.checked ? 'completed' : ''} ${readOnly ? 'is-read-only' : ''}" data-bag-row="${bag.id}">${box}
                <div class="editable-item-content">
                    <div class="editable-item-title">${escapeHtml(name)} <span class="title-indicator" title="A bag to grab">${BAG_GLYPH}</span></div>
                    ${parent ? `<div class="editable-item-description">in ${escapeHtml(bagFromGroup(parent.id, groupOwnerOf(bag.owner_id, lens), lens, listBags) || parent.name)}</div>` : ''}
                </div>
            </div>
        `;
    }

    /* The quiet button that opens Change bags, in the actions under a packing
     * list's items: beside Remove from day on a day's entry, after Add Item
     * on a template's row. Only when the list has bags. */
    function changeBagsHtml(attribute, id, listBags) {
        if (!listBags.length) return '';
        return `<button type="button" class="btn btn--sm btn--quiet" ${attribute}="${id}">Change bags</button>`;
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
        const other = otherDimension(item, lens, day.bags || []);
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
                    <div class="editable-item-title-line">
                        <div class="editable-item-title">${escapeHtml(item.name)}${markHtml}</div>
                        ${other ? `<span class="editable-item-description editable-item-aside">${escapeHtml(other)}</span>` : ''}
                    </div>
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
     *
     * Each group folds on its own, so one person or one bag can be packed at
     * a time. `isGroupOpen(key)` says which groups the page has open; a group
     * it has not heard of is folded, which is how every list first opens.
     *
     * `showItem(item)` narrows the rows drawn (the Coming Up's Items filter),
     * bags by whether they were grabbed as items by whether they were packed;
     * a group with no row left is not drawn. Counts are still the whole
     * group's, so progress reads the same with rows hidden.
     */
    function dayCardHtml(day, { readOnly = false, open = false, isGroupOpen = () => false, showItem = () => true, lens } = {}) {
        const listBags = day.bags || [];
        // A day is done when every item is packed and every bag grabbed. It
        // dims to say so — except in the archive, where every day is past and
        // dimming them all only makes them hard to read. Packed items inside
        // are still muted there, row by row.
        const done = !readOnly && isDone(day);
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
            : viewSections(day.items, lens, listBags).map(section => {
                const shown = section.items.filter(showItem);
                const shownBags = section.bags.filter(showItem);
                if (shown.length === 0 && shownBags.length === 0) return '';
                const key = `${day.id}:${section.key}`;
                return listGroupHtml({
                    key,
                    name: section.name,
                    countLabel: sectionCountLabel(section),
                    leadHtml: shownBags.map(bag => bagRowHtml(bag, { listBags, lens, day, readOnly })).join(''),
                    rowsHtml: shown.map(item => dayItemRowHtml(day, item, readOnly, lens)).join(''),
                    collapsible: true,
                    open: isGroupOpen(key),
                });
            }).join('');
        const actions = readOnly ? '' : `
            <div class="editable-item-actions packing-list-day-actions">
                <button type="button" class="btn btn--sm btn--secondary" data-check-all-day="${day.id}">Check All</button>
                <button type="button" class="btn btn--sm btn--secondary" data-reset-day="${day.id}">Uncheck All</button>
                <button type="button" class="btn btn--sm btn--secondary" data-add-day-item="${day.id}">Add Item</button>
                ${changeBagsHtml('data-change-day-bags', day.id, listBags)}
                <button type="button" class="btn btn--sm btn--quiet" data-remove-day="${day.id}">${isTemplated(day) ? 'Remove from day' : 'Delete'}</button>
                ${day.changed_count ? `<button type="button" class="btn btn--sm btn--quiet" data-resync-day="${day.id}">Resync with template</button>` : ''}
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
                    <div class="packing-list-progress-line">
                        <span class="editable-item-meta packing-list-progress" data-progress role="status" aria-live="polite">${escapeHtml(progressLabel(day.checked, day.total))}</span>
                        <span class="editable-item-meta packing-list-bag-progress" data-bag-progress role="status" aria-live="polite"${day.bags_total ? '' : ' hidden'}>${escapeHtml(bagsProgressLabel(day.bags_checked || 0, day.bags_total || 0))}</span>
                    </div>
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
     * `isOpen(day)` says which entries were open before a re-render, and
     * `isGroupOpen(key)` which of their groups; `showItem` is `dayCardHtml`'s. */
    function dayBoxesHtml(days, { today, readOnly = false, isOpen = () => false, isGroupOpen = () => false, showItem, lens } = {}) {
        const boxes = [];
        days.forEach(day => {
            const last = boxes[boxes.length - 1];
            if (last && last.date === day.date) last.days.push(day);
            else boxes.push({ date: day.date, days: [day] });
        });
        return boxes.map(box => `
            <section class="day-box day-box--divided" data-day-box="${box.date}">
                ${box.days.map(day => dayCardHtml(day, { readOnly, open: isOpen(day), isGroupOpen, showItem, lens })).join('')}
                <div class="date-label">${escapeHtml(boxDateLabel(box.date, today))}</div>
            </section>
        `).join('');
    }

    /* Every item packed and every bag grabbed. */
    function isDone(day) {
        return day.total > 0 && day.checked === day.total
            && (day.bags_checked || 0) === (day.bags_total || 0);
    }

    /* Bring a card's counts up to date without re-rendering its rows, which
     * would take focus off the box that was just ticked. */
    function updateDayCardCounts(card, day, lens) {
        card.classList.toggle('completed', isDone(day));
        card.querySelector('[data-progress]').textContent = progressLabel(day.checked, day.total);
        const bagLine = card.querySelector('[data-bag-progress]');
        bagLine.hidden = !day.bags_total;
        bagLine.textContent = bagsProgressLabel(day.bags_checked || 0, day.bags_total || 0);
        viewSections(day.items, lens, day.bags || []).forEach(section => {
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
        templateSectionCountLabel,
        bagRowHtml,
        bagLabelIn,
        changeBagsHtml,
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
