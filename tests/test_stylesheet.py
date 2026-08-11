"""Static checks on the stylesheet.

These need no browser, so they run in the ordinary test job and catch the
cheap-to-make mistakes before the visual suite ever starts: a value typed
instead of a token, a selector declared twice, a focus ring switched off.

The rules they enforce are the ones written down in
docs/visual-design-system.md.
"""

from __future__ import annotations

import pathlib
import re

import pytest

CSS_PATH = pathlib.Path(__file__).resolve().parents[1] / "static" / "styles.css"

# Comments carry prose about the rules ("never set outline: none"), so every
# check runs against the stylesheet with comments removed.
SOURCE = re.sub(r"/\*.*?\*/", "", CSS_PATH.read_text(), flags=re.S)

# Blocks where raw values are the definition rather than a usage.
TOKEN_BLOCK = re.compile(r":root\s*\{[^}]*\}", re.S)
DECLARATIONS_ONLY = TOKEN_BLOCK.sub("", SOURCE)


def _rules() -> list[tuple[str, str]]:
    """Every (context, selector) pair, where context is the enclosing at-rules.

    A brace-depth walk rather than a regex: a selector inside `@media` is a
    different rule from the same selector at the top level, and conflating the
    two would let a genuine duplicate through.
    """
    rules: list[tuple[str, str]] = []
    stack: list[tuple[str, str]] = []
    buffer = ""
    for char in SOURCE:
        if char == "{":
            head = " ".join(buffer.split())
            buffer = ""
            context = " > ".join(h for kind, h in stack if kind == "at")
            if head.startswith("@"):
                stack.append(("at", head))
            else:
                rules.append((context, head))
                stack.append(("rule", head))
        elif char == "}":
            buffer = ""
            if stack:
                stack.pop()
        else:
            buffer += char
    return rules


def test_no_selector_is_declared_twice_in_the_same_context():
    """F1 — duplicated blocks are how rules silently override each other.

    `footer`, `footer a` and `footer a:hover` were each declared twice
    verbatim, and a stale duplicate of `.toolbar-search` later broke the
    toolbar's shared column grid until it was found by measurement.
    """
    seen: dict[tuple[str, str], int] = {}
    for context, selector in _rules():
        if selector.startswith("@") or selector.endswith("%"):
            continue  # at-rules and keyframe stops legitimately repeat
        seen[(context, selector)] = seen.get((context, selector), 0) + 1
    duplicates = {key: count for key, count in seen.items() if count > 1}
    assert not duplicates, f"selectors declared more than once: {duplicates}"


def test_no_rule_suppresses_the_focus_ring():
    """E1 — a control may not switch focus off without replacing it."""
    offenders = re.findall(r"[^;{}]*outline:\s*(?:none|0)[^;}]*", DECLARATIONS_ONLY)
    assert not offenders, f"focus suppressed without a replacement: {offenders}"


def test_colours_are_defined_only_as_tokens():
    """C4 — components reference roles; literals live in :root alone."""
    body = re.sub(r"url\([^)]*\)", "", DECLARATIONS_ONLY)
    literals = re.findall(r"#[0-9a-fA-F]{3,8}\b", body)
    assert not literals, f"raw colours outside the token block: {sorted(set(literals))}"


def test_type_sizes_are_defined_only_as_tokens():
    """C1 — 17 declared font sizes became six tokens; keep it that way."""
    sizes = re.findall(r"font-size:\s*([^;]+);", DECLARATIONS_ONLY)
    off_scale = [s.strip() for s in sizes if "var(--text-" not in s and s.strip() != "inherit"]
    assert not off_scale, f"font sizes not taken from the type scale: {off_scale}"


@pytest.mark.parametrize("prop", ["margin", "padding", "gap", "row-gap", "column-gap"])
def test_spacing_comes_from_the_scale(prop):
    """C2 — 18 ad hoc spacing values became one 4px scale.

    Hairlines (1px) and the -1px border overlaps are exempt: they are optical
    details, not spacing.
    """
    pattern = re.compile(rf"^\s*{prop}(?:-[a-z]+)?:\s*([^;]+);", re.M)
    offenders = []
    for value in pattern.findall(DECLARATIONS_ONLY):
        for token in value.split():
            if re.fullmatch(r"-?[0-9.]+px", token) and token not in {"1px", "-1px"}:
                offenders.append(value.strip())
    assert not offenders, f"{prop} values outside the space scale: {sorted(set(offenders))}"


def test_the_stylesheet_declares_the_documented_token_set():
    """The scales the tests and the styleguide both read back."""
    root = TOKEN_BLOCK.search(SOURCE).group(0)
    for n in range(1, 9):
        assert f"--space-{n}:" in root, f"--space-{n} is missing"
    for name in ["xs", "sm", "base", "lg", "xl", "display"]:
        assert f"--text-{name}:" in root, f"--text-{name} is missing"
    for name in ["ink", "ink-muted", "ink-subtle", "rule", "surface", "inverse"]:
        assert f"--{name}:" in root, f"--{name} is missing"
