/* The site menu: a sidebar that opens from the right, shared by every page.
 *
 * Markup contract (templates/base.html):
 *
 *   button[data-sidebar-toggle]   the menu button, aria-controls the sidebar
 *   .sidebar-scrim                the dim layer over the page
 *   nav.sidebar#site-sidebar      the links
 *
 * Open state is one class on <body>, so the stylesheet owns every visual
 * consequence of it and this file only decides *when*.
 *
 * The page stays scrollable while the sidebar is open, so nothing here locks
 * scrolling. A tap on the scrim closes the sidebar and goes no further — the
 * scrim is what the tap lands on, so the checkbox underneath never sees it.
 * Closing happens on `click`, never on `pointerdown`: removing the scrim
 * before the click fires would hand that click to whatever was beneath it.
 * A drag produces no click at all, which is what lets a finger scroll the
 * page through the scrim without closing anything.
 */
(function () {
    'use strict';

    const OPEN_CLASS = 'sidebar-open';

    function init() {
        const toggle = document.querySelector('[data-sidebar-toggle]');
        const scrim = document.querySelector('[data-sidebar-scrim]');
        const sidebar = toggle && document.getElementById(toggle.getAttribute('aria-controls'));
        if (!toggle || !scrim || !sidebar) return;

        const isOpen = () => document.body.classList.contains(OPEN_CLASS);

        function open() {
            document.body.classList.add(OPEN_CLASS);
            toggle.setAttribute('aria-expanded', 'true');
            toggle.setAttribute('aria-label', 'Close menu');
            // Land on the current page's link when there is one, so a
            // keyboard user starts from where they are.
            const target = sidebar.querySelector('a[aria-current="page"]') || sidebar.querySelector('a');
            if (target) target.focus({ preventScroll: true });
        }

        function close(returnFocus) {
            if (!isOpen()) return;
            document.body.classList.remove(OPEN_CLASS);
            toggle.setAttribute('aria-expanded', 'false');
            toggle.setAttribute('aria-label', 'Open menu');
            if (returnFocus) toggle.focus({ preventScroll: true });
        }

        toggle.addEventListener('click', function () {
            if (isOpen()) close(true);
            else open();
        });

        scrim.addEventListener('click', function (e) {
            e.preventDefault();
            e.stopPropagation();
            close(false);
        });

        document.addEventListener('keydown', function (e) {
            if (e.key === 'Escape' && isOpen()) {
                e.preventDefault();
                close(true);
            }
        });

        // Tabbing out of the sidebar would put focus on a dimmed page the
        // pointer cannot reach, so leaving it by keyboard closes it.
        sidebar.addEventListener('focusout', function (e) {
            if (isOpen() && e.relatedTarget && !sidebar.contains(e.relatedTarget)
                && e.relatedTarget !== toggle) {
                close(false);
            }
        });

        // Widening the window past the docking width puts the sidebar on the
        // page permanently and hides the button, so an overlay opened before
        // the resize has nothing left to close it. The stylesheet owns the
        // breakpoint; a hidden button is how this file learns it was crossed.
        window.addEventListener('resize', function () {
            if (isOpen() && getComputedStyle(toggle).display === 'none') close(false);
        });

        // Back/forward restores a page from the cache exactly as it was left,
        // which would be with the sidebar open over it after following a link.
        window.addEventListener('pageshow', function (e) {
            if (e.persisted) close(false);
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
