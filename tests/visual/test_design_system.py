"""Design-system regression tests.

These encode the rules in docs/visual-design-system.md as assertions against a
real rendering. They are deliberately geometric rather than pixel-based: when
one fails it says which rule broke and by how much, which a screenshot diff
cannot.

Each test names the audit finding it protects against.
"""

from __future__ import annotations

import pytest

from .conftest import PAGES, TOOLBAR_PAGES, VIEWPORTS

ALL_PAGES = sorted(PAGES)
TARGET_MIN = 44.0


@pytest.mark.parametrize("viewport", sorted(VIEWPORTS))
@pytest.mark.parametrize("page", ALL_PAGES)
def test_page_blocks_share_a_left_edge(measure, page, viewport):
    """A1 — the header, nav, page column and footer must line up.

    Two nested max-widths used to put every desktop page 2px out of line with
    its own header rule.
    """
    edges = measure(page, viewport)["leftEdges"]
    assert len(set(edges.values())) == 1, f"left edges disagree on {page}/{viewport}: {edges}"


@pytest.mark.parametrize("viewport", sorted(VIEWPORTS))
@pytest.mark.parametrize("page", ALL_PAGES)
def test_no_horizontal_overflow(measure, page, viewport):
    """The body never scrolls sideways at any supported width."""
    frame = measure(page, viewport)["frame"]
    assert not frame["horizontalOverflow"], (
        f"{page}/{viewport} scrolls horizontally: {frame['scrollWidth']} > {frame['clientWidth']}"
    )


@pytest.mark.parametrize("viewport", sorted(VIEWPORTS))
@pytest.mark.parametrize("page", TOOLBAR_PAGES)
def test_filter_labels_are_placed_by_rule_not_by_chip_count(measure, page, viewport):
    """B1 — label placement is a design decision, not an accident of wrapping.

    Stacked below 768px, inline at and above it. Before the rebuild this was
    emergent: three chips sat inline and four did not, so Meal History showed
    both behaviours in one toolbar and adding a family member relaid out a page.
    """
    toolbar = measure(page, viewport)["toolbar"]
    assert toolbar, f"{page} has no toolbar"
    if not toolbar["groups"]:
        # Chips are derived from the rows on the page, so a page whose archive
        # is empty in the sample data renders none. Nothing to compare.
        pytest.skip(f"{page} renders no filter chips with the seeded data")
    expected = VIEWPORTS[viewport][0] >= 768
    for group in toolbar["groups"]:
        assert group["labelInline"] is expected, (
            f"{page}/{viewport}: '{group['label']}' ({group['chipCount']} chips) "
            f"is {'inline' if group['labelInline'] else 'stacked'}, expected "
            f"{'inline' if expected else 'stacked'}"
        )


@pytest.mark.parametrize("viewport", sorted(VIEWPORTS))
def test_clear_filters_occupies_one_fixed_slot_on_every_page(measure, viewport):
    """B2 — the reset control cannot drift between pages.

    It used to be a child of whichever filter group a template picked, so it
    landed in five positions across five pages, and on Meal History it changed
    row as well as column.
    """
    seen: dict[str, tuple[float, float]] = {}
    for page in TOOLBAR_PAGES:
        toolbar = measure(page, viewport)["toolbar"]
        assert toolbar["reset"], f"{page} has no toolbar reset slot"
        assert toolbar["resetText"] == "Clear Filters"
        assert not toolbar["resetInsideGroup"], f"{page}: the reset is inside a filter group again"
        assert toolbar["resetIsLastChild"], f"{page}: the reset is not the toolbar's last block"
        reset = toolbar["reset"]
        seen[page] = (reset["x"], round(reset["bottom"] - toolbar["box"]["bottom"], 1))

    positions = set(seen.values())
    assert len(positions) == 1, f"Clear Filters sits differently across pages at {viewport}: {seen}"


@pytest.mark.parametrize("viewport", sorted(VIEWPORTS))
def test_page_header_is_the_same_distance_from_the_next_block_everywhere(measure, viewport):
    """A2/A3 — the band under the title rule comes from the stack, not luck.

    It used to be 32, 61 or 92px depending on whether a page happened to carry
    an archive link and a retention note as loose siblings.
    """
    gaps = {}
    for page in ALL_PAGES:
        data = measure(page, viewport)
        if data.get("headerToNext") is not None:
            gaps[page] = data["headerToNext"]
    assert len(gaps) >= 6, f"expected most pages to use .page-header, got {sorted(gaps)}"
    assert len(set(gaps.values())) == 1, f"header-to-content gap varies at {viewport}: {gaps}"


