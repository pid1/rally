/* The dot between quiet buttons side by side, kept off the start of a line.
 *
 * The stylesheet draws a "·" before every `.btn--quiet` that follows another
 * (`.btn--quiet + .btn--quiet`). Where a row wraps, the second button can
 * start a new line, and a line that opens with a dot reads as a stray mark.
 * No selector can see where a line wraps, so this file looks: a button whose
 * top is below its neighbor's bottom, or whose neighbor is not drawn at all,
 * starts a line and is marked `.is-line-start`, which hides its dot and draws
 * it back to the line's edge.
 *
 * Rows are rendered by every page's own script, appear when a modal opens or
 * a "View more" unfolds, and rewrap when the window changes width, so the
 * marks are recomputed after any of those, at most once a frame. A pass that
 * changes nothing writes nothing, which is what lets it watch the attributes
 * it sets without looping.
 */
(function () {
    const SELECTOR = '.btn--quiet + .btn--quiet';
    let scheduled = false;

    function startsLine(button) {
        const before = button.previousElementSibling;
        if (!before || before.getClientRects().length === 0) return true;
        if (button.getClientRects().length === 0) return false;
        return button.getBoundingClientRect().top >= before.getBoundingClientRect().bottom - 1;
    }

    function mark() {
        scheduled = false;
        document.querySelectorAll(SELECTOR).forEach(button => {
            const start = startsLine(button);
            if (button.classList.contains('is-line-start') !== start) {
                button.classList.toggle('is-line-start', start);
            }
        });
    }

    function schedule() {
        if (scheduled) return;
        scheduled = true;
        requestAnimationFrame(mark);
    }

    document.addEventListener('DOMContentLoaded', () => {
        schedule();
        new MutationObserver(schedule).observe(document.body, {
            childList: true,
            subtree: true,
            attributes: true,
            attributeFilter: ['class', 'style', 'open', 'hidden'],
        });
        window.addEventListener('resize', schedule);
        // The web font changes every word's width once it arrives.
        if (document.fonts) document.fonts.ready.then(schedule);
    });
})();
