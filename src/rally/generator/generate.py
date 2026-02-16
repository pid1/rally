"""Daily family summary generator."""

import json
import os
import tomllib
from datetime import timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import anthropic
import requests
from icalendar import Calendar

from rally.database import SessionLocal, init_db
from rally.models import DashboardSnapshot
from rally.utils.timezone import ensure_utc, now_utc, today_utc


class SummaryGenerator:
    """Generate daily family summaries with calendar, weather, and todos."""

    def __init__(self):
        # Detect environment: production uses /data, development uses PWD
        env = os.getenv("RALLY_ENV", "development")
        self.is_production = env == "production"

        if self.is_production:
            self.data_dir = Path("/data")
            self.output_dir = Path("/output")
        else:
            self.data_dir = Path.cwd()
            self.output_dir = Path.cwd()

        config_path = self.data_dir / "config.toml"
        with open(config_path, "rb") as f:
            self.config = tomllib.load(f)
        self.client = anthropic.Anthropic(api_key=self.config["anthropic"]["api_key"])
        # Allow overriding model in config; fall back to a stable default
        self.model = self.config.get("anthropic", {}).get("model", "claude-sonnet-4-5-20250929")

        # Get local timezone from config (default to UTC)
        self.local_tz = ZoneInfo(self.config.get("local_timezone", "UTC"))

    def fetch_calendars(self) -> list[dict[str, list[dict]]]:
        """Download and parse ICS feeds, filtering for next 7 days of events."""
        today = today_utc()
        end_date = today + timedelta(days=7)

        calendars = []
        for key, url in self.config["calendars"].items():
            try:
                response = requests.get(url, timeout=10)
                response.raise_for_status()

                # Parse ICS data
                cal = Calendar.from_ical(response.text)
                events = []

                for component in cal.walk():
                    if component.name == "VEVENT":
                        dtstart = component.get("dtstart")
                        if not dtstart:
                            continue

                        # Get event date (handle both date and datetime objects)
                        event_date = dtstart.dt
                        if hasattr(event_date, "date"):
                            event_date = event_date.date()

                        # Only include events in the next 7 days
                        if today <= event_date < end_date:
                            summary = str(component.get("summary", "Untitled Event"))
                            description = str(component.get("description", ""))
                            location = str(component.get("location", ""))

                            # Format time if datetime available
                            time_str = ""
                            if hasattr(dtstart.dt, "strftime"):
                                # Convert to local timezone for display
                                dt = dtstart.dt
                                if hasattr(dt, "tzinfo") and dt.tzinfo is not None:
                                    # Ensure it's timezone-aware in UTC first, then convert to local
                                    dt = ensure_utc(dt).astimezone(self.local_tz)
                                time_str = dt.strftime("%I:%M %p").lstrip("0")

                            events.append(
                                {
                                    "summary": summary,
                                    "time": time_str,
                                    "date": event_date.strftime("%Y-%m-%d"),
                                    "description": description,
                                    "location": location,
                                }
                            )

                # Sort events by date and time
                events.sort(key=lambda e: (e["date"], e["time"]))

                if events:
                    calendars.append({"name": key, "events": events})

            except Exception as e:
                print(f"Error fetching/parsing {key}: {e}")

        return calendars

    def fetch_weather(self) -> dict | None:
        """Get weather from OpenWeather."""
        api_key = self.config["weather"]["api_key"]
        lat = self.config["weather"]["lat"]
        lon = self.config["weather"]["lon"]

        url = "https://api.openweathermap.org/data/3.0/onecall"
        params = {
            "lat": lat,
            "lon": lon,
            "appid": api_key,
            "units": "imperial",
            "exclude": "minutely,alerts",
        }

        try:
            response = requests.get(url, params=params, timeout=10)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            print(f"Error fetching weather: {e}")
            return None

    def load_todos(self) -> str:
        """Load todos from database for LLM context.

        Includes all incomplete todos and completed todos from last 24 hours.
        """

        db = SessionLocal()
        try:
            import re
            from datetime import datetime

            from rally.models import Todo

            # Get todos visible within 24-hour window
            cutoff = now_utc() - timedelta(hours=24)
            todos = (
                db.query(Todo)
                .filter((Todo.completed == False) | (Todo.updated_at > cutoff))  # noqa: E712
                .order_by(Todo.created_at.desc())
                .all()
            )

            if not todos:
                return "No todos currently active."

            # Format todos for LLM
            lines = []
            for todo in todos:
                status = " (completed)" if todo.completed else ""
                line = f"{todo.title}{status}"

                # Add due date if present
                if todo.due_date:
                    try:
                        date_obj = datetime.strptime(todo.due_date, "%Y-%m-%d")
                        day_name = date_obj.strftime("%A")
                        date_formatted = date_obj.strftime("%b %d")
                        line += f" [Due {day_name}, {date_formatted}]"
                    except ValueError:
                        line += f" [Due {todo.due_date}]"  # Fallback

                if todo.description:
                    # Look for dates in format YYYY-MM-DD in the description and add day of week
                    desc = todo.description
                    date_pattern = r"(\d{4}-\d{2}-\d{2})"
                    matches = re.finditer(date_pattern, desc)
                    for match in matches:
                        date_str = match.group(1)
                        try:
                            date_obj = datetime.strptime(date_str, "%Y-%m-%d")
                            day_name = date_obj.strftime("%A")
                            # Replace date with "date (DayName)"
                            desc = desc.replace(date_str, f"{date_str} ({day_name})")
                        except ValueError:
                            pass  # Skip invalid dates
                    line += f" - {desc}"
                lines.append(line)

            return "\n".join(lines)
        finally:
            db.close()

    def load_dinner_plans(self) -> str:
        """Load dinner plans for next 7 days from database for LLM context."""
        db = SessionLocal()
        try:
            from datetime import datetime

            from rally.models import DinnerPlan

            today = today_utc()

            # Get all dates in the range
            date_range = [(today + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(7)]

            # Get plans for next 7 days
            plans = (
                db.query(DinnerPlan)
                .filter(DinnerPlan.date.in_(date_range))
                .order_by(DinnerPlan.date.asc())
                .all()
            )

            if not plans:
                return "No dinner plans for the next 7 days."

            # Format plans for LLM
            lines = []
            for plan in plans:
                plan_date = datetime.strptime(plan.date, "%Y-%m-%d").date()
                days_away = (plan_date - today).days

                if days_away == 0:
                    day_label = "Tonight"
                elif days_away == 1:
                    day_label = "Tomorrow night"
                else:
                    day_label = f"{plan_date.strftime('%A')} ({plan_date.strftime('%b %d')})"

                lines.append(f"{day_label}: {plan.plan}")

            return "\n".join(lines)
        finally:
            db.close()

    def load_context(self) -> str:
        """Load family context."""
        return (self.data_dir / "context.txt").read_text()

    def load_voice(self) -> str:
        """Load agent voice profile."""
        return (self.data_dir / "agent_voice.txt").read_text()

    def load_template(self) -> str:
        """Load HTML template."""
        # Template is in templates/ directory relative to project root
        # Path: generate.py -> generator/ -> rally/ -> src/ -> project_root/
        base_dir = Path(__file__).resolve().parent.parent.parent.parent
        return (base_dir / "templates" / "dashboard.html").read_text()

    def _extract_json_object(self, text: str) -> dict | None:
        """Try to extract the first top-level JSON object from arbitrary text.

        Handles code fences and leading/trailing noise, and balances braces while
        being aware of strings and escapes.
        """
        import re

        if not text:
            return None

        # Strip common markdown fences if present
        text = text.strip()
        if text.startswith("```"):
            # Remove first fence line and possible language tag
            text = re.sub(r"^```[a-zA-Z0-9_-]*\n", "", text)
            # Remove closing fence if present
            text = re.sub(r"\n```\s*$", "", text)
            text = text.strip()

        # Find first '{'
        start = text.find("{")
        if start == -1:
            return None

        stack = 0
        in_str = False
        esc = False
        end = None
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
            else:
                if ch == '"':
                    in_str = True
                elif ch == "{":
                    stack += 1
                elif ch == "}":
                    stack -= 1
                    if stack == 0:
                        end = i + 1
                        break
        if end is None:
            return None

        candidate = text[start:end]
        try:
            return json.loads(candidate)
        except Exception:
            return None

    def generate_summary(self) -> dict:
        """Generate the daily summary JSON data using Claude."""
        calendars = self.fetch_calendars()
        weather = self.fetch_weather()
        todos = self.load_todos()
        dinner_plans = self.load_dinner_plans()
        context = self.load_context()
        voice = self.load_voice()

        # Format calendars for prompt
        cal_text = ""
        if calendars:
            from datetime import datetime

            for cal in calendars:
                cal_text += f"\nCALENDAR: {cal['name']}\n"
                current_date = None
                for event in cal["events"]:
                    # Group events by date for readability
                    if event["date"] != current_date:
                        current_date = event["date"]
                        cal_text += f"\n  {datetime.strptime(event['date'], '%Y-%m-%d').strftime('%A, %B %d')}:\n"

                    cal_text += f"    - {event['time']} {event['summary']}"
                    if event["location"]:
                        cal_text += f" at {event['location']}"
                    if event["description"]:
                        cal_text += f" ({event['description']})"
                    cal_text += "\n"
        else:
            cal_text = "No calendar events for the next 7 days."

        today = now_utc().strftime("%A, %B %d, %Y")
        prompt = f"""You're creating content for a daily family summary for {today}.

AGENT VOICE:
{voice}

FAMILY CONTEXT:
{context}

CALENDAR EVENTS (next 7 days, may have duplicates - dedupe them):
{cal_text}

WEATHER FORECAST (next 7 days from OpenWeather):
{weather}

TODOS:
{todos}

DINNER PLANS (next 7 days):
{dinner_plans}

Create content for a daily summary. Respond with ONLY a JSON object (no markdown fences) using this exact schema:
{{
  "greeting": "A short, friendly greeting or note about the day (1-2 sentences)",
  "weather_summary": "Weather overview with clothing recommendation (plain text, 2-3 sentences)",
  "schedule": [
    {{
      "time": "8:00 AM",
      "title": "Event name",
      "notes": "Optional context or suggestion (or empty string)"
    }}
  ],
  "briefing": "Optional warnings or coordination notes. Empty string if nothing notable."
}}

Guidelines:
1. Deduplicate calendar events (same event in multiple calendars)
2. Schedule should show TODAY'S events in chronological order
3. Identify time gaps as opportunities to tackle todos
4. Recommend clothing based on TODAY'S weather and activities
5. Look at the FULL 7-DAY weather forecast. If upcoming weather might affect plans (rain for outdoor events, extreme temps, etc.), mention it in the briefing
6. Check upcoming calendar events against weather. Suggest prep work if needed (umbrellas, warmer clothes, rescheduling outdoor activities)
7. Consider family routines and how everyone can support each other
8. DINNER PREP: Only mention dinner prep in briefing if action is needed TODAY, TOMORROW, or the day after (within 48 hours). Don't mention prep for dinners 3+ days away.
9. The briefing should surface important things that need attention TODAY or VERY SOON (within 1-2 days)

Do NOT include any HTML in your response. Plain text only for all values."""

        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=4000,
                messages=[{"role": "user", "content": prompt}],
            )

            # Get the response text
            response_text = response.content[0].text if getattr(response, "content", None) else ""
            print(f"Claude response (first 500 chars): {response_text[:500]}")

            # Try strict JSON first
            try:
                data = json.loads(response_text)
                return data
            except Exception:
                pass

            # Fallback: attempt to extract a JSON object from the text
            extracted = self._extract_json_object(response_text)
            if extracted is not None:
                return extracted

            # If all parsing fails, raise to outer handler
            raise json.JSONDecodeError(
                "Unable to parse JSON from Claude response", response_text or "", 0
            )
        except json.JSONDecodeError as e:
            print(f"JSON decode error: {e}")
            print(
                f"Response text: {response_text if 'response_text' in locals() else 'No response'}"
            )
            # Return error structure matching expected schema
            return {
                "greeting": "⚠️ Unable to generate today's summary.",
                "weather_summary": f"JSON Error: {e}",
                "schedule": [],
                "briefing": "The system will retry at the next scheduled interval.",
            }
        except Exception as e:
            print(f"General error: {e}")
            # Return error structure matching expected schema
            return {
                "greeting": "⚠️ Unable to generate today's summary.",
                "weather_summary": f"Error: {e}",
                "schedule": [],
                "briefing": "The system will retry at the next scheduled interval.",
            }

    def save_snapshot(self, data: dict) -> None:
        """Save generated summary data to database."""
        db = SessionLocal()
        try:
            today = today_utc().strftime("%Y-%m-%d")

            # Deactivate previous snapshots for today
            db.query(DashboardSnapshot).filter(DashboardSnapshot.date == today).update(
                {"is_active": False}
            )

            # Create new snapshot
            snapshot = DashboardSnapshot(
                date=today,
                data=data,
                is_active=True,
            )
            db.add(snapshot)
            db.commit()
            print(f"Snapshot saved at {now_utc()}")
        finally:
            db.close()


def main():
    """Main entry point for scheduled generation."""
    # Ensure database is initialized
    init_db()

    generator = SummaryGenerator()
    data = generator.generate_summary()
    generator.save_snapshot(data)


if __name__ == "__main__":
    main()