@pytest.mark.parametrize("page", ALL_PAGES)
def test_content_starts_above_the_fold_on_a_phone(measure, page):
    """A4 — a phone must show real content without scrolling.

    Four of eight pages used to place the first row below an 844px viewport;
    Completed Tasks needed 981px before anything actionable appeared.
    """
    data = measure(page, "mobile")
    first, height = data["firstContent"], data["frame"]["viewportHeight"]
    assert first, f"{page} renders no content container"
    assert first["y"] < height, (
        f"{page}: first content row at {first['y']}px, below the {height}px fold"
    )


@pytest.mark.parametrize("page", ALL_PAGES)
def test_touch_targets_meet_44px_on_a_phone(measure, page):
    """E2 — every hit area is at least 44×44 where fingers are used.

    Measured at the effective target: a checkbox inside a label is tapped via
    the label, so the label is what has to be big enough.
    """
    too_small = [
        t for t in measure(page, "mobile")["targets"] if t["w"] < TARGET_MIN or t["h"] < TARGET_MIN
    ]
    assert not too_small, f"{page} has undersized touch targets: {too_small[:6]}"


@pytest.mark.parametrize("page", ALL_PAGES)
def test_every_control_shows_a_focus_ring(measure, page):
    """E1 — no control may suppress focus without replacing it.

    The whole filter toolbar used to set `outline: none` on `:focus`, leaving
    keyboard users with no indication of where they were.
    """
    blind = [f for f in measure(page, "desktop")["focusable"] if not f["ring"]]
    assert not blind, f"{page} has controls with no visible focus ring: {blind}"


@pytest.mark.parametrize("viewport", sorted(VIEWPORTS))
@pytest.mark.parametrize("page", ALL_PAGES)
def test_type_sizes_come_from_the_scale(measure, page, viewport):
    """C1 — every rendered font-size is one of the six type tokens.

    There were 17 declared sizes, nine of them inside a 0.4rem band.
    """
    data = measure(page, viewport)
    allowed = {round(v, 2) for v in data["tokens"]["textPx"]}
    used = {round(float(size.rstrip("px")), 2) for size in data["fontSizes"]}
    assert used <= allowed, (
        f"{page}/{viewport} renders off-scale type: "
        f"{sorted(used - allowed)} (scale: {sorted(allowed)})"
    )


@pytest.mark.parametrize("page", ALL_PAGES)
def test_colours_come_from_the_palette(measure, page):
    """C4 — every rendered text colour is a palette role."""
    data = measure(page, "desktop")

    def norm(value: str) -> tuple[int, ...]:
        if value.startswith("#"):
            v = value.lstrip("#")
            return tuple(int(v[i : i + 2], 16) for i in (0, 2, 4))
        return tuple(int(float(n)) for n in __import__("re").findall(r"\d+(?:\.\d+)?", value)[:3])

    allowed = {norm(c) for c in data["tokens"]["colours"]}
    used = {norm(c) for c in data["colours"]}
    assert used <= allowed, f"{page} renders off-palette colours: {sorted(used - allowed)}"


@pytest.mark.parametrize("page", ALL_PAGES)
def test_text_meets_wcag_aa_contrast(measure, page):
    """C5 — no run of text falls below 4.5:1.

    #999999 was used as a text colour in ten rules at 2.85:1, carrying real
    information: purchase dates, "Not yet rated", the paused state on
    recurring tasks.
    """
    failures = [c for c in measure(page, "desktop")["contrast"] if c["ratio"] < 4.5]
    assert not failures, f"{page} has text below 4.5:1: {failures[:6]}"


@pytest.mark.parametrize("page", ALL_PAGES)
def test_every_modal_uses_the_shared_chassis(measure, page):
    """D2 — one modal chassis, so scrollable content signals itself the same way.

    Five modals had the scroll wrapper and six did not; on a phone the ones
    without filled ~90% of the viewport with no fade to say more lay below.
    """
    for modal in measure(page, "desktop")["modals"]:
        # The verification dialog is the documented exception: no title, no
        # form, a single centred status block.
        if not modal["hasTitle"]:
            continue
        assert modal["hasScroll"] and modal["hasBody"], (
            f"{page}: modal #{modal['id']} is missing the .modal-scroll/.modal-body chassis"
        )


