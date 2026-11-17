/* Long free text, folded to its first few sentences until asked for.
 *
 * A task's details can run to a paragraph — clinic hours, who to ask for,
 * what to bring — and shown whole they push the rest of the list a screen
 * further down, on a phone most of all. So a long description shows roughly
 * its first two sentences, ending where a sentence ends rather than mid-word
 * at a hard count, with `View more` on its own line under it, looking like
 * every other View more in Rally. The rest is one tap away, and the tap can
 * land anywhere on the description, not only on the button.
 *
 * Call longTextHtml(text, key) where a template would call
 * escapeHtmlWithPhoneLinks() on a description. It escapes through that helper
 * (so phone numbers stay links in both the short and the full text) and its
 * output is safe to hand to innerHTML. `key` identifies the text across
 * re-renders — a filter, a sort, a save — so an opened description stays open.
 *
 * Text short enough to show whole comes back exactly as
 * escapeHtmlWithPhoneLinks() would return it, with no wrapper.
 */
(function () {
    'use strict';

    /* About how much of a description shows folded: the length of two
       ordinary sentences. The cut goes to the last sentence end within
       TARGET + SLACK, so two sentences that run a little long still show
       whole rather than being cut back to one. */
    const TARGET = 210;
    const SLACK = 25;
    /* Folding away less than this hides a few words behind a tap, which
       costs more than it saves. */
    const MIN_HIDDEN = 40;
    /* A first sentence ending before this is too little to read on its own,
       so the cut falls back to a word boundary near TARGET instead. */
    const MIN_SHOWN = 80;

    /* Words a period follows without ending a sentence: "Dr. Ruiz". */
    const ABBREVIATIONS = new Set([
        'mr', 'mrs', 'ms', 'dr', 'st', 'jr', 'sr', 'vs', 'etc', 'ave', 'mt',
        'no', 'approx', 'appt', 'dept', 'e.g', 'i.e', 'a.m', 'p.m',
    ]);

    const expanded = new Set();

    /* Offsets just past each sentence end: . ! or ? (with any closing quote
       or bracket) followed by whitespace and then something that can start
       a sentence, or a line break. */
    function sentenceEnds(text) {
        const ends = [];
        const pattern = /[.!?]+["'”’)\]]*(?=\s)/g;
        let match;
        while ((match = pattern.exec(text)) !== null) {
            const end = match.index + match[0].length;
            const rest = text.slice(end);
            const next = rest.trimStart()[0];
            if (next === undefined) continue;
            const lineBreak = /^[ \t]*\n/.test(rest);
            if (!lineBreak && !/[A-Z0-9"'“‘(\[]/.test(next)) continue;
            if (match[0][0] === '.' && !lineBreak) {
                const word = (text.slice(0, match.index).match(/(\S+)$/) || ['', ''])[1];
                const bare = word.toLowerCase().replace(/^[("'“‘\[]+/, '');
                // An initial ("J. Smith") or a known abbreviation.
                if (/^[a-z]$/.test(bare) || ABBREVIATIONS.has(bare)) continue;
            }
            ends.push(end);
        }
        // A line break ends a thought even without punctuation.
        const breaks = /\S(?=[ \t]*\n)/g;
        while ((match = breaks.exec(text)) !== null) ends.push(match.index + 1);
        return [...new Set(ends)].sort((a, b) => a - b);
    }

    /* Where to fold the text: { cut, ellipsis }, or null to show it whole. */
    function longTextCut(text) {
        const source = text == null ? '' : String(text);
        const body = source.trimEnd();
        if (body.length <= TARGET + SLACK) return null;

        let cut = 0;
        for (const end of sentenceEnds(body)) {
            if (end > TARGET + SLACK) break;
            cut = end;
        }
        let ellipsis = false;
        if (cut < MIN_SHOWN) {
            // No sentence ends in reach: fold at the last space before
            // TARGET, and say the sentence goes on.
            const space = body.lastIndexOf(' ', TARGET);
            cut = space > MIN_SHOWN ? space : TARGET;
            ellipsis = true;
        }
        if (body.length - cut < MIN_HIDDEN) return null;
        return { cut, ellipsis };
    }

    function longTextHtml(text, key) {
        const source = text == null ? '' : String(text);
        const fold = longTextCut(source);
        if (!fold) return escapeHtmlWithPhoneLinks(source);

        const open = key != null && expanded.has(String(key));
        const short = source.slice(0, fold.cut).trimEnd() + (fold.ellipsis ? '…' : '');
        const keyAttr = key != null
            ? ` data-long-text-key="${String(key).replace(/[^\w:-]/g, '')}"`
            : '';
        return `<span class="long-text${open ? ' is-expanded' : ''}"${keyAttr}>`
            + `<span class="long-text-short">${escapeHtmlWithPhoneLinks(short)}</span>`
            + `<span class="long-text-full">${escapeHtmlWithPhoneLinks(source)}</span>`
            + `<span class="long-text-toggle" role="button" tabindex="0" aria-expanded="${open}">`
            + '<span class="long-text-more">View more</span>'
            + '<span class="long-text-less">View less</span>'
            + '</span></span>';
    }

    function toggle(host) {
        const open = host.classList.toggle('is-expanded');
        const button = host.querySelector('.long-text-toggle');
        if (button) button.setAttribute('aria-expanded', String(open));
        const key = host.dataset.longTextKey;
        if (key) {
            if (open) expanded.add(key);
            else expanded.delete(key);
        }
    }

    /* One listener for every long text on the page, so a list that
       re-renders needs no rewiring. A click anywhere on the text toggles it,
       except on a link inside it (a phone number to dial) or at the end of
       a drag that selected some of it to copy. */
    document.addEventListener('click', (event) => {
        const host = event.target.closest && event.target.closest('.long-text');
        if (!host) return;
        const onButton = event.target.closest('.long-text-toggle');
        if (!onButton) {
            if (event.target.closest('a')) return;
            const selection = window.getSelection && window.getSelection();
            if (selection && !selection.isCollapsed && host.contains(selection.anchorNode)) return;
        }
        toggle(host);
    });

    /* The toggle is a span with role=button rather than a <button>, so it
       carries no button styling to undo and looks exactly like the
       `.disclosure` summary it shares its rules with. So it takes Enter and
       Space itself. */
    document.addEventListener('keydown', (event) => {
        if (event.key !== 'Enter' && event.key !== ' ') return;
        const button = event.target.closest && event.target.closest('.long-text-toggle');
        if (!button) return;
        event.preventDefault();
        toggle(button.closest('.long-text'));
    });

    window.longTextHtml = longTextHtml;
    window.longTextCut = longTextCut;
})();
