"""Tests for the description renderer: what each path produces, and what it refuses to.

`rich_text` is the second place Rally inserts text as markup rather than as
escaped text, so as with `rally.markdown` the tests are the boundary. There is
one per property: attributes, schemes, script content, entities, balance.
Everything the converter emits must be a tag it wrote itself, which the last
group checks against arbitrary tag soup rather than the cases someone thought of.
"""

import random
import re
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from rally import rich_text
from rally.calendars.occurrence import Occurrence
from rally.routers.events import _occurrence_response
from rally.schemas import OccurrenceResponse

LINK = 'class="inline-link" target="_blank" rel="noopener noreferrer"'

# What a family really pasted: paragraphs carrying `dir`, an empty trailing one,
# and a link whose label is its URL.
SCHOOL = (
    '<p dir="auto">Early Inquiry is now open.</p>'
    '<p dir="auto">Submit the form to stay informed.</p>'
    '<p dir="auto">Early Inquiry Form:<br>'
    '<a href="https://www.ourschool.org/admissions/x">https://www.ourschool.org/admissions/x</a></p>'
    '<p dir="auto"></p>'
)


def _render(text, source="ics"):
    return rich_text.render_description(text, source=source)


# --- Which path runs -------------------------------------------------------------


def test_native_text_is_always_plain_so_a_typed_tag_is_shown_as_typed():
    assert _render("<b>bold</b>", source="native") == "<p>&lt;b&gt;bold&lt;/b&gt;</p>"


@pytest.mark.parametrize("source", ["ics", "caldav_google", "caldav_apple"])
def test_every_external_source_takes_the_html_path_when_it_holds_a_tag(source):
    assert _render("<b>bold</b>", source=source) == "<p><strong>bold</strong></p>"


def test_external_text_without_a_tag_takes_the_plain_path():
    assert _render("temp < 40\n\nbring layers") == "<p>temp &lt; 40</p><p>bring layers</p>"


def test_a_comparison_is_not_mistaken_for_a_tag():
    # The rule that keeps "the 5<6 rule" plain is tag-shaped, not the "<" character.
    assert _render("the 5<6 rule") == "<p>the 5&lt;6 rule</p>"


@pytest.mark.parametrize("empty", [None, "", "   \n  "])
def test_nothing_renders_as_nothing(empty):
    assert _render(empty) == ""
    assert _render(empty, source="native") == ""


# --- Plain text ----------------------------------------------------------------


def test_a_blank_line_is_a_paragraph_and_a_single_newline_is_a_break():
    assert _render("Doors open\nBring cash\n\nSee you there", source="native") == (
        "<p>Doors open<br>Bring cash</p><p>See you there</p>"
    )


def test_windows_and_old_mac_line_endings_are_paragraphs_too():
    assert _render("a\r\n\r\nb\r\rc", source="native") == "<p>a</p><p>b</p><p>c</p>"


def test_a_bare_url_becomes_a_link():
    url = "https://www.example.com/events/2026-10-03/"
    assert _render(f"Doors open at 5:30pm\n\n{url}", source="native") == (
        f'<p>Doors open at 5:30pm</p><p><a {LINK} href="{url}">{url}</a></p>'
    )


def test_a_sentence_ending_after_a_url_is_not_part_of_the_url():
    result = _render("Read https://example.com/a.", source="native")
    assert (
        result == f'<p>Read <a {LINK} href="https://example.com/a">https://example.com/a</a>.</p>'
    )


def test_a_bracket_around_a_url_is_not_part_of_it_but_the_urls_own_is():
    wrapped = _render("(see https://example.com/a)", source="native")
    assert 'href="https://example.com/a">' in wrapped and wrapped.endswith("</a>)</p>")
    own = _render("https://en.wikipedia.org/wiki/Rally_(disambiguation)", source="native")
    assert 'href="https://en.wikipedia.org/wiki/Rally_(disambiguation)"' in own


def test_a_query_string_ampersand_is_escaped_in_the_href():
    result = _render("https://example.com/?a=1&b=2", source="native")
    assert 'href="https://example.com/?a=1&amp;b=2"' in result


def test_only_http_and_https_are_linked_when_they_are_merely_written():
    assert "<a" not in _render("javascript:alert(1) ftp://example.com", source="native")


# --- HTML: what is kept ----------------------------------------------------------


