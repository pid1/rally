"""Daily family summary generator."""

import json
import os
import tomllib
from datetime import datetime, timedelta
from pathlib import Path

import anthropic
import requests
from icalendar import Calendar

from rally.database import SessionLocal, init_db
from rally.models import DashboardSnapshot


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

    def fetch_calendars(self) -> list[dict[str, list[dict]]]:
        """Download and parse ICS feeds, filtering for today's events."""
        today = datetime.now().date()
        tomorrow = today + timedelta(days=1)
        
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
                        
                        # Only include today's events
                        if event_date == today:
                            summary = str(component.get("summary", "Untitled Event"))
                            description = str(component.get("description", ""))
                            location = str(component.get("location", ""))
                            
                            # Format time if datetime available
                            time_str = ""
                            if hasattr(dtstart.dt, "strftime"):
                                time_str = dtstart.dt.strftime("%I:%M %p").lstrip("0")
                            
                            events.append({
                                "summary": summary,
                                "time": time_str,
                                "description": description,
                                "location": location,
                            })
                
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
        """Load todos - placeholder until database is implemented."""
        # TODO: Implement database integration
        return "No todos configured yet"

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

    def generate_summary(self) -> dict:
        """Generate the daily summary JSON data using Claude."""
        calendars = self.fetch_calendars()
        weather = self.fetch_weather()
        todos = self.load_todos()
        context = self.load_context()
        voice = self.load_voice()

        # Format calendars for prompt
        cal_text = ""
        if calendars:
            for cal in calendars:
                cal_text += f"\nCALENDAR: {cal['name']}\n"
                for event in cal['events']:
                    cal_text += f"  - {event['time']} {event['summary']}"
                    if event['location']:
                        cal_text += f" at {event['location']}"
                    if event['description']:
                        cal_text += f" ({event['description']})"
                    cal_text += "\n"
        else:
            cal_text = "No calendar events for today."

        today = datetime.now().strftime("%A, %B %d, %Y")
        prompt = f"""You're creating content for a daily family summary for {today}.

AGENT VOICE:
{voice}

FAMILY CONTEXT:
{context}

TODAY'S CALENDAR EVENTS (already filtered to today only, may have duplicates - dedupe them):
{cal_text}

WEATHER FORECAST:
{weather}

TODOS (sorted by priority, higher number = more important):
{todos}

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
  "heads_up": "Optional warnings or coordination notes. Empty string if nothing notable."
}}

Guidelines:
1. Deduplicate calendar events (same event in multiple calendars)
2. Schedule should be in chronological order
3. Identify time gaps as opportunities to tackle todos based on priority
4. Recommend clothing based on weather and activities
5. Warn in heads_up if weather affects plans(outdoor events, school pickup, etc.)
6. Consider family routines and how everyone can support each other

Do NOT include any HTML in your response. Plain text only for all values."""

        try:
            response = self.client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=4000,
                messages=[{"role": "user", "content": prompt}],
            )

            data = json.loads(response.content[0].text)
            return data
        except Exception as e:
            # Return error structure matching expected schema
            return {
                "greeting": "⚠️ Unable to generate today's summary.",
                "weather_summary": f"Error: {e}",
                "schedule": [],
                "heads_up": "The system will retry at the next scheduled interval.",
            }

    def save_snapshot(self, data: dict) -> None:
        """Save generated summary data to database."""
        db = SessionLocal()
        try:
            today = datetime.now().strftime("%Y-%m-%d")
            
            # Deactivate previous snapshots for today
            db.query(DashboardSnapshot).filter(
                DashboardSnapshot.date == today
            ).update({"is_active": False})
            
            # Create new snapshot
            snapshot = DashboardSnapshot(
                date=today,
                data=data,
                is_active=True,
            )
            db.add(snapshot)
            db.commit()
            print(f"Snapshot saved at {datetime.now()}")
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
