/* Drag-to-reorder for grouped lists.
 *
 * Markup contract, matching what /shopping renders:
 *
 *   [container] > .shopping-group > .list-container > .editable-item[data-id]
 *
 * and a handle inside each row that participates. Dropping a row on another
 * group's list moves it there, so "reorder" and "change group" are one gesture.
 *
 * Why pointer events rather than HTML5 drag-and-drop: `dragstart` never fires
 * from a touch, and Rally is used on a phone and a wall tablet as much as on a
 * desktop. Pointer events are one code path for mouse, touch and pen, and they
 * give us the drop indicator and the edge auto-scroll that native DnD leaves to
 * the browser to do badly.
 *
 * The dragged row is not cloned into a placeholder — the original element is
 * moved through the DOM as you drag, dimmed in place, while a copy follows the
 * pointer. That means the live DOM is always exactly what you would get if you
 * let go, so committing is a matter of reading the destination list back.
 *
 * The handle is a real <button>, so the same move is available from the
 * keyboard: focus it and press the arrow keys. Moving between groups by
 * keyboard is the Edit form's Store field, which already does it.
 */
(function () {
  "use strict";

  // Far enough that a tap on the handle is never mistaken for a drag, close
  // enough that a deliberate drag feels immediate.
  const DRAG_THRESHOLD_PX = 5;

  // Auto-scroll while dragging near a viewport edge: without it a list longer
  // than the screen can only be rearranged within one screenful, which on a
  // phone is about four rows.
  const EDGE_ZONE_PX = 72;
  const EDGE_SPEED_MAX_PX = 18;

  function initDragReorder(config) {
    const container = config.container;
    if (!container) return null;

    const {
      groupSelector,
      listSelector,
      itemSelector,
      handleSelector,
      canDrag,
      groupKey,
      groupLabel,
      itemLabel,
      onReorder,
      announce,
    } = config;

    let drag = null;
    // Set only by the keyboard path: a commit re-renders the list, which
    // destroys the button the user is standing on. Nothing to restore after
    // a pointer drag, where focus was never the thing being moved.
    let refocusId = null;
    // Held across a keyboard commit. Key repeat fires far faster than the
    // round trip, and two overlapping commits each read the DOM at their own
    // moment — the second would save an order computed before the first
    // landed. A dropped repeat costs one keypress; a scrambled list costs
    // the arrangement.
    let committing = false;

    // --- reading the DOM -------------------------------------------------

    function draggableRows(list) {
      return Array.from(list.querySelectorAll(itemSelector)).filter(canDrag);
    }

    function orderOf(list) {
      return draggableRows(list).map((row) => Number(row.dataset.id));
    }

    function listOf(element) {
      const group = element.closest(groupSelector);
      return group && container.contains(group)
        ? group.querySelector(listSelector)
        : null;
    }

    /* The first child a dragged row must stay above: the completed block
     * that trails every group, or the "nothing here" notice of an empty one.
     * Both are non-draggable, which is the only property worth testing. */
    function firstTrailingChild(list, moving) {
      return (
        Array.from(list.children).find(
          (child) =>
            child !== moving &&
            !(child.matches(itemSelector) && canDrag(child)),
        ) || null
      );
    }

    /* An empty group renders a notice; once a row is hovering over it, that
     * notice is a lie. Hidden rather than removed so a cancelled drag can
     * put it straight back. */
    function syncNotices(list) {
      const populated = !!list.querySelector(itemSelector);
      Array.from(list.children).forEach((child) => {
        if (child.matches(itemSelector)) return;
        if (populated) {
          child.hidden = true;
          child.dataset.dragHidden = "true";
        } else if (child.dataset.dragHidden) {
          child.hidden = false;
          delete child.dataset.dragHidden;
        }
      });
    }

    function restoreNotices() {
      container.querySelectorAll("[data-drag-hidden]").forEach((el) => {
        el.hidden = false;
        delete el.dataset.dragHidden;
      });
    }

    // --- the drag itself -------------------------------------------------

    function beginDrag(event) {
      const item = drag.item;
      const rect = item.getBoundingClientRect();

      drag.offsetX = drag.startX - rect.left;
      drag.offsetY = drag.startY - rect.top;
      drag.originList = item.parentElement;
      drag.originNext = item.nextSibling;
      drag.originKey = groupKey(item.closest(groupSelector));
      drag.originOrder = orderOf(drag.originList);

      const ghost = item.cloneNode(true);
      ghost.classList.add("drag-ghost");
      ghost.removeAttribute("data-id");
      ghost.setAttribute("aria-hidden", "true");
      ghost.style.width = rect.width + "px";
      ghost.style.height = rect.height + "px";
      document.body.appendChild(ghost);
      drag.ghost = ghost;

      item.classList.add("drag-source");
      drag.handle.setAttribute("aria-pressed", "true");
      document.body.classList.add("is-dragging");
      drag.started = true;
      startEdgeScroll();
    }

    function moveGhost() {
      const x = drag.pointerX - drag.offsetX;
      const y = drag.pointerY - drag.offsetY;
      drag.ghost.style.transform = "translate3d(" + x + "px, " + y + "px, 0)";
    }

    /* Decide where the row would land if you let go now, and put it there.
     *
     * `elementFromPoint` is the right question to ask because it respects
     * whatever is actually on screen after the page has scrolled. The ghost
     * sets `pointer-events: none` precisely so it never answers it. */
    function updateDropTarget() {
      const under = document.elementFromPoint(drag.pointerX, drag.pointerY);
      if (!under) return;
      const list = listOf(under);
      if (!list) return;

      let before = null;
      for (const sibling of draggableRows(list)) {
        if (sibling === drag.item) continue;
        const rect = sibling.getBoundingClientRect();
        if (drag.pointerY < rect.top + rect.height / 2) {
          before = sibling;
          break;
        }
      }
      if (!before) before = firstTrailingChild(list, drag.item);

      if (
        drag.item.parentElement !== list ||
        drag.item.nextSibling !== before
      ) {
        const vacated = drag.item.parentElement;
        list.insertBefore(drag.item, before);
        if (vacated !== list) syncNotices(vacated);
        syncNotices(list);
      }
    }

    function startEdgeScroll() {
      const step = () => {
        if (!drag || !drag.started) return;
        const height = window.innerHeight;
        let delta = 0;
        if (drag.pointerY < EDGE_ZONE_PX) {
          delta = -EDGE_SPEED_MAX_PX * (1 - drag.pointerY / EDGE_ZONE_PX);
        } else if (drag.pointerY > height - EDGE_ZONE_PX) {
          delta =
            EDGE_SPEED_MAX_PX *
            ((drag.pointerY - height + EDGE_ZONE_PX) / EDGE_ZONE_PX);
        }
        if (delta) {
          window.scrollBy(0, Math.round(delta));
          // The page moved under a stationary pointer, so what sits
          // beneath it has changed even though nothing was moved.
          updateDropTarget();
        }
        drag.scrollFrame = requestAnimationFrame(step);
      };
      drag.scrollFrame = requestAnimationFrame(step);
    }

    function teardown(state) {
      if (state.scrollFrame) cancelAnimationFrame(state.scrollFrame);
      if (state.ghost) state.ghost.remove();
      state.item.classList.remove("drag-source");
      state.handle.removeAttribute("aria-pressed");
      document.body.classList.remove("is-dragging");
      restoreNotices();
      try {
        state.handle.releasePointerCapture(state.pointerId);
      } catch (error) {
        /* Already released — the pointer left the document, or the row
         * was re-rendered out from under the capture. Nothing to undo. */
      }
    }

    function cancelDrag() {
      if (!drag) return;
      const state = drag;
      drag = null;
      if (state.started)
        state.originList.insertBefore(state.item, state.originNext);
      teardown(state);
    }

    function sameOrder(a, b) {
      return a.length === b.length && a.every((id, index) => id === b[index]);
    }

    async function commit(list, movedItem, originKey, originOrder) {
      const group = list.closest(groupSelector);
      const key = groupKey(group);
      const ids = orderOf(list);
      if (key === originKey && sameOrder(ids, originOrder)) return false;

      await onReorder(key, ids);
      if (announce) {
        const position = ids.indexOf(Number(movedItem.dataset.id)) + 1;
        announce(
          itemLabel(movedItem) +
            ", " +
            position +
            " of " +
            ids.length +
            " in " +
            groupLabel(group),
        );
      }
      return true;
    }

    // --- pointer wiring --------------------------------------------------
    //
    // Delegated from the container, which survives every re-render; the rows
    // inside it do not.

    container.addEventListener("pointerdown", (event) => {
      if (drag) return;
      if (event.button > 0) return; // right/middle click is not a drag
      const handle = event.target.closest(handleSelector);
      if (!handle || !container.contains(handle)) return;
      const item = handle.closest(itemSelector);
      if (!item || !canDrag(item)) return;

      drag = {
        handle: handle,
        item: item,
        pointerId: event.pointerId,
        startX: event.clientX,
        startY: event.clientY,
        pointerX: event.clientX,
        pointerY: event.clientY,
        started: false,
      };
      // Capture keeps the moves coming even when the pointer outruns the
      // handle, which it does immediately.
      handle.setPointerCapture(event.pointerId);
    });

    document.addEventListener(
      "pointermove",
      (event) => {
        if (!drag || event.pointerId !== drag.pointerId) return;
        if (!drag.started) {
          const dx = event.clientX - drag.startX;
          const dy = event.clientY - drag.startY;
          if (Math.hypot(dx, dy) < DRAG_THRESHOLD_PX) return;
          beginDrag(event);
        }
        // Suppresses the text selection a mouse drag would otherwise
        // paint across the list.
        event.preventDefault();
        drag.pointerX = event.clientX;
        drag.pointerY = event.clientY;
        moveGhost();
        updateDropTarget();
      },
      { passive: false },
    );

    document.addEventListener("pointerup", async (event) => {
      if (!drag || event.pointerId !== drag.pointerId) return;
      const state = drag;
      drag = null;
      if (!state.started) {
        // A tap on the handle. Nothing moved, nothing to save.
        teardown(state);
        return;
      }
      const list = state.item.parentElement;
      teardown(state);
      await commit(list, state.item, state.originKey, state.originOrder);
    });

    document.addEventListener("pointercancel", cancelDrag);

    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && drag) cancelDrag();
    });

    // --- keyboard wiring -------------------------------------------------

    container.addEventListener("keydown", async (event) => {
      if (event.key !== "ArrowUp" && event.key !== "ArrowDown") return;
      if (committing) return;
      const handle = event.target.closest(handleSelector);
      if (!handle || !container.contains(handle)) return;
      const item = handle.closest(itemSelector);
      if (!item || !canDrag(item)) return;

      const list = item.parentElement;
      const peers = draggableRows(list);
      const index = peers.indexOf(item);
      const target = index + (event.key === "ArrowUp" ? -1 : 1);
      if (target < 0 || target >= peers.length) return;

      event.preventDefault();
      const originKey = groupKey(list.closest(groupSelector));
      const originOrder = orderOf(list);
      if (event.key === "ArrowUp") {
        list.insertBefore(item, peers[target]);
      } else {
        list.insertBefore(item, peers[target].nextSibling);
      }

      refocusId = item.dataset.id;
      committing = true;
      try {
        await commit(list, item, originKey, originOrder);
      } finally {
        committing = false;
      }
      refocus();
    });

    /* The commit re-rendered the list, so the button the user was standing
     * on no longer exists. Put focus on its replacement, or arrowing twice
     * in a row is impossible. */
    function refocus() {
      if (!refocusId) return;
      const selector =
        itemSelector + '[data-id="' + refocusId + '"] ' + handleSelector;
      refocusId = null;
      const handle = container.querySelector(selector);
      if (handle) handle.focus();
    }

    return { cancel: cancelDrag, isDragging: () => !!(drag && drag.started) };
  }

  window.initDragReorder = initDragReorder;
})();
