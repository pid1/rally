"""Free text from somewhere else, made readable without being made dangerous.

An event's Notes arrive two ways. A person typed them into Rally, or an
external calendar sent them, and some calendars send HTML. Both used to be
escaped and dropped into a ``<span>``, so a family saw literal tags in a
single block with no paragraphs and no tappable links.

There are two paths here, and which one runs is decided by where the text came
from rather than by guessing at its content:

Plain text
    Escapes everything, turns a blank line into a paragraph and a single line
    break into ``<br>`` (the same "Enter works the way people expect" rule
    ``rally.markdown`` gets from ``breaks``), and links bare ``http(s)`` URLs.
    Text typed into Rally always takes this path, so a tag a person types is
    shown exactly as typed.

HTML
    Only for text from an external calendar that contains something
    tag-shaped. It is an **allowlist**: the output contains ``p``, ``br``,
    ``ul``, ``ol``, ``li``, ``strong``, ``em`` and ``a``, and nothing else.
    Every attribute is dropped except an ``href`` that passes the scheme check
    below. Any other tag disappears and its text stays. ``script``, ``style``
    and ``title`` disappear with their contents.

The output of either path is inserted into the page as markup rather than as
text, which makes this module the second half of Rally's trust boundary (the
first is ``rally.markdown``). It is safe because it never passes anything
through: every piece of text is escaped, and every tag in the result is one this
file wrote itself. The output is balanced by construction, so a stray or missing
closing tag in the source cannot break the modal around it.

Built on the standard library's ``html.parser`` rather than a sanitizer
dependency: the allowlist is small enough to read in one sitting, and a
converter that *rebuilds* markup from parsed events has no "forgot to strip
one" failure mode to defend against.
"""

import re
from html import escape
from html.parser import HTMLParser
from urllib.parse import urlsplit

# `<` + optional `/` + a letter. The same test `schemas._MARKUP_RE` uses, for the
# same reason: "temp < 40" and "the 5<6 rule" are text, not tags.
_TAG_SHAPED = re.compile(r"<\s*/?[a-zA-Z]")

_LINK_SCHEMES = frozenset({"http", "https", "mailto", "tel"})
_SCHEME = re.compile(r"^([A-Za-z][A-Za-z0-9+.\-]*):")

# Everything a browser ignores or strips inside a URL while it works out the
# scheme — a tab or newline in the middle of `java\nscript:` is skipped, not
# an error, so the check has to skip it too.
_URL_NOISE = re.compile(r"[\x00-\x20\x7f]")

_BARE_URL = re.compile(r"https?://[^\s<>\"']+")
_TRAILING_PUNCTUATION = ".,;:!?"

_BLOCK_BREAKS = frozenset(
    {
        "blockquote",
        "pre",
        "table",
        "tr",
        "td",
        "th",
        "section",
        "article",
        "header",
        "footer",
        "hr",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
    }
)
_PARAGRAPHS = frozenset({"p", "div"})
_DROPPED_WITH_CONTENTS = frozenset({"script", "style", "title"})
_INLINE = {"b": "strong", "strong": "strong", "i": "em", "em": "em"}

_LINK_ATTRS = 'class="inline-link" target="_blank" rel="noopener noreferrer"'


def render_description(text: str | None, *, source: str) -> str:
    """An event's description as HTML safe to insert into the page.

    Returns an empty string for empty or missing input, so a caller can treat
    "no notes" and "notes with nothing in them" as the same absent state.
    """
    if not text or not text.strip():
        return ""
    # Imported here rather than at the top: this module is generic and the
    # calendars package is not, so nothing else in it should have to load one to
    # use the other.
    from rally.calendars.occurrence import SOURCE_NATIVE

    if source != SOURCE_NATIVE and _TAG_SHAPED.search(text):
        return render_html(text)
    return render_plain(text)


def _link_url(match: re.Match) -> tuple[str, str]:
    """A bare URL, and the trailing punctuation that is the sentence's, not its.

    A closing parenthesis is only the sentence's when it has no opener in the
    URL, so ``(see https://example.com/a)`` loses its bracket and
    ``https://en.wikipedia.org/wiki/Rally_(disambiguation)`` keeps its own.
    """
    url = match.group(0)
    tail = ""
    while url:
        last = url[-1]
        if last in _TRAILING_PUNCTUATION or (last == ")" and url.count(")") > url.count("(")):
            tail = last + tail
            url = url[:-1]
        else:
            break
    return url, tail


def _autolink(text: str) -> str:
    """Escaped text in which every bare http(s) URL is a link."""
    html = []
    cursor = 0
    for match in _BARE_URL.finditer(text):
        url, tail = _link_url(match)
        if not url:
            continue
        html.append(escape(text[cursor : match.start()]))
        html.append(f'<a {_LINK_ATTRS} href="{escape(url)}">{escape(url)}</a>')
        html.append(escape(tail))
        cursor = match.end()
    html.append(escape(text[cursor:]))
    return "".join(html)


def render_plain(text: str | None) -> str:
    """Plain text as paragraphs, line breaks and links."""
    if not text or not text.strip():
        return ""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    paragraphs = []
    for block in re.split(r"\n[ \t]*\n+", normalized):
        lines = [line.strip() for line in block.split("\n")]
        lines = [line for line in lines if line]
        if lines:
            paragraphs.append("<p>" + "<br>".join(_autolink(line) for line in lines) + "</p>")
    return "".join(paragraphs)


