/* Phone numbers in the text a family writes, turned into tel: links.
 *
 * Rally is read on a phone as often as on the wall tablet, and a number
 * written into a task — "Number is 800-111-1234" — is there to be called.
 * Selecting it out of a line of italic text and pasting it into the dialer is
 * the friction that turns a two-minute call into a task that rolls over to
 * tomorrow, so everywhere Rally shows free text the family typed, the numbers
 * in it are links.
 *
 * Call escapeHtmlWithPhoneLinks() wherever a template would otherwise call its
 * own escapeHtml() on a description, note or location. It escapes the text
 * first and builds the anchors itself, so what comes back is safe to hand to
 * innerHTML, and a phone number is the only thing in it that becomes markup.
 *
 * What counts as a phone number:
 *
 *   - A North American one: ten digits, optionally led by 1 or +1, written any
 *     of the ways people write them — 8001111234, 800-111-1234, 800.111.1234,
 *     (800) 111-1234, 1-800-111-1234.
 *   - An international one carrying its + country code: +44 20 7946 0958.
 *
 * What does not, which matters more, because a wrong link is worse than no
 * link: the digit count alone rules out dates (2026-09-11), ZIP+4 codes
 * (98101-1234) and clock times, and the rule that no North American area code
 * begins with 0 or 1 rules out most of what is left — quantity ranges and
 * numbered lists like 100-200-3000. A number is also left alone when a letter,
 * digit or hyphen runs straight into it, so an order number or a serial is not
 * mistaken for something to dial.
 *
 * A bare national number written the way the rest of the world writes it
 * (020 7946 0958) stays plain text: there is no country code to dial it with,
 * and guessing one would be worse than leaving it.
 */
(function () {
    'use strict';

    const ENTITIES = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };

    /* A run of digits and the characters people set between them. Deliberately
       loose — every real decision is made by dialable() below. */
    const RUN = /\+?\(?\d[\d\s().-]*\d/g;

    function escapeHtml(text) {
        return text.replace(/[&<>"']/g, (char) => ENTITIES[char]);
    }

    /* The number to dial, or null when this run of digits is not one.
       The returned number is always E.164, which is what a dialer wants and
       what stops a link from depending on how the number was typed. */
    function dialable(text) {
        const digits = text.replace(/\D/g, '');
        if (text.startsWith('+')) {
            // E.164 tops out at fifteen digits; nothing shorter than eight is
            // a number another country could reach, and none starts with 0.
            const usable = digits.length >= 8 && digits.length <= 15 && digits[0] !== '0';
            return usable ? `+${digits}` : null;
        }
        const national = digits.length === 11 && digits[0] === '1' ? digits.slice(1) : digits;
        if (national.length !== 10) return null;
        // No North American area code begins with 0 or 1, so a run shaped
        // like 100-200-3000 is a list of numbers rather than a number.
        if (national[0] <= '1') return null;
        return `+1${national}`;
    }

    /* A number must stand on its own. A letter, digit or hyphen either side of
       it means it is part of something longer — an order number, a serial, a
       range — and not a number to dial. A trailing period is a sentence
       ending, so it is allowed. */
    function standsAlone(source, start, end) {
        return !/[\w\-./]/.test(source[start - 1] || '') && !/[\w\-/]/.test(source[end] || '');
    }

    /* The first phone number inside one run, as absolute offsets into source.
     *
     * A run can pick up digits either side of the number it contains — "call
     * 800-111-1234 3 times" is one run — so every span of whole digit groups
     * is tried, longest first from the leftmost start, and the first span that
     * both stands alone and dials wins. */
    function findNumber(source, run, offset) {
        const groups = [];
        const digits = /\d+/g;
        let group;
        while ((group = digits.exec(run)) !== null) {
            groups.push([group.index, group.index + group[0].length]);
        }
        for (let first = 0; first < groups.length; first++) {
            for (let last = groups.length - 1; last >= first; last--) {
                let from = groups[first][0];
                const to = groups[last][1];
                // A + or a ( immediately in front belongs to the number: it
                // decides whether this is an international one, and dropping
                // it would leave a stray bracket beside the link.
                while (from > 0 && (run[from - 1] === '+' || run[from - 1] === '(')) from--;
                if (!standsAlone(source, offset + from, offset + to)) continue;
                const number = dialable(run.slice(from, to));
                if (number) return { start: offset + from, end: offset + to, number };
            }
        }
        return null;
    }

    /* Escape text for innerHTML, with its phone numbers marked up as tel:
       links. The link text is the number exactly as it was written; only the
       href is normalized. */
    function escapeHtmlWithPhoneLinks(text) {
        const source = text == null ? '' : String(text);
        let html = '';
        let cursor = 0;
        let run;
        RUN.lastIndex = 0;
        while ((run = RUN.exec(source)) !== null) {
            const found = findNumber(source, run[0], run.index);
            if (!found) continue;
            html += escapeHtml(source.slice(cursor, found.start));
            html += `<a class="phone-link" href="tel:${found.number}">`
                + `${escapeHtml(source.slice(found.start, found.end))}</a>`;
            cursor = found.end;
            RUN.lastIndex = found.end;
        }
        return html + escapeHtml(source.slice(cursor));
    }

    window.escapeHtmlWithPhoneLinks = escapeHtmlWithPhoneLinks;
})();
