// Shared meal add/edit modal used by both meal pages:
//   - Meal Planner (Current & Upcoming) — add + edit upcoming meals
//   - Previous Meals                     — edit past meals
//
// Both templates include _meal_edit_modal.html (same element IDs); each page
// constructs a MealEditModal with page-specific hooks. Keeping the markup and
// behavior in one place means the edit experience has a single source of truth.
//
// Ratings/reviews live only on past meals (Previous Meals). When an edit moves a
// meal's date onto the planner (today or later), any existing rating/review is
// discarded: this class warns and confirms first, and the server clears them.
(function (global) {
    'use strict';

    const MEAL_TYPES = ['Breakfast', 'Lunch', 'Dinner', 'Snacks'];

    function escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    // Today's date (YYYY-MM-DD) in the page's IANA timezone, by static/dates.js's
    // todayLocal(). Mirrors the backend's today_local() so the planner/history
    // day boundary agrees with the server.
    function todayIn(tzName) {
        return todayLocal(tzName || 'UTC');
    }

    // `plan-text` (Meal Name) is a single-line <input>, which silently drops
    // any line break set on it. A meal saved before the split can hold
    // multi-line text, so opening it for Edit has to flatten it first —
    // otherwise saving it back would run the lines together with nothing
    // between them. Meals that are never edited are untouched.
    function flattenToOneLine(text) {
        return (text || '').replace(/\r\n|\r|\n/g, ' ');
    }

    class MealEditModal {
        // opts:
        //   getFamilyMembers  () => [{id, name}]   (required)
        //   getDefaultMealType() => 'Dinner'        (optional)
        //   getTimezone       () => 'UTC'           (optional; for the discard check)
        //   onSaved           () => Promise|void    (called after save/delete)
        constructor(opts) {
            this.getFamilyMembers = opts.getFamilyMembers;
            this.getDefaultMealType = opts.getDefaultMealType || (() => 'Dinner');
            this.getTimezone = opts.getTimezone || (() => 'UTC');
            this.onSaved = opts.onSaved || (() => {});

            this.editingId = null;
            this.editingPlan = null; // full plan object when editing (for rating checks)

            this.overlay = document.getElementById('modal-overlay');
            this.form = document.getElementById('plan-form');
            this.addAnotherBtn = document.getElementById('btn-save-add-another');
            // A missing Meal Name is refused under it, never by the browser's
            // bubble (`requireText()` in dialogs.js). The server refuses one too.
            this.refuseBlankName = requireText('plan-text', 'plan-text-message', "A name can't be empty.");
            requireDate('plan-date', 'plan-date-message');
            this._bindEvents();
        }

        _bindEvents() {
            // Both Save and Save & Add Another are submit buttons, so the
            // browser runs the required-field check before either path starts.
            // `submitter` is what says which one was pressed.
            this.form.addEventListener('submit', (e) => {
                e.preventDefault();
                this._submit(e.submitter === this.addAnotherBtn);
            });
            document.getElementById('btn-cancel').addEventListener('click', () => this.close());
            document.getElementById('btn-delete-plan').addEventListener('click', () => this._confirmDelete());
            this.overlay.addEventListener('click', (e) => {
                if (e.target === this.overlay) this.close();
            });
        }

        // --- Form population --------------------------------------------------

        populateFamilyControls() {
            const members = this.getFamilyMembers();

            const checkboxGroup = document.getElementById('attendee-checkboxes');
            checkboxGroup.innerHTML = members.map(m =>
                `<label class="checkbox-label"><input type="checkbox" class="attendee-cb" value="${m.id}"> ${escapeHtml(m.name)}</label>`
            ).join('');

            const cookSelect = document.getElementById('plan-cook');
            cookSelect.innerHTML = '<option value="">— nobody assigned —</option>';
            members.forEach(m => {
                cookSelect.innerHTML += `<option value="${m.id}">${escapeHtml(m.name)}</option>`;
            });
        }

        populateMealTypeSelector() {
            const select = document.getElementById('plan-meal-type');
            const def = this.getDefaultMealType();
            select.innerHTML = MEAL_TYPES.map(t => {
                const label = t === def ? `${t} (default)` : t;
                return `<option value="${t}">${label}</option>`;
            }).join('');
        }

        _getSelectedAttendeeIds() {
            const checked = document.querySelectorAll('.attendee-cb:checked');
            const ids = Array.from(checked).map(cb => parseInt(cb.value));
            return ids.length > 0 ? ids : null; // null = everyone
        }

        _getSelectedCookId() {
            const val = document.getElementById('plan-cook').value;
            return val ? parseInt(val) : null;
        }

        // --- Open / close -----------------------------------------------------

        openAdd(defaults = {}) {
            this.editingId = null;
            this.editingPlan = null;
            document.getElementById('modal-title').textContent = 'Add Meal Plan';
            this.form.reset();
            clearFormMessages('modal-overlay');
            document.getElementById('plan-edit-id').value = '';
            this.populateFamilyControls();
            this.populateMealTypeSelector();
            document.getElementById('plan-date').value = defaults.date || todayIn(this.getTimezone());
            document.getElementById('plan-meal-type').value = defaults.meal_type || this.getDefaultMealType();
            document.querySelectorAll('.attendee-cb').forEach(cb => cb.checked = false);
            document.getElementById('plan-cook').value = '';
            document.getElementById('btn-delete-plan').style.display = 'none';
            this.addAnotherBtn.style.display = '';
            showModalOverlay('modal-overlay');
        }

        openEdit(plan) {
            if (!plan) return;
            this.editingId = plan.id;
            this.editingPlan = plan;
            document.getElementById('modal-title').textContent = 'Edit Meal Plan';
            clearFormMessages('modal-overlay');
            this.populateFamilyControls();
            this.populateMealTypeSelector();
            document.getElementById('plan-edit-id').value = plan.id;
            document.getElementById('plan-date').value = plan.date;
            document.getElementById('plan-meal-type').value = plan.meal_type || 'Dinner';
            document.getElementById('plan-text').value = flattenToOneLine(plan.plan);
            document.getElementById('plan-notes').value = plan.notes || '';
            document.querySelectorAll('.attendee-cb').forEach(cb => {
                cb.checked = plan.attendee_ids && plan.attendee_ids.includes(parseInt(cb.value));
            });
            document.getElementById('plan-cook').value = plan.cook_id || '';
            document.getElementById('btn-delete-plan').style.display = '';
            this.addAnotherBtn.style.display = 'none';
            showModalOverlay('modal-overlay');
        }

        close() {
            // Through the chassis, so the page behind is unlocked as well.
            hideModalOverlay(this.overlay.id);
            this.editingId = null;
            this.editingPlan = null;
        }

        // --- Save / delete ----------------------------------------------------

        // `addAnother` keeps the modal open for the next entry instead of
        // closing it. Meals are planned in batches, and everything but the menu
        // text is usually the same from one to the next.
        async _submit(addAnother = false) {
            if (this.refuseBlankName()) return;
            const date = document.getElementById('plan-date').value;
            const meal_type = document.getElementById('plan-meal-type').value;
            const plan = document.getElementById('plan-text').value;
            const notes = document.getElementById('plan-notes').value;
            const attendee_ids = this._getSelectedAttendeeIds();
            const cook_id = this._getSelectedCookId();

            // Moving a rated meal onto the planner (today or later) discards its
            // rating and review. Warn and confirm before doing so, over the
            // modal, naming the meal as it was saved.
            if (this.editingPlan &&
                (this.editingPlan.rating || this.editingPlan.review) &&
                date >= todayIn(this.getTimezone())) {
                const ok = await confirmModal({
                    title: 'Move to planner',
                    messageHtml: `Moving <strong>${escapeHtml(this.editingPlan.plan)}</strong> to the planner will discard its rating and review. Continue?`,
                    confirmLabel: 'Move',
                });
                if (!ok) return; // keep modal open for correction
            }

            try {
                if (this.editingId) {
                    await this._request(`/api/meal-planner/${this.editingId}`, 'PUT',
                        { date, meal_type, plan, notes, attendee_ids, cook_id });
                } else {
                    await this._request('/api/meal-planner', 'POST',
                        { date, meal_type, plan, notes, attendee_ids, cook_id });
                }
                if (addAnother) {
                    this._resetForNextEntry();
                } else {
                    this.close();
                }
                await this.onSaved();
            } catch (error) {
                console.error('Error saving meal plan:', error);
                alertModal({ messageHtml: 'Failed to save meal plan' });
            }
        }

        // Clear the meal name and notes, and nothing else: the date, meal
        // type, attendees and cook just entered are almost always right for
        // the next meal too, and re-entering them is the cost this button
        // exists to remove.
        //
        // Only ever called after a save has succeeded. A failed save leaves the
        // text in place so it can be corrected and retried.
        _resetForNextEntry() {
            const text = document.getElementById('plan-text');
            text.value = '';
            document.getElementById('plan-notes').value = '';
            text.focus();
        }

        async _confirmDelete() {
            if (!this.editingId) return;
            const name = this.editingPlan ? this.editingPlan.plan : '';
            const confirmed = await confirmModal({
                title: 'Delete meal',
                messageHtml: `Delete <strong>${escapeHtml(name)}</strong>?`,
                confirmLabel: 'Delete',
            });
            if (!confirmed) return;
            try {
                const resp = await fetch(`/api/meal-planner/${this.editingId}`, { method: 'DELETE' });
                if (!resp.ok) throw new Error('Failed to delete plan');
                this.close();
                await this.onSaved();
            } catch (error) {
                console.error('Error deleting meal plan:', error);
                alertModal({ messageHtml: 'Failed to delete meal plan' });
            }
        }

        async _request(url, method, body) {
            const resp = await fetch(url, {
                method,
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body),
            });
            if (!resp.ok) throw new Error(`Request failed: ${method} ${url}`);
            return resp.json();
        }
    }

    global.MealEditModal = MealEditModal;
})(window);