def test_the_school_description_becomes_paragraphs_and_a_link_with_no_empty_paragraph():
    url = "https://www.ourschool.org/admissions/x"
    assert _render(SCHOOL) == (
        "<p>Early Inquiry is now open.</p>"
        "<p>Submit the form to stay informed.</p>"
        f'<p>Early Inquiry Form:<br><a {LINK} href="{url}">{url}</a></p>'
    )


def test_bold_italic_and_a_list_are_kept():
    assert _render(
        "<p><b>Bring</b> <i>both</i>:</p><ul><li>cleats</li><li>shin guards</li></ul>"
    ) == (
        "<p><strong>Bring</strong> <em>both</em>:</p><ul><li>cleats</li><li>shin guards</li></ul>"
    )


def test_an_ordered_list_stays_ordered_and_a_list_can_nest():
    assert _render("<ol><li>a<ul><li>b</li></ul></li><li>c</li></ol>") == (
        "<ol><li>a<ul><li>b</li></ul></li><li>c</li></ol>"
    )


def test_br_and_self_closing_br_are_breaks_but_a_leading_or_trailing_one_is_dropped():
    assert _render("<p><br>a<br/>b<br></p>") == "<p>a<br>b</p>"


def test_a_bare_url_in_html_text_is_linked_but_one_inside_a_link_is_not_linked_twice():
    result = _render(
        'see https://example.com/x and <a href="https://example.com/y">https://example.com/y</a>'
    )
    assert result.count("<a ") == 2 and "</a></a>" not in result


def test_whitespace_collapses_the_way_html_does():
    assert _render("<p>one\n   two</p>\n\n<p>three</p>") == "<p>one two</p><p>three</p>"


# --- HTML: what is refused -------------------------------------------------------


def test_every_attribute_is_dropped_but_the_links_href():
    result = _render('<p dir="auto" style="color:red" onclick="x()"><b class="c" id="i">t</b></p>')
    assert result == "<p><strong>t</strong></p>"


def test_an_anchors_other_attributes_never_reach_the_page():
    result = _render('<a href="https://e.com" onclick="steal()" target="_self" style="x">go</a>')
    assert "onclick" not in result and "style" not in result and 'target="_self"' not in result
    assert result.count("target=") == 1


@pytest.mark.parametrize(
    "href",
    [
        "javascript:alert(1)",
        "JaVaScRiPt:alert(1)",
        "java\tscript:alert(1)",
        "java&#x0A;script:alert(1)",
        " javascript:alert(1)",
        "data:text/html,<script>x</script>",
        "vbscript:x",
        "/relative/path",
        "//example.com/protocol-relative",
        "http:",
        "https:///nohost",
        "",
    ],
)
def test_a_link_that_is_not_web_mail_or_phone_is_its_label_and_no_anchor(href):
    result = _render(f'<a href="{href}">label</a>')
    assert "<a" not in result and "href" not in result
    assert result == "<p>label</p>"


@pytest.mark.parametrize(
    "href", ["http://e.com/a", "HTTPS://E.COM/a", "mailto:a@b.co", "tel:+12065550147"]
)
def test_the_four_permitted_schemes_link(href):
    assert f'href="{href}"' in _render(f'<a href="{href}">x</a>')


def test_a_padded_href_is_linked_with_the_padding_removed():
    # Browsers ignore whitespace and control characters around and inside a URL,
    # so a feed that pads one still means a link — and what is emitted is the
    # cleaned value, never the padded one.
    result = _render('<a href="  https://e.com/a\n">x</a>')
    assert 'href="https://e.com/a"' in result


def test_a_href_cannot_break_out_of_its_attribute():
    result = _render("<a href='https://e.com/\"onmouseover=\"x'>x</a>")
    assert "onmouseover=" not in result.replace("&quot;onmouseover=&quot;", "")
    assert '"onmouseover' not in result


def test_the_bracketed_markdown_style_href_in_the_school_sample_is_a_label_only():
    text = (
        '<p><a href="[https://a.org/x](https://b.org/x)">[https://a.org/x](https://b.org/x)</a></p>'
    )
    result = _render(text)
    assert result == "<p>[https://a.org/x](https://b.org/x)</p>"


def test_script_style_and_title_vanish_with_their_contents():
    result = _render("<title>T</title><style>p{}</style><p>a<script>alert(1)</script>b</p>")
    assert result == "<p>ab</p>"


def test_an_unclosed_script_swallows_the_rest_rather_than_running_it():
    assert "alert" not in _render("<p>a</p><script>alert(1)")


