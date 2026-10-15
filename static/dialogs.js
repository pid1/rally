/* Rally's own questions and messages, in place of the browser's confirm() and
 * alert(), and the in-place messages a form reports with.
 *
 * The dialog is `#dialog-modal-overlay` (templates/_dialog_modal.html), which
 * base.html includes after every page's content. Every overlay shares one
 * z-index, so the later one in the document draws on top: the dialog can be
 * asked over any page modal (removing an item from the item modal).
 *
 * Loaded by base.html on every page, after modal.js, which opens and closes it.
 *
 *   confirmModal({ title, messageHtml, confirmLabel })  → Promise<boolean>
 *       A question: the action as the title, the verb on the confirm button,
 *       and Cancel. True on the confirm button, false on Cancel or a click on
 *       the backdrop.
 *   alertModal({ messageHtml })                          → Promise<void>
 *       A failure: the message and OK, with no title. Resolves when it closes.
 *   showFormMessage(id, text, role) / clearFormMessage(id)
 *       A form check ("alert") or a notice ("status") in a `.form-message`,
 *       next to what it is about, with the form left open.
 *   joinNames(names)
 *       Names in a sentence, quoted, with the Oxford comma.
 *
 * `messageHtml` is inserted as markup, so the caller escapes every name in it
 * before wrapping it in <strong>.
 */
(function () {
    'use strict';

    const OVERLAY_ID = 'dialog-modal-overlay';
    let resolvePending = null;

    function part(id) {
        return document.getElementById(id);
    }

    function close(answer) {
        hideModalOverlay(OVERLAY_ID);
        const resolve = resolvePending;
        resolvePending = null;
        if (resolve) resolve(answer);
    }

    function wire() {
        const overlay = part(OVERLAY_ID);
        if (!overlay || overlay.dataset.dialogWired) return;
        overlay.dataset.dialogWired = 'true';
        part('btn-dialog-modal-confirm').addEventListener('click', () => close(true));
        part('btn-dialog-modal-cancel').addEventListener('click', () => close(false));
        overlay.addEventListener('click', (e) => {
            if (e.target === overlay) close(false);
        });
    }

    function open({ title, messageHtml, confirmLabel, cancellable }) {
        wire();
        // A dialog asked while another is still open answers the first one
        // as Cancel: only one question is ever waiting.
        if (resolvePending) close(false);
        const heading = part('dialog-modal-title');
        heading.textContent = title || '';
        // Set inline rather than through `hidden`, which a rule that sets
        // `display` (as `.btn` does) would outrank.
        heading.style.display = title ? '' : 'none';
        part('dialog-modal-message').innerHTML = messageHtml;
        part('btn-dialog-modal-confirm').textContent = confirmLabel;
        part('btn-dialog-modal-cancel').style.display = cancellable ? '' : 'none';
        showModalOverlay(OVERLAY_ID);
        return new Promise((resolve) => { resolvePending = resolve; });
    }

    function confirmModal({ title, messageHtml, confirmLabel }) {
        return open({ title, messageHtml, confirmLabel, cancellable: true });
    }

    function alertModal({ messageHtml }) {
        return open({ title: '', messageHtml, confirmLabel: 'OK', cancellable: false })
            .then(() => undefined);
    }

    /* A form check (role "alert") or a notice (role "status"), reported in
       the form's `.form-message`, cleared by the next change to what it is
       about. */
    function showFormMessage(id, text, role = 'alert') {
        const message = part(id);
        if (!message) return;
        message.setAttribute('role', role);
        message.textContent = text;
        message.hidden = false;
        // A form scrolls, and a refusal nobody can see is no refusal.
        message.scrollIntoView({ block: 'nearest' });
    }

    function clearFormMessage(id) {
        const message = part(id);
        if (message) message.hidden = true;
    }

    /* Names in a sentence, each in straight double quotes so a title with
       "and" in it never reads as two, joined with the Oxford comma. The
       server's `packing_lists.join_names` says the same thing. */
    function joinNames(names) {
        const quoted = names.map(name => `"${name}"`);
        if (quoted.length <= 2) return quoted.join(' and ');
        return `${quoted.slice(0, -1).join(', ')}, and ${quoted[quoted.length - 1]}`;
    }

    window.confirmModal = confirmModal;
    window.alertModal = alertModal;
    window.showFormMessage = showFormMessage;
    window.clearFormMessage = clearFormMessage;
    window.joinNames = joinNames;
})();
