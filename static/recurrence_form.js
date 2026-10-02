/* The repeat controls in templates/_recurrence_fields.html: Daily, Weekly,
 * Monthly and Custom, shared by every "Add Recurring" modal.
 *
 * The form compiles the controls into the rule shape `rally.recurrence`
 * reads ({recurrence_type, recurrence_day, custom_rule}) and reads one back
 * into the controls. It never works out a date itself: the read-back line asks
 * `POST /api/recurring-todos/preview` what the rule produces and renders the
 * answer, so the server stays the only place that knows what "every 12
 * months on the first Sunday" means.
 *
 * A page calls `RecurrenceForm.init({...})` once, then `reset()` or `load(rule)`
 * each time its modal opens. The task-only control ("calculate next due date
 * from") is optional: a page that does not render it never sees it in the
 * rule.
 *
 * Daily can skip weekends. That travels as `custom_rule: {weekdays_only: true}`
 * beside `recurrence_type: 'daily'`, the one place a built-in rule carries a
 * custom_rule.
 */
(function () {
    'use strict';

    const DAY_NAMES = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'];
    const WEEKDAYS_LONG = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];
    const MONTHS_LONG = ['January', 'February', 'March', 'April', 'May', 'June', 'July',
        'August', 'September', 'October', 'November', 'December'];

    const $ = id => document.getElementById(id);

    let options = {
        startInputId: null,
        endInputId: null,
        leadLabel: () => 'First',
        showAnchor: () => false,
    };
    let previewSequence = 0;
    let previewTimer = null;

    function ordinal(n) {
        const s = ['th', 'st', 'nd', 'rd'];
        const v = n % 100;
        return n + (s[(v - 20) % 10] || s[v] || s[0]);
    }

    function formatLongDate(value, withWeekday) {
        const date = new Date(value + 'T00:00:00');
        const long = `${MONTHS_LONG[date.getMonth()]} ${date.getDate()}, ${date.getFullYear()}`;
        return withWeekday ? `${WEEKDAYS_LONG[date.getDay()]}, ${long}` : long;
    }

    function describeCustomRule(rule) {
        if (!rule) return 'Custom';
        const { freq, interval } = rule;
        const n = interval || 1;
        const every = n === 1 ? 'Every' : `Every ${n}`;
        if (freq === 'daily') {
            const base = n === 1 ? 'Daily' : `Every ${n} days`;
            const notes = [];
            if (rule.weekdays_only) notes.push('weekdays only');
            if (rule.next_due_from === 'completion_date') notes.push('from completion date');
            return notes.length ? `${base} (${notes.join(', ')})` : base;
        }
        if (freq === 'weekly') {
            const span = weekSpan(rule.weekdays || [0]);
            if (span && n === 1) return `Every ${span}`;
            if (span) return `Every ${n} weeks on ${span}s`;
            const days = (rule.weekdays || [0]).map(d => DAY_NAMES[d].slice(0, 3)).join(', ');
            return `${every} week${n !== 1 ? 's' : ''} on ${days}`;
        }
        if (freq === 'monthly') {
            const mo = `${every} month${n !== 1 ? 's' : ''}`;
            if (rule.mode === 'day') return `${mo} on the ${ordinal(rule.day)}`;
            if (rule.mode === 'weekday') return `${mo} on the ${rule.ordinal} ${DAY_NAMES[rule.weekday]}`;
        }
        return 'Custom';
    }

    /* "weekday" for exactly Monday to Friday, "weekend day" for exactly
     * Saturday and Sunday, otherwise null and the days are listed. */
    function weekSpan(weekdays) {
        const set = [...new Set(weekdays)].sort((a, b) => a - b).join(',');
        if (set === '0,1,2,3,4') return 'weekday';
        if (set === '5,6') return 'weekend day';
        return null;
    }

    /* "Weekly on Monday", "Every weekday", "Every 2 weeks on Mon, Wed" — a
     * row's cadence. */
    function describe(rule) {
        if (rule.recurrence_type === 'daily') {
            return rule.custom_rule && rule.custom_rule.weekdays_only ? 'Every weekday' : 'Daily';
        }
        if (rule.recurrence_type === 'weekly') return `Weekly on ${DAY_NAMES[rule.recurrence_day] || 'Monday'}`;
        if (rule.recurrence_type === 'monthly') return `Monthly on the ${ordinal(rule.recurrence_day || 1)}`;
        if (rule.recurrence_type === 'custom') return describeCustomRule(rule.custom_rule);
        return rule.recurrence_type;
    }

    // ── Showing the controls that apply ──

    function updateDaySelector() {
        const type = $('recurring-type').value;
        const dayGroup = $('recurring-day-group');
        const daySelect = $('recurring-day');
        const customGroup = $('recurring-custom-group');

        dayGroup.style.display = 'none';
        customGroup.style.display = 'none';
        $('daily-weekdays-only-group').style.display = type === 'daily' ? '' : 'none';

        if (type === 'daily') return;

        if (type === 'custom') {
            customGroup.style.display = '';
            updateCustomSubGroups();
            return;
        }

        dayGroup.style.display = '';
        if (type === 'weekly') {
            $('recurring-day-label').textContent = 'Day of Week';
            daySelect.innerHTML = DAY_NAMES.map((d, i) => `<option value="${i}">${d}</option>`).join('');
        } else if (type === 'monthly') {
            $('recurring-day-label').textContent = 'Day of Month';
            daySelect.innerHTML = Array.from({ length: 31 }, (_, i) =>
                `<option value="${i + 1}">${ordinal(i + 1)}</option>`
            ).join('');
        }
    }

    function updateCustomSubGroups() {
        const freq = $('custom-freq').value;
        const weekdaysOnly = $('custom-weekdays-only');
        if (weekdaysOnly) {
            $('custom-weekdays-only-group').style.display = freq === 'daily' ? '' : 'none';
            if (freq !== 'daily') weekdaysOnly.checked = false;
        }
        // The due-date/completion-date anchor only applies to "every N days"
        // rules whose page says it does (a task that sets a due date).
        const anchorGroup = $('custom-next-due-from-group');
        if (anchorGroup) {
            anchorGroup.style.display = freq === 'daily' && options.showAnchor() ? '' : 'none';
        }
        $('custom-weekly-group').style.display = freq === 'weekly' ? '' : 'none';
        $('custom-monthly-group').style.display = freq === 'monthly' ? '' : 'none';
    }

    function updateCustomMonthlyMode() {
        const mode = document.querySelector('input[name="custom-monthly-mode"]:checked').value;
        $('custom-monthly-day-group').style.display = mode === 'day' ? '' : 'none';
        $('custom-monthly-weekday-group').style.display = mode === 'weekday' ? '' : 'none';
    }

    // ── The rule ──

    function resetCustomFields() {
        $('custom-interval').value = '1';
        $('custom-freq').value = 'daily';
        if ($('custom-weekdays-only')) $('custom-weekdays-only').checked = false;
        const anchor = document.querySelector('input[name="custom-next-due-from"][value="due_date"]');
        if (anchor) anchor.checked = true;
        document.querySelectorAll('#custom-weekdays input[type="checkbox"]').forEach(cb => { cb.checked = false; });
        document.querySelector('input[name="custom-monthly-mode"][value="day"]').checked = true;
        $('custom-monthly-day').value = '1';
        $('custom-ordinal').value = 'first';
        $('custom-weekday').value = '0';
        updateCustomSubGroups();
        updateCustomMonthlyMode();
    }

    function loadCustomRule(rule) {
        if (!rule) return;
        $('custom-interval').value = rule.interval || 1;
        $('custom-freq').value = rule.freq || 'daily';
        updateCustomSubGroups();
        if (rule.freq === 'daily') {
            if ($('custom-weekdays-only')) $('custom-weekdays-only').checked = !!rule.weekdays_only;
            const anchor = rule.next_due_from === 'completion_date' ? 'completion_date' : 'due_date';
            const radio = document.querySelector(`input[name="custom-next-due-from"][value="${anchor}"]`);
            if (radio) radio.checked = true;
        }
        if (rule.freq === 'weekly') {
            const wds = rule.weekdays || [];
            document.querySelectorAll('#custom-weekdays input[type="checkbox"]').forEach(cb => {
                cb.checked = wds.includes(parseInt(cb.value, 10));
            });
        } else if (rule.freq === 'monthly') {
            const mode = rule.mode || 'day';
            document.querySelector(`input[name="custom-monthly-mode"][value="${mode}"]`).checked = true;
            updateCustomMonthlyMode();
            if (mode === 'day') {
                $('custom-monthly-day').value = rule.day || 1;
            } else {
                $('custom-ordinal').value = rule.ordinal || 'first';
                $('custom-weekday').value = rule.weekday != null ? rule.weekday : 0;
            }
        }
    }

    function buildCustomRule() {
        const freq = $('custom-freq').value;
        const interval = parseInt($('custom-interval').value, 10) || 1;
        const rule = { freq, interval };
        if (freq === 'daily' && $('custom-weekdays-only') && $('custom-weekdays-only').checked) {
            rule.weekdays_only = true;
        }
        // Only persist a non-default anchor; absence means "due_date".
        if (freq === 'daily') {
            const anchor = document.querySelector('input[name="custom-next-due-from"]:checked');
            if (anchor && anchor.value === 'completion_date') {
                rule.next_due_from = 'completion_date';
            }
        }
        if (freq === 'weekly') {
            rule.weekdays = [...document.querySelectorAll('#custom-weekdays input[type="checkbox"]:checked')]
                .map(cb => parseInt(cb.value, 10));
            if (rule.weekdays.length === 0) rule.weekdays = [0]; // default Monday
        } else if (freq === 'monthly') {
            rule.mode = document.querySelector('input[name="custom-monthly-mode"]:checked').value;
            if (rule.mode === 'day') {
                rule.day = parseInt($('custom-monthly-day').value, 10);
            } else {
                rule.ordinal = $('custom-ordinal').value;
                rule.weekday = parseInt($('custom-weekday').value, 10);
            }
        }
        return rule;
    }

    /* The controls as the rule the API stores. */
    function values() {
        const type = $('recurring-type').value;
        let customRule = null;
        if (type === 'custom') customRule = buildCustomRule();
        else if (type === 'daily' && $('daily-weekdays-only').checked) customRule = { weekdays_only: true };
        return {
            recurrence_type: type,
            recurrence_day: type === 'weekly' || type === 'monthly' ? parseInt($('recurring-day').value, 10) : null,
            custom_rule: customRule,
        };
    }

    /* Back to Daily with every custom control at its default. */
    function reset() {
        $('recurring-type').value = 'daily';
        $('daily-weekdays-only').checked = false;
        resetCustomFields();
        updateDaySelector();
    }

    /* Put a stored rule back into the controls. */
    function load(rule) {
        $('recurring-type').value = rule.recurrence_type;
        $('daily-weekdays-only').checked =
            rule.recurrence_type === 'daily' && !!(rule.custom_rule && rule.custom_rule.weekdays_only);
        resetCustomFields();
        updateDaySelector();
        if (rule.recurrence_type === 'custom') {
            loadCustomRule(rule.custom_rule);
        } else if (rule.recurrence_day != null) {
            $('recurring-day').value = rule.recurrence_day;
        }
    }

    // ── The read-back line ──
    //
    // Debounced with a sequence guard: typing in the interval box would
    // otherwise fire a request per keystroke and answer out of order.

    function schedulePreview() {
        clearTimeout(previewTimer);
        previewTimer = setTimeout(refreshPreview, 150);
    }

    async function refreshPreview() {
        const box = $('recurrence-preview');
        if (!box) return;
        const start = options.startInputId ? $(options.startInputId).value : '';
        const end = options.endInputId ? $(options.endInputId).value : '';
        const payload = { ...values(), start_date: start || null };
        const sequence = ++previewSequence;

        try {
            const response = await fetch('/api/recurring-todos/preview', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            });
            if (!response.ok) throw new Error('preview failed');
            const { occurrences } = await response.json();
            if (sequence !== previewSequence) return; // A later edit already answered
            // An end date is a bound the preview endpoint does not know about,
            // and comparing YYYY-MM-DD strings is comparing dates.
            const shown = end ? occurrences.filter(o => o <= end) : occurrences;
            if (shown.length === 0) {
                const from = start ? 'the start date' : 'today';
                box.innerHTML = `<div class="recurrence-preview-lead">Nothing falls between ${from} and the end date.</div>`;
                return;
            }
            const rest = shown.slice(1).map(o => formatLongDate(o, false)).join(', ');
            box.innerHTML = `
                <div class="recurrence-preview-lead">${options.leadLabel()}: ${formatLongDate(shown[0], true)}</div>
                ${rest ? `<div class="recurrence-preview-rest">then ${rest}</div>` : ''}
            `;
        } catch (error) {
            // Say nothing rather than something wrong.
            if (sequence === previewSequence) box.innerHTML = '';
        }
    }

    function init(config) {
        options = { ...options, ...config };

        $('custom-monthly-day').innerHTML = Array.from({ length: 31 }, (_, i) =>
            `<option value="${i + 1}">${ordinal(i + 1)}</option>`
        ).join('');

        $('recurring-type').addEventListener('change', updateDaySelector);
        $('custom-freq').addEventListener('change', updateCustomSubGroups);
        document.querySelectorAll('input[name="custom-monthly-mode"]').forEach(r =>
            r.addEventListener('change', updateCustomMonthlyMode)
        );

        // Anything that changes the rule changes the dates it produces. The
        // custom group is listened to as a whole: every control inside it
        // feeds the rule.
        ['recurring-type', 'recurring-day', 'daily-weekdays-only', 'recurring-custom-group', options.startInputId, options.endInputId]
            .filter(Boolean)
            .forEach(id => {
                $(id).addEventListener('change', schedulePreview);
                $(id).addEventListener('input', schedulePreview);
            });
    }

    window.RecurrenceForm = {
        init,
        reset,
        load,
        values,
        describe,
        ordinal,
        updateCustomSubGroups,
        refreshPreview,
    };
})();