def test_unlisted_tags_go_but_their_text_stays():
    assert _render("<h1>Head</h1><table><tr><td>cell</td></tr></table><span>x</span>y") == (
        "<p>Head</p><p>cell</p><p>xy</p>"
    )


def test_an_entity_is_decoded_and_then_escaped_again_so_it_cannot_become_markup():
    result = _render("<p>&lt;script&gt;alert(1)&lt;/script&gt; &amp; &#39;q&#39;</p>")
    assert result == "<p>&lt;script&gt;alert(1)&lt;/script&gt; &amp; &#x27;q&#x27;</p>"


def test_a_link_inside_a_link_is_not_nested():
    result = _render('<a href="https://a.com">x<a href="https://b.com">y</a></a>')
    assert result.count("<a ") == 1


# --- HTML: the output is balanced ---------------------------------------------------


def test_stray_and_unclosed_tags_leave_balanced_output():
    assert (
        _render("</b></p></ul>text<b>bold<i>both")
        == "<p>text<strong>bold<em>both</em></strong></p>"
    )


def test_an_inline_element_spanning_paragraphs_is_closed_and_reopened_in_each():
    assert (
        _render("<b><p>x</p><p>y</p></b>") == "<p><strong>x</strong></p><p><strong>y</strong></p>"
    )


def test_an_li_outside_a_list_still_lands_in_one():
    assert _render("<li>a</li><li>b</li>") == "<ul><li>a</li><li>b</li></ul>"


_ALLOWED = {"p", "br", "ul", "ol", "li", "strong", "em", "a"}
_TOKENS = [
    "<p>", "</p>", "<div>", "</div>", "<br>", "<br/>", "<ul>", "</ul>", "<ol>", "</ol>",
    "<li>", "</li>", "<b>", "</b>", "<i>", "</i>", "<em>", "</em>", "<strong>", "</strong>",
    '<a href="https://e.com/x">', '<a href="javascript:x">', "<a>", "</a>", "<script>", "</script>",
    "<style>", "</style>", "<h2>", "</h2>", "<td>", "</td>", "<span onclick=x>", "</span>",
    "text", " ", "\n", "&amp;", "&lt;", "&#60;script&#62;", "https://e.com/y", "800-111-1234", "<", ">",
]  # fmt: skip


def _assert_balanced_and_allowlisted(html):
    stack = []
    for closing, name in re.findall(r"<(/?)([a-zA-Z0-9]+)", html):
        assert name in _ALLOWED, f"{name} is not on the allowlist: {html!r}"
        if name == "br":
            continue
        if closing:
            assert stack and stack.pop() == name, f"unbalanced </{name}>: {html!r}"
        else:
            stack.append(name)
    assert not stack, f"unclosed {stack}: {html!r}"
    # Every "<" that survives is one of the converter's own tags.
    assert not re.search(r"<(?!/?(?:p|br|ul|ol|li|strong|em|a)[ >])", html), html
    assert "onclick" not in html and "<script" not in html and "javascript:" not in html


@pytest.mark.parametrize("seed", range(200))
def test_arbitrary_tag_soup_always_yields_balanced_output_of_allowlisted_tags(seed):
    rng = random.Random(seed)
    soup = "".join(rng.choice(_TOKENS) for _ in range(rng.randint(1, 40)))
    _assert_balanced_and_allowlisted(rich_text.render_html(soup))


# --- The API's shape ---------------------------------------------------------------


def _response(description, source):
    start = datetime(2026, 8, 14, 14, tzinfo=UTC)
    occurrence = Occurrence(
        uid="u",
        source=source,
        title="Open house",
        start=start,
        end=start + timedelta(hours=1),
        start_local_date="2026-08-14",
        end_local_date="2026-08-14",
        description=description,
    )
    return _occurrence_response(occurrence, ZoneInfo("America/Chicago"))


def test_an_occurrence_carries_the_raw_description_and_the_rendered_one():
    response = _response(SCHOOL, "ics")
    assert isinstance(response, OccurrenceResponse)
    assert response.description == SCHOOL, "the raw text is what the edit form loads"
    assert response.description_html.startswith("<p>Early Inquiry is now open.</p>")
    assert response.model_dump()["description_html"] == response.description_html


def test_an_occurrence_with_no_description_has_no_html():
    assert _response("", "ics").description_html == ""
