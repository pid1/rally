/* The modal chassis, shared by every modal in Rally.
 *
 * Markup contract:
 *
 *   .modal-overlay > .modal-content > h3 + .modal-scroll > .modal-body
 *
 * .modal-body is the scroll box; .modal-scroll paints a fade over whichever
 * edge is currently hiding content. Modals used to be split between those that
 * had this wrapper and those that fell back to scrolling .modal-content, so
 * half of them offered no signal that the form continued below the fold — most
 * visibly on a phone, where a modal fills ~90% of the viewport.
 *
 * Loaded on every page that owns a modal; the wiring below is idempotent and
 * runs against whatever is in the DOM at load.
 */
(function () {
  "use strict";

  function updateModalFade(scroll) {
    const body = scroll.querySelector(".modal-body");
    if (!body) return;
    const maxScroll = body.scrollHeight - body.clientHeight;
    // A 1px tolerance: sub-pixel layout otherwise leaves the bottom fade on
    // permanently for content that actually fits.
    scroll.toggleAttribute("data-overflow-top", body.scrollTop > 1);
    scroll.toggleAttribute(
      "data-overflow-bottom",
      body.scrollTop < maxScroll - 1,
    );
  }

  function wire(scroll) {
    if (scroll.dataset.fadeWired) return;
    scroll.dataset.fadeWired = "true";
    const body = scroll.querySelector(".modal-body");
    if (!body) return;
    const refresh = () => updateModalFade(scroll);
    body.addEventListener("scroll", refresh);
    // The fade must also settle when the content changes height — a
    // conditional field appearing (custom recurrence), or a list
    // re-rendering. Field handlers run before this bubbles.
    body.addEventListener("change", refresh);
    if (typeof ResizeObserver !== "undefined") {
      new ResizeObserver(refresh).observe(body);
    }
  }

  function wireAll() {
    document.querySelectorAll(".modal-scroll").forEach(wire);
  }

  /* Show a modal at its top, and compute its fade state once it has been
   * laid out.
   *
   * The scroll reset is the point of opening through here. A hidden modal
   * keeps the scrollTop it was left at, so scrolling to the bottom of Add
   * Item, cancelling, and opening it again put you back at the bottom — with
   * the first field's label above the fold, which reads as a form that
   * starts mid-way through itself. Every modal opens at the top now.
   */
  function showModalOverlay(overlayId) {
    const overlay = document.getElementById(overlayId);
    if (!overlay) return;
    overlay.style.display = "flex";
    const scroll = overlay.querySelector(".modal-scroll");
    if (scroll) {
      wire(scroll);
      const body = scroll.querySelector(".modal-body");
      if (body) body.scrollTop = 0;
      requestAnimationFrame(() => updateModalFade(scroll));
    }
  }

  function hideModalOverlay(overlayId) {
    const overlay = document.getElementById(overlayId);
    if (overlay) overlay.style.display = "none";
  }

  window.showModalOverlay = showModalOverlay;
  window.hideModalOverlay = hideModalOverlay;
  window.updateModalFade = updateModalFade;

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", wireAll);
  } else {
    wireAll();
  }
})();
