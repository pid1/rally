"""Dashboard router for Rally."""

from datetime import datetime

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from markupsafe import Markup
from sqlalchemy.orm import Session

from rally import markdown
from rally.database import get_db
from rally.generator.generate import SummaryGenerator
from rally.models import DashboardSnapshot, Note
from rally.templating import templates
from rally.utils.settings import today_local_str
from rally.utils.timezone import ensure_utc, now_utc

router = APIRouter(tags=["dashboard"])


def _build_stem_section(stem: dict | None) -> Markup:
    """Render the optional STEM 'concept of the day' card, or '' when absent.

    The LLM is instructed to emit plain text, but every value is HTML-escaped
    here (``Markup.format`` escapes its arguments) to guarantee the dashboard
    never renders injected markup.
    """
    if not isinstance(stem, dict):
        return Markup("")

    title = str(stem.get("title", "")).strip()
    if not title:
        return Markup("")

    field = str(stem.get("field", "")).strip()
    explanation = str(stem.get("explanation", "")).strip()

    activities = []
    for activity in stem.get("activities", []) or []:
        if not isinstance(activity, dict):
            continue
        idea = str(activity.get("idea", "")).strip()
        if not idea:
            continue
        audience = str(activity.get("audience", "")).strip()
        audience_html = (
            Markup('<span class="stem-audience">{}</span> ').format(audience)
            if audience
            else Markup("")
        )
        activities.append(Markup("<li>{}{}</li>").format(audience_html, idea))

    parts = [Markup('<section class="card">')]
    parts.append(Markup('<div class="card-header">STEM Concept of the Day</div>'))
    parts.append(Markup('<div class="card-content">'))
    if field:
        parts.append(Markup('<div class="stem-field">{}</div>').format(field))
    parts.append(Markup('<div class="stem-title">{}</div>').format(title))
    if explanation:
        parts.append(Markup("<p>{}</p>").format(explanation))
    if activities:
        parts.append(
            Markup('<ul class="stem-activities">{}</ul>').format(Markup("").join(activities))
        )
    parts.append(Markup("</div></section>"))
    return Markup("").join(parts)


def _build_note_section(note_html: str) -> str:
    """Render today's Daily Note card, or '' when there is no note.

    ``note_html`` has already been through ``rally.markdown``, whose renderer
    escapes any tag it is handed; the API also refuses to store markup in the
    first place. Nothing further is escaped here, because doing so would show
    the family the HTML of their own bold text.
    """
    if not note_html:
        return ""
    return (
        '<section class="card">'
        '<div class="card-header">Daily Note</div>'
        f'<div class="card-content">{note_html}</div>'
        "</section>"
    )


def _build_schedule(schedule: list) -> Markup:
    """Render today's schedule items, escaping every value the LLM produced.

    Titles and notes come from calendar feeds, sports schedules and the
    family's own entries by way of the LLM, so none of it is trusted as HTML.
    """
    items = []
    for item in schedule:
        notes = (
            Markup('<div class="schedule-notes">{}</div>').format(item["notes"])
            if item.get("notes")
            else Markup("")
        )
        items.append(
            Markup(
                '<div class="schedule-item">'
                '<div class="schedule-time">{}</div>'
                '<div class="schedule-title">{}</div>'
                "{}"
                "</div>"
            ).format(item["time"], item["title"], notes)
        )
    if not items:
        return Markup("<p>No events scheduled today.</p>")
    return Markup("").join(items)