def _open(browser, live_server, path: str, height: int = 900):
    """A real page at desktop width, for assertions the probe cache cannot make."""
    context = browser.new_context(viewport={"width": 1440, "height": height})
    page = context.new_page()
    page.goto(live_server + path, wait_until="networkidle")
    return context, page


def test_empty_inventory_does_not_blame_the_filters(browser, live_server):
    """A fresh install has no items and no filters set.

    Telling somebody "nothing matches these filters" when they have set none
    sends them hunting for a filter to clear, which is the one thing that
    cannot help. The two empty states are different problems with different
    fixes, so they say different things.
    """
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    page = context.new_page()
    try:
        # The seeded inventory is deliberately full — an empty one is the state
        # of a fresh install, served here rather than by deleting the sample
        # data every other test on this server depends on.
        page.route(
            "**/api/preparedness/items?**",
            lambda route: route.fulfill(status=200, content_type="application/json", body="[]"),
        )
        page.goto(live_server + "/preparedness", wait_until="networkidle")
        page.wait_for_selector("#groups-container .container-empty-state")
        empty = page.locator("#groups-container .container-empty-state").inner_text()
        assert "No items yet" in empty
        assert "filter" not in empty.lower()
    finally:
        context.close()


def test_over_filtered_inventory_does_blame_the_filters(browser, live_server):
    """With a filter actually applied, the filter message is the right one."""
    context, page = _open(browser, live_server, "/preparedness")
    try:
        page.wait_for_selector("#groups-container .prep-group, #groups-container .editable-item")
        page.fill("#search-input", "kryptonite")
        page.wait_for_function(
            "() => document.querySelector('#groups-container .container-empty-state')"
            "?.innerText.includes('filters')"
        )
        empty = page.locator("#groups-container .container-empty-state").inner_text()
        assert "Nothing matches these filters" in empty
    finally:
        context.close()


# --- calendar views on a phone --------------------------------------------------


def _calendar(browser, live_server, width=390):
    context = browser.new_context(viewport={"width": width, "height": 844})
    page = context.new_page()
    page.goto(live_server + "/calendar", wait_until="networkidle")
    page.wait_for_selector("#calendar-view")
    return context, page


def test_every_view_is_reachable_on_a_phone(browser, live_server):
    """The month grid used to be hidden below 768px and the selector with it,
    so a phone had no view choice at all."""
    context, page = _calendar(browser, live_server)
    try:
        options = page.eval_on_selector_all("#view-select option", "els => els.map(e => e.value)")
        assert options == ["day", "week", "month", "agenda"]
        assert page.is_visible("#view-select"), "the selector must not be hidden on a phone"
    finally:
        context.close()


def test_a_phone_lands_on_the_day_view(browser, live_server):
    """What is happening today is what you open a calendar on a phone to find."""
    context, page = _calendar(browser, live_server)
    try:
        assert page.input_value("#view-select") == "day"
    finally:
        context.close()


def test_the_month_grid_renders_on_a_phone(browser, live_server):
    context, page = _calendar(browser, live_server)
    try:
        page.select_option("#view-select", "month")
        page.wait_for_selector(".calendar-grid", state="visible")
        assert page.is_visible(".calendar-grid")
    finally:
        context.close()


def test_the_week_starts_on_sunday(browser, live_server):
    context, page = _calendar(browser, live_server)
    try:
        page.select_option("#view-select", "month")
        page.wait_for_selector(".calendar-weekday")
        labels = page.eval_on_selector_all(
            ".calendar-weekday", "els => els.map(e => e.textContent.trim())"
        )
        assert labels[0] == "Sun", f"week must start on Sunday, got {labels}"
        assert labels == ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
    finally:
        context.close()