def _safe_href(href: str | None) -> str | None:
    """The href to link to, or ``None`` when it should not be a link at all."""
    if not href:
        return None
    cleaned = _URL_NOISE.sub("", href)
    scheme = _SCHEME.match(cleaned)
    if not scheme or scheme.group(1).lower() not in _LINK_SCHEMES:
        return None
    if scheme.group(1).lower() in ("http", "https") and not urlsplit(cleaned).netloc:
        return None
    return cleaned


class _Converter(HTMLParser):
    """Rebuilds a description from the parser's events, keeping only the allowlist.

    ``blocks`` collects finished top-level HTML. ``buffer`` is the inline run
    being written, ``open_inline`` the ``strong`` / ``em`` / ``a`` elements still
    open inside it, and ``lists`` a stack of list frames, so a list inside a
    list item lands where it belongs.
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.blocks: list[str] = []
        self.buffer: list[str] = []
        self.has_content = False
        self.open_inline: list[tuple[str, str | None]] = []
        self.lists: list[dict] = []
        self.skipping = 0
        self.link_depth = 0

    # -- output -------------------------------------------------------------

    def _openers(self) -> list[str]:
        return [opener for _, opener in self.open_inline if opener]

    def _inline_html(self) -> str:
        """The current run, closed off, with leading and trailing breaks trimmed."""
        closers = [f"</{tag}>" for tag, opener in reversed(self.open_inline) if opener]
        html = "".join(self.buffer) + "".join(closers)
        html = re.sub(r"<(strong|em)></\1>", "", html)
        return re.sub(r"^(?:<br>)+|(?:<br>)+$", "", html)

    def _sink(self, html: str, *, block: bool) -> None:
        """Put finished HTML where the current context wants it."""
        if not self.lists:
            self.blocks.append(html if not block else f"<p>{html}</p>")
            return
        frame = self.lists[-1]
        if frame["item"] is None:
            frame["item"] = []
        parts = frame["item"]
        if not block:  # a nested list: appended as it is
            parts.append(html)
            return
        if parts and not parts[-1].startswith(("<ul>", "<ol>")):
            parts.append("<br>")
        parts.append(html)

    def _flush(self) -> None:
        """End the current run: emit it if it holds anything, then reopen what was open."""
        if self.has_content:
            self._sink(self._inline_html(), block=True)
        self.buffer = self._openers()
        self.has_content = False

    def _finish_item(self) -> None:
        frame = self.lists[-1]
        if frame["item"] is not None:
            frame["items"].append("<li>" + "".join(frame["item"]) + "</li>")
        frame["item"] = None

    def _close_list(self) -> None:
        self._flush()
        self._finish_item()
        frame = self.lists.pop()
        if frame["items"]:
            self._sink(f"<{frame['tag']}>{''.join(frame['items'])}</{frame['tag']}>", block=False)

    # -- parser events ------------------------------------------------------

    def handle_starttag(self, tag, attrs):
        if tag in _DROPPED_WITH_CONTENTS:
            self.skipping += 1
        if self.skipping:
            return
        if tag in _PARAGRAPHS or tag in _BLOCK_BREAKS:
            self._flush()
        elif tag == "br":
            if self.has_content:
                self.buffer.append("<br>")
        elif tag in ("ul", "ol"):
            self._flush()
            self.lists.append({"tag": tag, "items": [], "item": None})
        elif tag == "li":
            if not self.lists:
                self.lists.append({"tag": "ul", "items": [], "item": None})
            self._flush()
            self._finish_item()
            self.lists[-1]["item"] = []
        elif tag in _INLINE:
            name = _INLINE[tag]
            self.open_inline.append((name, f"<{name}>"))
            self.buffer.append(f"<{name}>")
        elif tag == "a":
            href = _safe_href(dict(attrs).get("href"))
            if href and not self.link_depth:
                opener = f'<a {_LINK_ATTRS} href="{escape(href)}">'
                self.open_inline.append(("a", opener))
                self.buffer.append(opener)
            else:
                # Still tracked, so its closing tag pairs with it, but it
                # writes nothing: an unusable link is its label and no more.
                self.open_inline.append(("a", None))
            self.link_depth += 1

    def handle_endtag(self, tag):
        if tag in _DROPPED_WITH_CONTENTS:
            self.skipping = max(0, self.skipping - 1)
            return
        if self.skipping:
            return
        if tag in _PARAGRAPHS or tag in _BLOCK_BREAKS:
            self._flush()
        elif tag == "li":
            if self.lists:
                self._flush()
                self._finish_item()
        elif tag in ("ul", "ol"):
            if self.lists:
                self._close_list()
        elif tag in _INLINE or tag == "a":
            name = _INLINE.get(tag, tag)
            for index in range(len(self.open_inline) - 1, -1, -1):
                if self.open_inline[index][0] == name:
                    self._close_inline_down_to(index)
                    break

    def _close_inline_down_to(self, index: int) -> None:
        """Close ``open_inline[index]`` and anything opened after it."""
        while len(self.open_inline) > index:
            name, opener = self.open_inline.pop()
            if name == "a":
                self.link_depth -= 1
            if opener:
                self.buffer.append(f"</{name}>")

    def handle_data(self, data):
        if self.skipping:
            return
        # HTML collapses every run of whitespace, newlines included.
        text = re.sub(r"\s+", " ", data)
        if not self.has_content:
            text = text.lstrip()
        if not text:
            return
        self.buffer.append(escape(text) if self.link_depth else _autolink(text))
        self.has_content = True

    def result(self) -> str:
        self.close()
        while self.lists:
            self._close_list()
        self._flush()
        return "".join(self.blocks)


def render_html(text: str | None) -> str:
    """HTML from another calendar, reduced to the allowlist and rebuilt."""
    if not text or not text.strip():
        return ""
    converter = _Converter()
    converter.feed(text)
    return converter.result()
