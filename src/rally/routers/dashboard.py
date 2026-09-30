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
from rally.schemas import (
    DashboardResponse,
    DashboardScheduleItem,
    DashboardStemConcept,
)
from rally.templating import templates
from rally.utils.settings import today_local_str
from rally.utils.timezone import ensure_utc, now_utc

router = APIRouter(tags=["dashboard"])


def _build_stem_section(stem: dict | None) -> str:
    """Render the optional STEM 'concept of the day' card, or '' when absent.

    The LLM is instructed to emit plain text, but values are HTML-escaped here
    to guarantee the dashboard never renders injected markup.
    """
    from html import escape

    if not isinstance(stem, dict):
        return ""

    title = str(stem.get("title", "")).strip()
    if not title:
        return ""

    field = str(stem.get("field", "")).strip()
    explanation = str(stem.get("explanation", "")).strip()

    activities_html = ""
    for activity in stem.get("activities", []) or []:
        if not isinstance(activity, dict):
            continue
        idea = str(activity.get("idea", "")).strip()
        if not idea:
            continue
        audience = str(activity.get("audience", "")).strip()
        audience_html = (
            f'<span class="stem-audience">{escape(audience)}</span> ' if audience else ""
        )
        activities_html += f"<li>{audience_html}{escape(idea)}</li>"

    parts = ['<section class="card">']
    parts.append('<div class="card-header">STEM Concept of the Day</div>')
    parts.append('<div class="card-content">')
    if field:
        parts.append(f'<div class="stem-field">{escape(field)}</div>')
    parts.append(f'<div class="stem-title">{escape(title)}</div>')
    if explanation:
        parts.append(f"<p>{escape(explanation)}</p>")
    if activities_html:
        parts.append(f'<ul class="stem-activities">{activities_html}</ul>')
    parts.append("</div></section>")
    return "".join(parts)


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


def _dashboard_context(data: dict, date_str: str, timestamp: datetime, note_html: str = "") -> dict:
    """Build the template context for dashboard.html from snapshot data.

    Every HTML-bearing value is wrapped in ``Markup`` so Jinja's autoescaping
    leaves it as it was inserted before the Dashboard moved onto Jinja: the
    schedule, briefing, note and STEM snippets are built as HTML here, and the
    greeting and weather summary have always been inserted verbatim.
    """
    # Ensure timestamp is timezone-aware and in UTC
    timestamp_utc_dt = ensure_utc(timestamp)

    # Format as both human-readable (fallback) and ISO UTC (for JS parsing)
    timestamp_str = timestamp_utc_dt.strftime("%Y-%m-%d %I:%M %p")
    timestamp_utc = timestamp_utc_dt.strftime("%Y-%m-%dT%H:%M:%SZ")  # ISO 8601 UTC

    # Build schedule HTML
    schedule_html = ""
    for item in data.get("schedule", []):
        notes = f'<div class="schedule-notes">{item["notes"]}</div>' if item.get("notes") else ""
        schedule_html += (
            f'<div class="schedule-item">'
            f'<div class="schedule-time">{item["time"]}</div>'
            f'<div class="schedule-title">{item["title"]}</div>'
            f"{notes}"
            f"</div>"
        )

    if not schedule_html:
        schedule_html = "<p>No events scheduled today.</p>"

    # Build optional briefing section
    briefing = data.get("briefing", "")
    if briefing:
        briefing_section = (
            f'<div class="briefing"><div class="briefing-title">The Briefing</div>{briefing}</div>'
        )
    else:
        briefing_section = ""

    return {
        "date": date_str,
        "greeting": Markup(data.get("greeting", "")),
        "weather_summary": Markup(data.get("weather_summary", "")),
        "schedule": Markup(schedule_html),
        "briefing_section": Markup(briefing_section),
        # Optional STEM "concept of the day" card
        "stem_section": Markup(_build_stem_section(data.get("stem_concept"))),
        "note_section": Markup(_build_note_section(note_html)),
        "timestamp": timestamp_str,  # Fallback for non-JS browsers
        "timestamp_utc": timestamp_utc,  # For JS timezone conversion
    }


def _todays_note(db: Session) -> Note | None:
    return db.query(Note).filter(Note.date == today_local_str(db)).first()


def _latest_snapshot(db: Session) -> DashboardSnapshot | None:
    """The most recent active snapshot, regardless of date.

    Snapshots are generated in the family's local timezone (e.g. 4 AM Central)
    but viewed against UTC-based dates, so filtering on "today" would miss
    them for part of every day.
    """
    return (
        db.query(DashboardSnapshot)
        .filter(DashboardSnapshot.is_active == True)  # noqa: E712
        .order_by(DashboardSnapshot.timestamp.desc())
        .first()
    )


@router.get("/dashboard", response_class=HTMLResponse)
async def get_dashboard(request: Request, db: Session = Depends(get_db)):
    """Serve the generated daily dashboard from cached snapshot.

    The Daily Note is the one card **not** taken from the snapshot: it is read
    live on every request, so a note added or corrected during the day appears
    on the next load rather than waiting for the next generation.
    """
    note = _todays_note(db)
    note_html = markdown.render(note.body) if note else ""
    snapshot = _latest_snapshot(db)

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


@router.get("/api/dashboard/regenerate")
async def regenerate_dashboard():
    """Trigger dashboard regeneration."""
    generator = SummaryGenerator()
    data = generator.generate_summary()
    generator.save_snapshot(data)
    return {"status": "success", "message": "Dashboard regenerated"}


def _stem_from_snapshot(stem: object) -> DashboardStemConcept | None:
    """Normalize the LLM's ``stem_concept`` the way ``_build_stem_section`` does.

    A model can return anything, so entries that are not shaped like an
    activity are dropped rather than failing the whole response.
    """
    if not isinstance(stem, dict) or not str(stem.get("title", "")).strip():
        return None
    activities = [
        {"idea": str(a["idea"]).strip(), "audience": str(a.get("audience", "")).strip()}
        for a in stem.get("activities") or []
        if isinstance(a, dict) and str(a.get("idea", "")).strip()
    ]
    return DashboardStemConcept(
        title=str(stem["title"]).strip(),
        field=str(stem.get("field", "")).strip(),
        explanation=str(stem.get("explanation", "")).strip(),
        activities=activities,
    )


@router.get("/api/dashboard", response_model=DashboardResponse)
async def get_dashboard_data(db: Session = Depends(get_db)):
    """The dashboard as JSON, for clients that render it themselves.

    Same sources as ``/dashboard``: the cached snapshot, plus today's Daily
    Note read live. Never generates anything — that costs an LLM call.
    """
    note = _todays_note(db)
    snapshot = _latest_snapshot(db)
    if not snapshot:
        return DashboardResponse(
            has_snapshot=False,
            greeting="No dashboard data available for today.",
            weather_summary=(
                "Run the 'generate' command to create today's dashboard, or wait for the "
                "scheduled generation at 4:00 AM Central."
            ),
            note=note,
        )

    data = snapshot.data or {}
    schedule = [
        DashboardScheduleItem(
            time=str(item.get("time", "")),
            title=str(item.get("title", "")),
            notes=str(item.get("notes") or ""),
        )
        for item in data.get("schedule", [])
        if isinstance(item, dict)
    ]
    return DashboardResponse(
        has_snapshot=True,
        generated_at=ensure_utc(snapshot.timestamp),
        greeting=str(data.get("greeting", "")),
        weather_summary=str(data.get("weather_summary", "")),
        schedule=schedule,
        briefing=str(data.get("briefing", "")),
        stem_concept=_stem_from_snapshot(data.get("stem_concept")),
        note=note,
    )
