"""Dashboard router for Rally."""

from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from rally.database import get_db
from rally.generator.generate import SummaryGenerator
from rally.models import DashboardSnapshot
from rally.utils.timezone import ensure_utc, now_utc, today_utc

router = APIRouter(tags=["dashboard"])

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


def _render_html(data: dict, date_str: str, timestamp: datetime) -> str:
    """Render snapshot data into HTML template."""
    base_dir = Path(__file__).resolve().parent.parent.parent.parent
    template_path = base_dir / "templates" / "dashboard.html"
    template = template_path.read_text()

    # Ensure timestamp is timezone-aware and in UTC
    timestamp_utc_dt = ensure_utc(timestamp)

    # Format as both human-readable (fallback) and ISO UTC (for JS parsing)
    timestamp_str = timestamp_utc_dt.strftime("%Y-%m-%d %I:%M %p")
    timestamp_utc = timestamp_utc_dt.strftime("%Y-%m-%dT%H:%M:%SZ")  # ISO 8601 UTC

    # Build schedule HTML
    schedule_html = ""
    for item in data.get("schedule", []):
        notes = (
            f'<div class="schedule-notes">{item["notes"]}</div>'
            if item.get("notes")
            else ""
        )
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
            '<div class="briefing">'
            '<div class="briefing-title">The Briefing</div>'
            f'{briefing}'
            "</div>"
        )
    else:
        briefing_section = ""

    html = template.replace("{{date}}", date_str)
    html = html.replace("{{greeting}}", data.get("greeting", ""))
    html = html.replace("{{weather_summary}}", data.get("weather_summary", ""))
    html = html.replace("{{schedule}}", schedule_html)
    html = html.replace("{{briefing_section}}", briefing_section)
    html = html.replace("{{timestamp}}", timestamp_str)  # Fallback for non-JS browsers
    html = html.replace("{{timestamp_utc}}", timestamp_utc)  # For JS timezone conversion
    return html


@router.get("/dashboard", response_class=HTMLResponse)
async def get_dashboard(request: Request, db: Session = Depends(get_db)):
    """Serve the generated daily dashboard from cached snapshot."""
    today = today_utc().strftime("%Y-%m-%d")

    # Fetch today's active snapshot
    snapshot = (
        db.query(DashboardSnapshot)
        .filter(DashboardSnapshot.date == today, DashboardSnapshot.is_active == True)  # noqa: E712
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
        html_content = _render_html(error_data, date_str, now_utc())
    else:
        # Render from cached data
        date_str = now_utc().strftime("%A, %B %d, %Y")
        html_content = _render_html(snapshot.data, date_str, snapshot.timestamp)

    return HTMLResponse(content=html_content)


@router.get("/api/dashboard/regenerate")
async def regenerate_dashboard():
    """Trigger dashboard regeneration."""
    generator = SummaryGenerator()
    data = generator.generate_summary()
    generator.save_snapshot(data)
    return {"status": "success", "message": "Dashboard regenerated"}
