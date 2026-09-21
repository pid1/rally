"""The one place a family's markdown becomes HTML.

Notes are the only text in Rally that renders as markup rather than as escaped
text, so the configuration below is the whole security boundary and there is
deliberately one renderer rather than one per caller.

Three settings carry the behavior, and none of them is incidental:

``"zero"``
    The preset that starts with **every** rule disabled. ``enable()`` only ever
    adds rules — it cannot take one away — so starting from ``"commonmark"``
    and enabling emphasis and lists would leave headings, links, images, code
    spans, fenced blocks, blockquotes and horizontal rules switched on as well.
    Beginning at zero makes the ``enable()`` call below the complete list of
    what a note can produce.

``html=False``
    Disables HTML passthrough, so a tag the family types is escaped to text
    rather than rendered. This is the backstop, not the primary control: the
    API rejects a note containing markup before it is ever stored (see
    ``schemas.reject_markup``). It stays because a validator can have a bug and
    this cannot.

``breaks=True`` plus the ``newline`` rule
    Standard markdown collapses a single newline, which would render
    "Soccer at 5" / Enter / "Pack the bag" as one run-on line. Both are
    required and neither works alone — ``breaks`` changes what the ``newline``
    rule emits, and ``"zero"`` has that rule switched off, so setting
    ``breaks`` by itself has nothing to act on.
"""

from markdown_it import MarkdownIt

# Bold, italic, bullet lists, numbered lists, and a line break per Enter.
# Everything else renders as the literal characters the family typed.
_RENDERER = MarkdownIt("zero", {"html": False, "breaks": True}).enable(
    ["emphasis", "list", "newline"]
)


def render(text: str | None) -> str:
    """Render a note's markdown source to HTML.

    Returns an empty string for empty or missing input, so a caller can treat
    "no note" and "a note with nothing in it" as the same absent state — which
    is what the dashboard's render-or-omit rule depends on.
    """
    if not text or not text.strip():
        return ""
    return _RENDERER.render(text)