def test_the_arrows_move_by_the_selected_slice(browser, live_server):
    """Prev/Next must mean a day in Day view and a week in Week view."""
    context, page = _calendar(browser, live_server)
    try:
        page.select_option("#view-select", "day")
        page.wait_for_timeout(400)
        first = page.inner_text("#range-label")
        page.click("#btn-next")
        page.wait_for_timeout(500)
        assert page.inner_text("#range-label") != first

        page.select_option("#view-select", "week")
        page.wait_for_timeout(500)
        week_label = page.inner_text("#range-label")
        assert "–" in week_label, f"a week is a range, got {week_label!r}"
    finally:
        context.close()


def test_add_event_defaults_to_the_day_on_screen(browser, live_server):
    """Adding an event while reading a day means adding it to that day.

    The button used to hand the form today's date whatever you were looking
    at, so an event added from Saturday's page was saved on Monday.
    """
    context, page = _calendar(browser, live_server)
    try:
        page.select_option("#view-select", "day")
        page.wait_for_timeout(400)
        page.click("#btn-next")
        page.wait_for_timeout(500)
        viewed = page.evaluate("() => isoDate(anchor)")
        assert viewed != page.evaluate("() => todayIso()"), "Next did not move off today"

        page.click("#btn-add-event")
        page.wait_for_selector("#event-modal-overlay", state="visible")
        assert page.input_value("#event-start-time").startswith(viewed)
        assert page.input_value("#event-end-time").startswith(viewed)

        page.check("#event-all-day")
        assert page.input_value("#event-start-date") == viewed
        assert page.input_value("#event-end-date") == viewed
    finally:
        context.close()


# Every focusable field, measured against the padding edge of each ancestor
# that clips it. Only the inline axis, and only where that ancestor cannot
# scroll horizontally: ink outside a box that scrolls is still reachable, ink
# outside one that does not is simply gone.
FOCUS_CLIP_JS = r"""
(overlayId) => {
  const overlay = document.getElementById(overlayId);
  overlay.style.display = 'flex';
  const clipped = [];
  const fields = overlay.querySelectorAll(
    'input:not([type=hidden]), select, textarea, button, a[href]');
  for (const el of fields) {
    const r = el.getBoundingClientRect();
    if (!r.width || !r.height) continue;
    el.focus();
    const cs = getComputedStyle(el);
    if (cs.outlineStyle === 'none') { el.blur(); continue; }
    const ring = parseFloat(cs.outlineWidth) + Math.max(0, parseFloat(cs.outlineOffset));
    for (let n = el.parentElement; n && n !== document.body; n = n.parentElement) {
      const ncs = getComputedStyle(n);
      if (ncs.overflowX === 'visible') continue;
      if (n.scrollWidth > n.clientWidth + 1) continue;  // scrollable: ink is reachable
      const nr = n.getBoundingClientRect();
      const left = r.left - (nr.left + parseFloat(ncs.borderLeftWidth));
      const right = (nr.right - parseFloat(ncs.borderRightWidth)) - r.right;
      if (left < ring - 0.5 || right < ring - 0.5) {
        clipped.push({
          field: el.id || el.tagName.toLowerCase(),
          clipper: (n.className || n.tagName).toString().slice(0, 40),
          ring, left: Math.round(left * 10) / 10, right: Math.round(right * 10) / 10,
        });
      }
    }
    el.blur();
  }
  overlay.style.display = 'none';
  return clipped;
}
"""


@pytest.mark.parametrize("page", ALL_PAGES)
def test_focus_rings_are_not_clipped_inside_modals(browser, live_server, page):
    """E5 — a focus ring must survive the box it is drawn in.

    `.modal-body` scrolls vertically, which makes the browser clip the
    horizontal axis too. Fields inside it are full-width and the ring sits 4px
    outside their border box; there were 4px of room on the right and none on
    the left, so every focused field in every modal was drawn as three sides of
    a rectangle. E1 never caught it because a closed modal has no visible
    controls to measure.
    """
    context, p = _open(browser, live_server, PAGES[page])
    try:
        overlays = p.eval_on_selector_all(
            ".modal-overlay", "els => els.map(e => e.id).filter(Boolean)"
        )
        if not overlays:
            pytest.skip(f"{page} has no modals")
        for overlay_id in overlays:
            clipped = p.evaluate(FOCUS_CLIP_JS, overlay_id)
            assert not clipped, f"{page}/#{overlay_id} clips focus rings: {clipped[:4]}"
    finally:
        context.close()


