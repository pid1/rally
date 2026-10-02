/* A text field's suggestion menu: the Shopping list's item names, and on the
 * Packing Lists page both item names and bags.
 *
 * Markup contract:
 *
 *   .autocomplete-wrap
 *     > input[role=combobox][aria-controls=<menu id>]
 *     + .autocomplete-menu[role=listbox]
 *
 * The page supplies where suggestions come from and what accepting one does;
 * this owns everything between — the debounce, the sequence guard that drops
 * a reply arriving after a newer keystroke's, ↑/↓/Enter/Esc, the menu, and the
 * optional × that forgets a suggestion.
 *
 *   attachAutocomplete({
 *       input, menu,
 *       source: async (query) => [suggestion, …],
 *       label: (suggestion) => ({ name, detail }),  // detail is optional
 *       onAccept: (suggestion) => void,             // after the field is filled
 *       onForget: async (suggestion) => void,       // omit for no ×
 *       enabled: () => bool,                        // e.g. add mode only
 *       scrollBox,                                  // the .modal-body the menu is shown inside
 *   })
 *
 * Enter with nothing highlighted is left alone, so it submits what was typed:
 * a suggestion menu must never stand between somebody and a new item.
 */
(function () {
    'use strict';

    const DEBOUNCE_MS = 150;

    function escapeText(text) {
        const div = document.createElement('div');
        div.textContent = text == null ? '' : String(text);
        return div.innerHTML;
    }

    function attachAutocomplete(config) {
        const { input, menu, source, label, onAccept, onForget, scrollBox } = config;
        const enabled = config.enabled || (() => true);

        let suggestions = [];
        let active = -1;
        let sequence = 0;
        let timer = null;

        function close() {
            suggestions = [];
            active = -1;
            menu.classList.remove('open');
            menu.innerHTML = '';
            input.setAttribute('aria-expanded', 'false');
        }

        function render() {
            if (suggestions.length === 0) {
                close();
                return;
            }
            menu.innerHTML = suggestions.map((suggestion, index) => {
                const { name, detail } = label(suggestion);
                return `
                    <div class="autocomplete-option ${index === active ? 'active' : ''}"
                         role="option" aria-selected="${index === active}" data-index="${index}">
                        <span class="autocomplete-name">${escapeText(name)}</span>
                        ${detail ? `<span class="autocomplete-detail">${escapeText(detail)}</span>` : ''}
                        ${onForget ? `<button type="button" class="autocomplete-dismiss" data-dismiss="${index}"
                                aria-label="Forget ${escapeText(name).replace(/"/g, '&quot;')}">×</button>` : ''}
                    </div>
                `;
            }).join('');
            menu.classList.add('open');
            input.setAttribute('aria-expanded', 'true');
            revealMenu();
        }

        /* A modal body is a scroll box, so the menu cannot spill outside it:
         * whatever part of it is below the box's bottom edge is out of sight.
         * Scroll just far enough to bring the whole (height-capped) menu into
         * view, and never so far that the field being typed in goes above the
         * top. When the menu already fits, nothing moves.
         *
         * This replaced scrolling to the top on every keystroke, which suited
         * a field at the top of its form and threw any field lower down (a
         * packing list item's Bag) to the bottom edge, its menu out of sight. */
        function revealMenu() {
            if (!scrollBox) return;
            const box = scrollBox.getBoundingClientRect();
            const hidden = menu.getBoundingClientRect().bottom - box.bottom;
            if (hidden <= 0) return;
            const room = input.getBoundingClientRect().top - box.top;
            scrollBox.scrollTop += Math.min(hidden, Math.max(0, room));
        }

        async function lookup() {
            const query = input.value.trim();
            if (!query) {
                close();
                return;
            }
            const mine = ++sequence;
            try {
                const found = await source(query);
                if (mine !== sequence) return; // a newer keystroke already answered
                suggestions = found || [];
                active = -1;
                render();
            } catch (error) {
                console.error('Error fetching suggestions:', error);
            }
        }

        function highlight(index) {
            active = index;
            menu.querySelectorAll('.autocomplete-option').forEach((el, i) => {
                el.classList.toggle('active', i === index);
                el.setAttribute('aria-selected', String(i === index));
            });
            const current = menu.querySelector('.autocomplete-option.active');
            if (current) current.scrollIntoView({ block: 'nearest' });
        }

        function accept(index) {
            const suggestion = suggestions[index];
            if (!suggestion) return;
            input.value = label(suggestion).name;
            if (onAccept) onAccept(suggestion);
            close();
            input.focus();
        }

        async function forget(index) {
            const suggestion = suggestions[index];
            if (!suggestion) return;
            try {
                await onForget(suggestion);
                suggestions = suggestions.filter(s => s !== suggestion);
                active = -1;
                render();
            } catch (error) {
                console.error('Error removing suggestion:', error);
            }
        }

        input.addEventListener('input', () => {
            if (!enabled()) return;
            clearTimeout(timer);
            timer = setTimeout(lookup, DEBOUNCE_MS);
        });

        input.addEventListener('keydown', (e) => {
            const open = menu.classList.contains('open');
            if (e.key === 'ArrowDown' && open) {
                e.preventDefault();
                highlight((active + 1) % suggestions.length);
            } else if (e.key === 'ArrowUp' && open) {
                e.preventDefault();
                highlight((active - 1 + suggestions.length) % suggestions.length);
            } else if (e.key === 'Enter') {
                if (open && active >= 0) {
                    e.preventDefault();
                    accept(active);
                }
            } else if (e.key === 'Escape' && open) {
                // Closing the menu is all Esc does here; it should not also
                // close the modal underneath.
                e.stopPropagation();
                close();
            }
        });

        // Delayed so a click on an option lands before the menu closes.
        input.addEventListener('blur', () => setTimeout(close, DEBOUNCE_MS));
        menu.addEventListener('mousedown', (e) => e.preventDefault());
        menu.addEventListener('click', (e) => {
            const dismiss = e.target.closest('.autocomplete-dismiss');
            if (dismiss) {
                e.preventDefault();
                e.stopPropagation();
                forget(parseInt(dismiss.dataset.dismiss, 10));
                return;
            }
            const option = e.target.closest('.autocomplete-option');
            if (option) accept(parseInt(option.dataset.index, 10));
        });

        return { close };
    }

    window.attachAutocomplete = attachAutocomplete;
})();
