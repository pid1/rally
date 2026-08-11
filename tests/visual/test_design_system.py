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
