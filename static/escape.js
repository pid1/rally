/* Text made safe to put in markup, in one place (#344).
 *
 * Every page builds rows as strings and hands them to innerHTML, so every
 * value that came from a person or a feed goes through escapeHtml() first.
 * There is one, and it escapes quotes as well as `&`, `<` and `>`, so the same
 * call is safe between tags and inside a quoted attribute. The copies each
 * page used to carry had drifted: most left `"` alone, and a calendar event
 * title with a quote in it broke out of its `title="…"` attribute (#343).
 *
 * It does not make a value safe inside JavaScript, such as a string in an
 * `onclick` attribute: the browser decodes `&#39;` back into `'` before the
 * script runs. A value a handler needs goes in a `data-` attribute instead.
 *
 * Loaded by base.html on every page, next to modal.js, dialogs.js and
 * dates.js, and not deferred, so the shared scripts and every page's own
 * script can use it.
 *
 *   escapeHtml(text) → string   `&`, `<`, `>`, `"` and `'` escaped; null and
 *                               undefined are an empty string, anything else
 *                               is made a string first
 */
(function () {
    'use strict';

    const ENTITIES = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };

    function escapeHtml(text) {
        if (text == null) return '';
        return String(text).replace(/[&<>"']/g, (char) => ENTITIES[char]);
    }

    window.escapeHtml = escapeHtml;
})();
