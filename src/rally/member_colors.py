"""The closed palette of family member identity colors.

Rally is grayscale and e-ink first, so a member's color is not decoration: it
is the only color-carrying channel in the app and the one thing that says whose
event a calendar cell holds. That makes the palette a fixed, validated set
rather than a free-form hex field.

**The set is closed on purpose.** One free color input can defeat every
constraint below at once — two members rendered as the same gray on a
monochrome panel, or one so light it vanishes against the page. The guarantee
is not "these are nice colors", it is "any two members are distinguishable on
any display Rally runs on", and that only holds while the set stays closed.

Three constraints, in priority order:

1. **Monochrome e-ink separability.** Adjacent entries are at least 1.24x apart
   in relative luminance, so the five stay five distinct gray levels on a panel
   with no color at all. Everything else here is subordinate to this. It is
   what rules out picking five colors by hue alone, which lands them at similar
   lightness and collapses them into one gray the moment color is removed.
2. **WCAG 1.4.11 non-text contrast.** Every entry clears 3:1 against both
   ``--surface`` and ``--surface-sunken``. A dot is a non-text UI element, so
   3:1 is the applicable bar rather than the 4.5:1 that ``--state-due`` needs as
   body text. ``--surface-sunken`` is the binding surface, and its ceiling is
   the top of the ladder.
3. **Color e-ink gamut.** Hues sit at least 53 degrees apart, near primaries a
   Spectra/Kaleido panel reproduces, rather than on distinctions (teal vs green,
   purple vs magenta) those panels wash out. This is the layer a color display
   adds on top of a set that already works without it.

Five is the maximum those constraints allow, not a preference: the ladder runs
from "dark enough to read as black at 8px" up to the 3:1 ceiling, and six
entries compress the spacing to 1.20x, eight to 1.15x.

The ``--member-*`` custom properties in ``static/styles.css`` are the rendering
half of this list. ``tests/test_member_colors.py`` asserts the two agree, so a
value cannot be changed in the stylesheet without the validator noticing.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True)
class MemberColor:
    """One palette entry.

    ``token`` is the CSS custom property carrying the same value, and ``label``
    is what the swatch announces to a screen reader — on a monochrome panel the
    swatches are five grays, so the accessible name is doing real work rather
    than satisfying a linter.
    """

    value: str
    label: str
    token: str


# Ordered by luminance, darkest first. The order is the ladder, and it is the
# order the swatches render in and the order auto-assignment hands them out.
PALETTE: tuple[MemberColor, ...] = (
    MemberColor(value="#315277", label="Ink blue", token="--member-ink-blue"),
    MemberColor(value="#af2c3d", label="Crimson", token="--member-crimson"),
    MemberColor(value="#8859b1", label="Violet", token="--member-violet"),
    MemberColor(value="#3b8c61", label="Forest", token="--member-forest"),
    MemberColor(value="#a38b43", label="Amber", token="--member-amber"),
)

MEMBER_COLORS: tuple[str, ...] = tuple(entry.value for entry in PALETTE)
COLORS_BY_VALUE: dict[str, MemberColor] = {entry.value: entry for entry in PALETTE}

# What a member gets when nothing else has chosen for them. The darkest entry,
# so a member created by a path that skips auto-assignment is still legible.
DEFAULT_COLOR: str = PALETTE[0].value


def is_palette_color(value: str | None) -> bool:
    """Whether ``value`` is one of the five. Case-insensitive; ``None`` is not."""
    return value is not None and value.lower() in COLORS_BY_VALUE


def next_unused(taken: Iterable[str | None]) -> str:
    """The first palette entry nobody in ``taken`` is using.

    Falls back to cycling once all five are spoken for: beyond five members two
    people share a color, which is the honest outcome of a closed palette and
    better than inventing a sixth that fails the constraints above. The cycle
    counts *members*, not distinct colors, so a seventh member follows a sixth
    round the ladder instead of landing on the same entry as them.
    """
    existing = [value for value in taken if value]
    used = {value.lower() for value in existing}
    for entry in PALETTE:
        if entry.value not in used:
            return entry.value
    return PALETTE[len(existing) % len(PALETTE)].value
