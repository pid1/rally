"""The closed member color palette: its invariants, and the two halves agreeing.

These are the properties `rally.member_colors` claims in its docstring. They are
asserted rather than described because the palette's whole value is a guarantee
— that any two members are distinguishable on any display Rally runs on — and a
guarantee nothing checks is a comment.
"""

import re
from pathlib import Path

from rally import member_colors

STYLESHEET = Path(__file__).parent.parent / "static" / "styles.css"

# --surface and --surface-sunken, the two backgrounds a dot is ever drawn on.
SURFACES = ("#ffffff", "#f5f5f5")


def _channel(value: float) -> float:
    return value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4


def relative_luminance(color: str) -> float:
    raw = color.lstrip("#")
    r, g, b = (_channel(int(raw[i : i + 2], 16) / 255) for i in (0, 2, 4))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: str, b: str) -> float:
    high, low = sorted((relative_luminance(a), relative_luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def test_stylesheet_and_module_declare_the_same_palette():
    """The rendering half and the validating half cannot be allowed to drift."""
    css = STYLESHEET.read_text()

    for entry in member_colors.PALETTE:
        declared = re.search(rf"{re.escape(entry.token)}:\s*(#[0-9a-fA-F]{{6}});", css)
        assert declared, f"{entry.token} is not declared in styles.css"
        assert declared.group(1).lower() == entry.value, (
            f"{entry.token} is {declared.group(1)} in the stylesheet "
            f"but {entry.value} in rally.member_colors"
        )


def test_every_entry_clears_non_text_contrast_on_both_surfaces():
    """WCAG 1.4.11: a dot is a UI component, so the bar is 3:1 rather than 4.5:1."""
    for entry in member_colors.PALETTE:
        for surface in SURFACES:
            ratio = contrast(entry.value, surface)
            assert ratio >= 3.0, f"{entry.label} is {ratio:.2f}:1 on {surface}"


def test_entries_stay_separable_with_color_removed():
    """The monochrome e-ink property, and the reason the palette stops at five.

    Rally is greyscale first: on a panel with no color, luminance is the entire
    identity signal. Adjacent entries hold at least 1.2x so five members remain
    five distinguishable grays.
    """
    ordered = sorted(member_colors.MEMBER_COLORS, key=relative_luminance)

    for darker, lighter in zip(ordered, ordered[1:], strict=False):
        step = (relative_luminance(lighter) + 0.05) / (relative_luminance(darker) + 0.05)
        assert step >= 1.2, f"{darker} and {lighter} are only {step:.2f}x apart in luminance"


def test_the_palette_is_ordered_darkest_first():
    """The declared order is the ladder, and both the swatches and
    auto-assignment walk it in that order."""
    luminances = [relative_luminance(value) for value in member_colors.MEMBER_COLORS]
    assert luminances == sorted(luminances)


def test_values_are_unique_and_well_formed():
    assert len(set(member_colors.MEMBER_COLORS)) == len(member_colors.MEMBER_COLORS)
    for value in member_colors.MEMBER_COLORS:
        assert re.fullmatch(r"#[0-9a-f]{6}", value), value


def test_is_palette_color_is_case_insensitive_and_rejects_everything_else():
    assert member_colors.is_palette_color(member_colors.MEMBER_COLORS[0].upper())
    assert not member_colors.is_palette_color(None)
    # Well-formed and invisible — the case a format check would wave through.
    assert not member_colors.is_palette_color("#ffffff")
    assert not member_colors.is_palette_color("#333333")


def test_next_unused_walks_the_palette_then_cycles_by_member():
    assert member_colors.next_unused([]) == member_colors.PALETTE[0].value
    assert member_colors.next_unused(member_colors.MEMBER_COLORS[:2]) == (
        member_colors.PALETTE[2].value
    )

    # Beyond five, a seventh member follows a sixth round the ladder rather
    # than landing on the same entry as them.
    taken = list(member_colors.MEMBER_COLORS)
    sixth = member_colors.next_unused(taken)
    seventh = member_colors.next_unused(taken + [sixth])
    assert sixth != seventh


def test_default_is_a_palette_color():
    assert member_colors.is_palette_color(member_colors.DEFAULT_COLOR)