def _dashboard_context(data: dict, date_str: str, timestamp: datetime, note_html: str = "") -> dict:
    """Build the template context for dashboard.html from snapshot data.

    The schedule, briefing, note and STEM snippets are built here as ``Markup``
    from literal templates, with every snapshot value escaped on the way in.
    The greeting and weather summary are plain text and left to Jinja's
    autoescaping. The snapshot is LLM output over calendar feeds and other
    outside text, and the prompt asks for plain text, so none of it is
    inserted as HTML.
    """
    # Ensure timestamp is timezone-aware and in UTC
    timestamp_utc_dt = ensure_utc(timestamp)

    # Format as both human-readable (fallback) and ISO UTC (for JS parsing)
    timestamp_str = timestamp_utc_dt.strftime("%Y-%m-%d %I:%M %p")
    timestamp_utc = timestamp_utc_dt.strftime("%Y-%m-%dT%H:%M:%SZ")  # ISO 8601 UTC

    # Build optional briefing section
    briefing = data.get("briefing", "")
    if briefing:
        briefing_section = Markup(
            '<div class="briefing"><div class="briefing-title">The Briefing</div>{}</div>'
        ).format(briefing)
    else:
        briefing_section = Markup("")

    return {
        "date": date_str,
        "greeting": data.get("greeting", ""),
        "weather_summary": data.get("weather_summary", ""),
        "schedule": _build_schedule(data.get("schedule", [])),
        "briefing_section": briefing_section,
        # Optional STEM "concept of the day" card
        "stem_section": _build_stem_section(data.get("stem_concept")),
        "note_section": Markup(_build_note_section(note_html)),
        "timestamp": timestamp_str,  # Fallback for non-JS browsers
        "timestamp_utc": timestamp_utc,  # For JS timezone conversion
    }


@router.get("/dashboard", response_class=HTMLResponse)
async def get_dashboard(request: Request, db: Session = Depends(get_db)):
    """Serve the generated daily dashboard from cached snapshot.

    The Daily Note is the one card **not** taken from the snapshot: it is read
    live on every request, so a note added or corrected during the day appears
    on the next load rather than waiting for the next generation.
    """
    note = db.query(Note).filter(Note.date == today_local_str(db)).first()
    note_html = markdown.render(note.body) if note else ""

    # Fetch the most recent active snapshot (regardless of date)
    # This handles timezone differences between when snapshots are generated
    # (in local timezone, e.g., 4 AM Central) vs when they're viewed (UTC-based)
    snapshot = (
        db.query(DashboardSnapshot)
        .filter(DashboardSnapshot.is_active == True)  # noqa: E712
        .order_by(DashboardSnapshot.timestamp.desc())
        .first()
    )

    if not snapshot:
        # No snapshot exists - show error message
        error_data = {
            "greeting": "No dashboard data available for today.",
            "weather_summary": "Run the 'generate' command to create today's dashboard, or wait for the scheduled generation at 4:00 AM Central.",
            "schedule": [],
            "briefing": "",
        }
        date_str = now_utc().strftime("%A, %B %d, %Y")
        context = _dashboard_context(error_data, date_str, now_utc(), note_html)
    else:
        # Render from cached data
        date_str = now_utc().strftime("%A, %B %d, %Y")
        context = _dashboard_context(snapshot.data, date_str, snapshot.timestamp, note_html)

    return templates.TemplateResponse(request, "dashboard.html", context)


@router.get("/api/dashboard/summary")
def get_dashboard_summary(db: Session = Depends(get_db)):
    """Today's generated summary and the live Daily Note, as data.

    The same content the dashboard page renders, for API and MCP callers. The
    summary is written by the LLM from the family's calendars — including
    external feeds — so callers should treat its prose as untrusted.
    """
    note = db.query(Note).filter(Note.date == today_local_str(db)).first()
    snapshot = (
        db.query(DashboardSnapshot)
        .filter(DashboardSnapshot.is_active == True)  # noqa: E712
        .order_by(DashboardSnapshot.timestamp.desc())
        .first()
    )
    return {
        "date": today_local_str(db),
        "generated_at": ensure_utc(snapshot.timestamp).isoformat() if snapshot else None,
        "summary": snapshot.data if snapshot else None,
        "daily_note": note.body if note else None,
    }


@router.get("/api/dashboard/regenerate")
async def regenerate_dashboard():
    """Trigger dashboard regeneration."""
    generator = SummaryGenerator()
    data = generator.generate_summary()
    generator.save_snapshot(data)
    return {"status": "success", "message": "Dashboard regenerated"}