# Open a modal, drive it to the bottom, close it, and open it again. A short
# viewport is what makes this measurable: a modal only scrolls when its content
# outruns the 90vh cap.
REOPEN_JS = r"""
(overlayId) => {
  const overlay = document.getElementById(overlayId);
  const body = overlay.querySelector('.modal-body');
  if (!body) return null;
  showModalOverlay(overlayId);
  body.scrollTop = body.scrollHeight;
  const left = body.scrollTop;
  hideModalOverlay(overlayId);
  showModalOverlay(overlayId);
  const reopened = body.scrollTop;
  hideModalOverlay(overlayId);
  return {left, reopened};
}
"""


@pytest.mark.parametrize("page", ALL_PAGES)
def test_every_modal_reopens_at_the_top(browser, live_server, page):
    """D7 — a modal opens at its first field, never where it was left.

    A hidden modal keeps its scrollTop. Scrolling to the bottom of Add Item,
    cancelling and reopening it put you back at the bottom, with the first
    field's label above the fold — a form that appears to start part-way
    through itself.
    """
    context, p = _open(browser, live_server, PAGES[page], height=460)
    try:
        overlays = p.eval_on_selector_all(
            ".modal-overlay", "els => els.map(e => e.id).filter(Boolean)"
        )
        if not overlays:
            pytest.skip(f"{page} has no modals")
        scrolled = 0
        for overlay_id in overlays:
            result = p.evaluate(REOPEN_JS, overlay_id)
            if result is None or result["left"] == 0:
                continue  # this modal fits; it has no scroll state to carry
            scrolled += 1
            assert result["reopened"] == 0, (
                f"{page}/#{overlay_id} reopened at {result['reopened']}px, "
                f"not at the top (it had been left at {result['left']}px)"
            )
        if not scrolled:
            pytest.skip(f"{page} has no modal tall enough to scroll")
    finally:
        context.close()


# Every label that wraps a checkbox or radio, measured against the control it
# wraps. Modals are shown first: most of these rows live in one, and a closed
# modal has nothing to measure.
CONTROL_ROW_JS = r"""
() => {
  const overlays = [...document.querySelectorAll('.modal-overlay')];
  const shown = overlays.filter(o => getComputedStyle(o).display === 'none');
  shown.forEach(o => { o.style.display = 'flex'; });

  const rows = [];
  for (const label of document.querySelectorAll('label')) {
    const control = label.querySelector('input[type=checkbox], input[type=radio]');
    if (!control) continue;
    const lr = label.getBoundingClientRect();
    const cr = control.getBoundingClientRect();
    if (!lr.height || !cr.height) continue;
    rows.push({
      label: (label.textContent || '').trim().slice(0, 30),
      cls: (label.className || '').toString().slice(0, 40),
      display: getComputedStyle(label).display,
      // Positive means the control sits above the middle of its own row.
      offset: Math.round(((lr.y + lr.height / 2) - (cr.y + cr.height / 2)) * 10) / 10,
      rowHeight: Math.round(lr.height * 10) / 10,
      controlHeight: Math.round(cr.height * 10) / 10,
    });
  }

  shown.forEach(o => { o.style.display = 'none'; });
  return rows;
}
"""


@pytest.mark.parametrize("page", ALL_PAGES)
def test_a_label_centres_the_control_it_wraps(browser, live_server, page):
    """A checkbox sits in the middle of its row, not on the text baseline.

    `.form-group label` is a block, and it outranked the bare `.checkbox-label`
    and `.weekday-label` classes — so inside a form every one of these rows
    silently lost its flex layout and the box floated a few pixels high, most
    visibly in the calendar's "Who's involved" chips.
    """
    context, p = _open(browser, live_server, PAGES[page])
    try:
        rows = p.evaluate(CONTROL_ROW_JS)
        if not rows:
            pytest.skip(f"{page} has no label-wrapped checkboxes")
        # A row taller than its control has wrapped onto more than one line;
        # centring a control against a paragraph is not what this measures.
        single_line = [r for r in rows if r["rowHeight"] <= r["controlHeight"] + TARGET_MIN]
        off_centre = [r for r in single_line if abs(r["offset"]) > 1]
        assert not off_centre, f"{page} has off-centre controls: {off_centre[:4]}"
    finally:
        context.close()
