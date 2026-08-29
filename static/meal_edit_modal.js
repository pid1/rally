// Shared meal add/edit modal used by both meal pages:
//   - Meal Planner (Current & Upcoming) — add + edit upcoming meals
//   - Meal History (Previous Meals)      — edit past meals
//
// Both templates include _meal_edit_modal.html (same element IDs); each page
// constructs a MealEditModal with page-specific hooks. Keeping the markup and
// behavior in one place means the edit experience has a single source of truth.
//
// Ratings/reviews live only on past meals (Meal History). When an edit moves a
// meal's date onto the planner (today or later), any existing rating/review is
// discarded: this class warns and confirms first, and the server clears them.
(function (global) {
	"use strict";

	const MEAL_TYPES = ["Breakfast", "Lunch", "Dinner", "Snacks"];

	function escapeHtml(text) {
		const div = document.createElement("div");
		div.textContent = text;
		return div.innerHTML;
	}

	// Today's date (YYYY-MM-DD) in a given IANA timezone. Mirrors the backend's
	// today_local() so the planner/history day boundary agrees with the server.
	function todayLocal(tzName) {
		return new Intl.DateTimeFormat("en-CA", {
			timeZone: tzName || "UTC",
		}).format(new Date());
	}

	class MealEditModal {
		// opts:
		//   getFamilyMembers  () => [{id, name}]   (required)
		//   getDefaultMealType() => 'Dinner'        (optional)
		//   getTimezone       () => 'UTC'           (optional; for the discard check)
		//   onSaved           () => Promise|void    (called after save/delete)
		constructor(opts) {
			this.getFamilyMembers = opts.getFamilyMembers;
			this.getDefaultMealType = opts.getDefaultMealType || (() => "Dinner");
			this.getTimezone = opts.getTimezone || (() => "UTC");
			this.onSaved = opts.onSaved || (() => {});

			this.editingId = null;
			this.editingPlan = null; // full plan object when editing (for rating checks)

			this.overlay = document.getElementById("modal-overlay");
			this.form = document.getElementById("plan-form");
			this._bindEvents();
		}

		_bindEvents() {
			this.form.addEventListener("submit", (e) => {
				e.preventDefault();
				this._submit();
			});
			document
				.getElementById("btn-cancel")
				.addEventListener("click", () => this.close());
			document
				.getElementById("btn-delete-plan")
				.addEventListener("click", () => this._confirmDelete());
			this.overlay.addEventListener("click", (e) => {
				if (e.target === this.overlay) this.close();
			});
		}

		// --- Form population --------------------------------------------------

		populateFamilyControls() {
			const members = this.getFamilyMembers();

			const checkboxGroup = document.getElementById("attendee-checkboxes");
			checkboxGroup.innerHTML = members
				.map(
					(m) =>
						`<label class="checkbox-label"><input type="checkbox" class="attendee-cb" value="${m.id}"> ${escapeHtml(m.name)}</label>`,
				)
				.join("");

			const cookSelect = document.getElementById("plan-cook");
			cookSelect.innerHTML = '<option value="">— nobody assigned —</option>';
			members.forEach((m) => {
				cookSelect.innerHTML += `<option value="${m.id}">${escapeHtml(m.name)}</option>`;
			});
		}

		populateMealTypeSelector() {
			const select = document.getElementById("plan-meal-type");
			const def = this.getDefaultMealType();
			select.innerHTML = MEAL_TYPES.map((t) => {
				const label = t === def ? `${t} (default)` : t;
				return `<option value="${t}">${label}</option>`;
			}).join("");
		}

		_getSelectedAttendeeIds() {
			const checked = document.querySelectorAll(".attendee-cb:checked");
			const ids = Array.from(checked).map((cb) => parseInt(cb.value));
			return ids.length > 0 ? ids : null; // null = everyone
		}

		_getSelectedCookId() {
			const val = document.getElementById("plan-cook").value;
			return val ? parseInt(val) : null;
		}

		// --- Open / close -----------------------------------------------------

		openAdd(defaults = {}) {
			this.editingId = null;
			this.editingPlan = null;
			document.getElementById("modal-title").textContent = "Add Meal Plan";
			this.form.reset();
			document.getElementById("plan-edit-id").value = "";
			this.populateFamilyControls();
			this.populateMealTypeSelector();
			document.getElementById("plan-date").value =
				defaults.date || todayLocal(this.getTimezone());
			document.getElementById("plan-meal-type").value =
				defaults.meal_type || this.getDefaultMealType();
			document
				.querySelectorAll(".attendee-cb")
				.forEach((cb) => (cb.checked = false));
			document.getElementById("plan-cook").value = "";
			document.getElementById("btn-delete-plan").style.display = "none";
			showModalOverlay("modal-overlay");
		}

		openEdit(plan) {
			if (!plan) return;
			this.editingId = plan.id;
			this.editingPlan = plan;
			document.getElementById("modal-title").textContent = "Edit Meal Plan";
			this.populateFamilyControls();
			this.populateMealTypeSelector();
			document.getElementById("plan-edit-id").value = plan.id;
			document.getElementById("plan-date").value = plan.date;
			document.getElementById("plan-meal-type").value =
				plan.meal_type || "Dinner";
			document.getElementById("plan-text").value = plan.plan;
			document.querySelectorAll(".attendee-cb").forEach((cb) => {
				cb.checked =
					plan.attendee_ids && plan.attendee_ids.includes(parseInt(cb.value));
			});
			document.getElementById("plan-cook").value = plan.cook_id || "";
			document.getElementById("btn-delete-plan").style.display = "";
			showModalOverlay("modal-overlay");
		}

		close() {
			this.overlay.style.display = "none";
			this.editingId = null;
			this.editingPlan = null;
		}

		// --- Save / delete ----------------------------------------------------

		async _submit() {
			const date = document.getElementById("plan-date").value;
			const meal_type = document.getElementById("plan-meal-type").value;
			const plan = document.getElementById("plan-text").value;
			const attendee_ids = this._getSelectedAttendeeIds();
			const cook_id = this._getSelectedCookId();

			// Moving a rated meal onto the planner (today or later) discards its
			// rating and review. Warn and confirm before doing so.
			if (
				this.editingPlan &&
				(this.editingPlan.rating || this.editingPlan.review) &&
				date >= todayLocal(this.getTimezone())
			) {
				const ok = confirm(
					"Moving this meal to the planner will discard its rating and review. Continue?",
				);
				if (!ok) return; // keep modal open for correction
			}

			try {
				if (this.editingId) {
					await this._request(`/api/dinner-plans/${this.editingId}`, "PUT", {
						date,
						meal_type,
						plan,
						attendee_ids,
						cook_id,
					});
				} else {
					await this._request("/api/dinner-plans", "POST", {
						date,
						meal_type,
						plan,
						attendee_ids,
						cook_id,
					});
				}
				this.close();
				await this.onSaved();
			} catch (error) {
				console.error("Error saving meal plan:", error);
				alert("Failed to save meal plan");
			}
		}

		async _confirmDelete() {
			if (!this.editingId) return;
			if (!confirm("Are you sure you want to delete this meal plan?")) return;
			try {
				const resp = await fetch(`/api/dinner-plans/${this.editingId}`, {
					method: "DELETE",
				});
				if (!resp.ok) throw new Error("Failed to delete plan");
				this.close();
				await this.onSaved();
			} catch (error) {
				console.error("Error deleting meal plan:", error);
				alert("Failed to delete meal plan");
			}
		}

		async _request(url, method, body) {
			const resp = await fetch(url, {
				method,
				headers: { "Content-Type": "application/json" },
				body: JSON.stringify(body),
			});
			if (!resp.ok) throw new Error(`Request failed: ${method} ${url}`);
			return resp.json();
		}
	}

	global.MealEditModal = MealEditModal;
	global.mealTodayLocal = todayLocal;
})(window);
