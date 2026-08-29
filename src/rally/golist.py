"""The go list: grouping and export renderers.

Every preparedness item, grouped by location, locations in physical walking
order, the unassigned group last. This is the artifact you print and tape
inside a cabinet door, so it has to work with no power, no phone and no
Tailscale.
"""

from __future__ import annotations

import csv
import io
from datetime import date

from sqlalchemy.orm import Session

from rally.models import PrepItem, PrepLocation

UNASSIGNED = "Unassigned"


def build_groups(
    db: Session, location_filter: list[str] | None = None
) -> list[tuple[int | None, str, list[PrepItem]]]:
    """Group items by location.

    Returns ``(location_id, location_name, items)`` ordered by ``sort_order``
    then name, with the ``NULL`` group appended last. Empty groups are omitted:
    a location with nothing in it is not a section of a packing list.
    """
    locations = (
        db.query(PrepLocation)
        .order_by(PrepLocation.sort_order.asc(), PrepLocation.name.asc())
        .all()
    )
    items = db.query(PrepItem).all()

    wanted_ids: set[int] | None = None
    want_unassigned = True
    if location_filter:
        wanted_ids = {int(v) for v in location_filter if v.isdigit()}
        want_unassigned = "unassigned" in location_filter

    by_location: dict[int | None, list[PrepItem]] = {}
    for item in items:
        by_location.setdefault(item.location_id, []).append(item)

    groups: list[tuple[int | None, str, list[PrepItem]]] = []
    for loc in locations:
        if wanted_ids is not None and loc.id not in wanted_ids:
            continue
        rows = sorted(by_location.get(loc.id, []), key=lambda i: i.name.lower())
        if rows:
            groups.append((loc.id, loc.name, rows))

    if want_unassigned:
        orphans = sorted(by_location.get(None, []), key=lambda i: i.name.lower())
        if orphans:
            groups.append((None, UNASSIGNED, orphans))

    return groups


def _line(item: PrepItem) -> str:
    return f"{item.name} — {item.quantity}" if item.quantity else item.name


# ── Markdown ─────────────────────────────────────────────────────────────────


def render_markdown(groups, generated_on: date) -> str:
    """Checkbox syntax, so the list drops straight into Obsidian or a gist."""
    out = ["# Go List", "", f"_Generated {generated_on.isoformat()}_", ""]
    total = 0
    for _lid, name, items in groups:
        out.append(f"## {name}")
        out.append("")
        for item in items:
            out.append(f"- [ ] {_line(item)}")
            if item.notes:
                out.append(f"      {item.notes}")
            if item.next_refresh_date:
                out.append(f"      _Refresh: {item.next_refresh_date}_")
            total += 1
        out.append("")
    out.append(f"_{total} items across {len(groups)} locations._")
    return "\n".join(out) + "\n"


# ── CSV ──────────────────────────────────────────────────────────────────────


def render_csv(groups, generated_on: date) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["location", "name", "quantity", "notes", "next_refresh_date"])
    for _lid, name, items in groups:
        for item in items:
            writer.writerow(
                [
                    name,
                    item.name,
                    item.quantity or "",
                    item.notes or "",
                    item.next_refresh_date or "",
                ]
            )
    return buf.getvalue()


# ── PDF ──────────────────────────────────────────────────────────────────────


def _latin1(text: str) -> str:
    """Make a string safe for fpdf2's core fonts.

    fpdf2's built-in fonts are latin-1 only, so an em dash or a degree sign
    pasted into a notes field raises at render time — a bug that surfaces only
    for the one person who typed it, in the export they needed most. Common
    typographic characters are transliterated rather than replaced, so the
    output stays readable instead of filling with question marks.
    """
    replacements = {
        "—": "-",
        "–": "-",
        "‘": "'",
        "’": "'",
        "“": '"',
        "”": '"',
        "…": "...",
        "·": "-",
        "•": "-",
        "×": "x",
        " ": " ",
    }
    for bad, good in replacements.items():
        text = text.replace(bad, good)
    return text.encode("latin-1", "replace").decode("latin-1")


def render_pdf(groups, generated_on: date) -> bytes:
    from fpdf import FPDF

    pdf = FPDF(orientation="P", unit="mm", format="Letter")
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.add_page()

    pdf.set_font("Helvetica", "B", 20)
    pdf.cell(0, 10, _latin1("GO LIST"), new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(110, 110, 110)
    pdf.cell(
        0,
        6,
        _latin1(f"Generated {generated_on.isoformat()}"),
        new_x="LMARGIN",
        new_y="NEXT",
    )
    pdf.set_text_color(0, 0, 0)
    pdf.ln(4)

    total = 0
    for _lid, name, items in groups:
        # Keep a location heading with at least a couple of its rows.
        if pdf.get_y() > 240:
            pdf.add_page()

        pdf.set_font("Helvetica", "B", 12)
        pdf.set_fill_color(235, 235, 235)
        pdf.cell(
            0, 8, _latin1(f"  {name.upper()}"), new_x="LMARGIN", new_y="NEXT", fill=True
        )
        pdf.ln(2)

        for item in items:
            pdf.set_font("Helvetica", "", 11)
            pdf.cell(6, 6, _latin1("[ ]"))
            pdf.multi_cell(0, 6, _latin1(_line(item)), new_x="LMARGIN", new_y="NEXT")
            detail = []
            if item.notes:
                detail.append(item.notes)
            if item.next_refresh_date:
                detail.append(f"Refresh: {item.next_refresh_date}")
            if detail:
                pdf.set_font("Helvetica", "I", 8)
                pdf.set_text_color(110, 110, 110)
                pdf.set_x(pdf.l_margin + 6)
                pdf.multi_cell(
                    0, 4.5, _latin1(" | ".join(detail)), new_x="LMARGIN", new_y="NEXT"
                )
                pdf.set_text_color(0, 0, 0)
            total += 1
        pdf.ln(3)

    pdf.set_font("Helvetica", "I", 8)
    pdf.set_text_color(110, 110, 110)
    pdf.cell(0, 6, _latin1(f"{total} items across {len(groups)} locations."))

    return bytes(pdf.output())
