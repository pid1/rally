/* The markup for one titled group of list rows.
 *
 *   section.list-group[data-group]
 *     > .list-group-header > .list-group-name + .list-group-rule + .list-group-count
 *     + .list-container > (rows, or an empty state)
 *
 * Shopping groups by store, Purchased by store, and a checklist by its own
 * groups, and each used to write this block out by hand. The header and the
 * rows are wrapped together so a group is one element: `drag_reorder.js` aims a
 * drop at the whole block, heading included, and an empty group is still a
 * target you can drag the last row of a group back into.
 *
 * `rowsHtml` is markup the caller has already escaped. Everything else is text
 * and is escaped here. `name` may be omitted for a list that has no groups to
 * tell apart (a checklist nobody has grouped), which renders the rows with no
 * header and keeps the wrapper, so the drag contract still holds.
 */
(function () {
    'use strict';

    function escapeText(text) {
        const div = document.createElement('div');
        div.textContent = text == null ? '' : String(text);
        return div.innerHTML;
    }

    function escapeAttribute(text) {
        return escapeText(text).replace(/"/g, '&quot;');
    }

    function listGroupHtml({ key, name, countLabel, rowsHtml, emptyText }) {
        const rows = rowsHtml || `<div class="container-empty-state">${escapeText(emptyText || 'Nothing here right now.')}</div>`;
        const header = name == null ? '' : `
                <div class="list-group-header">
                    <span class="list-group-name">${escapeText(name)}</span>
                    <span class="list-group-rule"></span>
                    ${countLabel ? `<span class="list-group-count">${escapeText(countLabel)}</span>` : ''}
                </div>`;
        return `
            <section class="list-group" data-group="${escapeAttribute(key)}">${header}
                <div class="list-container">${rows}</div>
            </section>
        `;
    }

    window.listGroupHtml = listGroupHtml;
})();
