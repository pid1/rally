"""Daily family summary generator."""

import json
import os
import tomllib
from datetime import timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import recurring_ical_events
import requests
from icalendar import Calendar

from rally.database import SessionLocal, init_db
from rally.models import Calendar as CalendarModel
from rally.models import DashboardSnapshot, FamilyMember, Setting
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

        # Load config.toml as fallback (may not exist if using DB-only config)
        config_path = self.data_dir / "config.toml"
        try:
            with open(config_path, "rb") as f:
                self.config = tomllib.load(f)
        except FileNotFoundError:
            self.config = {}

        # Try loading settings from DB
        db_settings = {}
        try:
            db = SessionLocal()
            try:
                for s in db.query(Setting).all():
                    db_settings[s.key] = s.value
            finally:
                db.close()
        except Exception:
            pass

        # LLM provider setup — prefer DB settings, fall back to config.toml
        if "llm_provider" in db_settings:
            self.provider = db_settings["llm_provider"]
            if self.provider == "anthropic":
                import anthropic

                self.model = db_settings.get("llm_anthropic_model", "")
                self.client = anthropic.Anthropic(
                    api_key=db_settings.get("llm_anthropic_api_key", "")
                )
            else:
                from openai import OpenAI

                self.model = db_settings.get("llm_local_model", "")
                self.client = OpenAI(
                    base_url=db_settings.get("llm_local_base_url", ""),
                    api_key=db_settings.get("llm_local_api_key", "no-key-needed"),
                )
        else:
            llm_config = self.config["llm"]
            self.provider = llm_config.get("provider", "local")
            provider_config = llm_config.get(self.provider, {})
            self.model = provider_config["model"]

            if self.provider == "anthropic":
                import anthropic

                self.client = anthropic.Anthropic(api_key=provider_config["api_key"])
            else:
                from openai import OpenAI

                self.client = OpenAI(
                    base_url=provider_config["base_url"],
                    api_key=provider_config.get("api_key", "no-key-needed"),
                )

        # Get local timezone: DB setting > config.toml > UTC
        tz_name = db_settings.get("local_timezone", self.config.get("local_timezone", "UTC"))
        self.local_tz = ZoneInfo(tz_name)

        # Store DB settings for use by other methods
        self._db_settings = db_settings

        # Optional: owner emails for accurate declined-event detection (config.toml fallback only)
        self.calendar_owners = self.config.get("calendar_owners", {})

    def _is_event_declined(self, component, owner_email: str | None = None) -> bool:
        """Check if a calendar event has been declined.

        Uses multiple signals to detect declined events across providers:
        - Google Calendar: PARTSTAT on attendees
        - Apple iCloud: PARTSTAT on attendees
        - Outlook/Exchange: X-MICROSOFT-CDO-BUSYSTATUS property

        When owner_email is provided (via [calendar_owners] in config.toml),
        only that attendee's PARTSTAT is checked—this is the most accurate
        approach. Without it, the method falls back to conservative heuristics.
        """
        # STATUS=CANCELLED means the organizer cancelled the event
        status = component.get("status")
        if status and str(status).upper() == "CANCELLED":
            return True

        attendees = component.get("attendee")
        if not attendees:
            return False

        if not isinstance(attendees, list):
            attendees = [attendees]

        if owner_email:
            # Best path: check the specific calendar owner's PARTSTAT
            owner_email_lower = owner_email.strip().lower()
            for att in attendees:
                att_email = str(att).replace("mailto:", "").strip().lower()
                if att_email == owner_email_lower:
                    partstat = ""
                    if hasattr(att, "params"):
                        partstat = str(att.params.get("PARTSTAT", ""))
                    return partstat.upper() == "DECLINED"
            # Owner not found in attendees — they may be the organizer; not declined
            return False

        # --- No owner email: use conservative heuristics ---

        # Microsoft Outlook: X-MICROSOFT-CDO-BUSYSTATUS=FREE with declined attendees
        busystatus = component.get("X-MICROSOFT-CDO-BUSYSTATUS")
        if busystatus and str(busystatus).upper() == "FREE":
            has_declined = any(
                hasattr(att, "params") and str(att.params.get("PARTSTAT", "")).upper() == "DECLINED"
                for att in attendees
            )
            if has_declined:
                return True

        # If ALL attendees have declined, the event is effectively dead
        all_declined = all(
            hasattr(att, "params") and str(att.params.get("PARTSTAT", "")).upper() == "DECLINED"
            for att in attendees
        )
        if all_declined:
            return True

        return False

    def fetch_calendars(self) -> list[dict[str, list[dict]]]:
        """Download and parse ICS feeds, filtering for next 7 days of events.

        Uses recurring_ical_events to properly expand recurring events (RRULE),
        including modified/cancelled occurrences via RECURRENCE-ID and EXDATE.
        """
        today = today_utc()
        end_date = today + timedelta(days=7)

        # Try loading calendars from DB first
        db_calendars = []
        try:
            db = SessionLocal()
            try:
                db_calendars = (
                    db.query(CalendarModel, FamilyMember.name)
                    .join(FamilyMember, CalendarModel.family_member_id == FamilyMember.id)
                    .all()
                )
            finally:
                db.close()
        except Exception:
            pass

        calendars = []

        if db_calendars:
            # Use DB calendars
            cal_sources = [
                (f"{cal.label} ({member_name})", cal.url, cal.owner_email)
                for cal, member_name in db_calendars
            ]
        elif "calendars" in self.config:
            # Fall back to config.toml
            cal_sources = [
                (key, url, self.calendar_owners.get(key))
                for key, url in self.config["calendars"].items()
            ]
        else:
            cal_sources = []

        for name, url, owner_email in cal_sources:
            try:
                response = requests.get(url, timeout=10)
                response.raise_for_status()

                # Parse ICS data and expand recurring events
                cal = Calendar.from_ical(response.text)
                recurring_events = recurring_ical_events.of(cal).between(today, end_date)

                events = []
                for component in recurring_events:
                    dtstart = component.get("dtstart")
                    if not dtstart:
                        continue

                    # Skip declined / cancelled events
                    if self._is_event_declined(component, owner_email):
                        continue

                    # Get event date (handle both date and datetime objects)
                    event_date = dtstart.dt
                    if hasattr(event_date, "date"):
                        event_date = event_date.date()

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
                        time_str = dt.strftime("%I:%M %p %Z").lstrip("0")

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
                    calendars.append({"name": name, "events": events})

            except Exception as e:
                print(f"Error fetching/parsing {name}: {e}")

        return calendars

    def fetch_weather(self) -> dict | None:
        """Get weather from OpenWeather."""
        # Try DB settings first, fall back to config.toml
        if all(k in self._db_settings for k in ("weather_api_key", "weather_lat", "weather_lon")):
            api_key = self._db_settings["weather_api_key"]
            lat = float(self._db_settings["weather_lat"])
            lon = float(self._db_settings["weather_lon"])
        else:
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

    def load_family_members(self) -> dict[int, str]:
        """Load family members from database, returning id -> name mapping."""
        db = SessionLocal()
        try:
            from rally.models import FamilyMember

            members = db.query(FamilyMember).all()
            return {m.id: m.name for m in members}
        finally:
            db.close()

    def load_todos(self) -> str:
        """Load outstanding todos from database for LLM context."""

        db = SessionLocal()
        try:
            import re
            from datetime import datetime

            from rally.models import Todo

            # Only send incomplete todos to the LLM
            todos = (
                db.query(Todo)
                .filter(Todo.completed == False)  # noqa: E712
                .order_by(Todo.created_at.desc())
                .all()
            )

            if not todos:
                return "No todos currently active."

            # Load family members for assignee names
            members = self.load_family_members()

            # Format todos for LLM
            lines = []
            for todo in todos:
                line = f"{todo.title}"

                # Add assignee if present
                if todo.assigned_to and todo.assigned_to in members:
                    line += f" [Assigned to {members[todo.assigned_to]}]"

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
        """Load family context from DB settings, falling back to file."""
        if self._db_settings.get("family_context"):
            return self._db_settings["family_context"]
        return (self.data_dir / "context.txt").read_text()

    def load_voice(self) -> str:
        """Load agent voice profile from DB settings, falling back to file."""
        if self._db_settings.get("agent_voice"):
            return self._db_settings["agent_voice"]
        return (self.data_dir / "agent_voice.txt").read_text()

    def load_template(self) -> str:
        """Load HTML template."""
        # Template is in templates/ directory relative to project root
        # Path: generate.py -> generator/ -> rally/ -> src/ -> project_root/
        base_dir = Path(__file__).resolve().parent.parent.parent.parent
        return (base_dir / "templates" / "dashboard.html").read_text()

    def _call_llm(self, prompt: str) -> str:
        """Call the configured LLM provider and return the response text."""
        if self.provider == "anthropic":
            response = self.client.messages.create(
                model=self.model,
                max_tokens=4000,
                messages=[{"role": "user", "content": prompt}],
            )
            return response.content[0].text if response.content else ""
        else:
            response = self.client.chat.completions.create(
                model=self.model,
                max_tokens=4000,
                messages=[{"role": "user", "content": prompt}],
            )
            return response.choices[0].message.content if response.choices else ""

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
        family_members = self.load_family_members()
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

        # Store raw inputs for eval ground truth
        self._generation_context = {
            "cal_text": cal_text,
            "weather": str(weather),
            "todos": todos,
            "dinner_plans": dinner_plans,
            "family_members": ", ".join(family_members.values())
            if family_members
            else "No family members configured.",
        }

        today = now_utc().strftime("%A, %B %d, %Y")
        prompt = f"""You're creating content for a daily family summary for {today}.

AGENT VOICE:
{voice}

FAMILY CONTEXT:
{context}

FAMILY MEMBERS:
{", ".join(family_members.values()) if family_members else "No family members configured."}

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
  "greeting": "A short, friendly greeting or note about the day (1 sentence)",
  "weather_summary": "Weather overview with clothing recommendation (plain text, 1 sentence)",
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
6. Consider family routines and how everyone can support each other. When todos are assigned to specific people, mention them by name.
7. DINNER PREP: Only mention dinner prep in briefing if action is needed TODAY, TOMORROW, or the day after (within 48 hours). Don't mention prep for dinners 3+ days away.
8. The briefing should surface important things that need attention TODAY or VERY SOON (within 1-2 days)
9. If the weather is actively dangerous (snow, thunderstorms, or tornado risk) within the next 7 days, mention it.

Do NOT include any HTML in your response. Plain text only for all values."""

        try:
            response_text = self._call_llm(prompt)
            print(f"LLM response:\n{response_text}")

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

    def evaluate_summary(self, summary_data: dict) -> dict:
        """Evaluate generated summary quality using LLM-as-judge.

        Applies the four-part eval formula:
          1. Role — quality evaluator for a family command center
          2. Context — the generated summary + raw input data (ground truth)
          3. Goal — grade on groundedness, tone, actionability, completeness,
             and guideline adherence
          4. Terminology — specific definitions and few-shot examples for each
             dimension

        Returns dict with dimension scores (1-5), explanations, and overall
        pass/fail.
        """
        if not getattr(self, "_generation_context", None):
            return {"error": "No generation context available. Run generate_summary() first."}

        ctx = self._generation_context
        summary_json = json.dumps(summary_data, indent=2)

        eval_prompt = f"""You are a quality evaluator for Rally, a family command center.
Your job is to judge the quality of an AI-generated daily family summary by
comparing it against the raw input data that was available to the generator.

== GENERATED SUMMARY (to evaluate) ==
{summary_json}

== RAW INPUT DATA (ground truth) ==
CALENDAR EVENTS:
{ctx["cal_text"]}

WEATHER DATA:
{ctx["weather"]}

TODOS:
{ctx["todos"]}

DINNER PLANS:
{ctx["dinner_plans"]}

FAMILY MEMBERS:
{ctx["family_members"]}

== EVALUATION CRITERIA ==
Score each dimension from 1 (worst) to 5 (best).

1. GROUNDEDNESS (no hallucination)
Every claim in the summary — events, times, weather details, todos, dinner
plans — must be traceable to the raw input data above. The summary must not
invent events, fabricate weather conditions, or reference todos/plans that
don't exist in the input.
- Score 5: Every fact traces directly to input data. No invented details.
- Score 3: Minor embellishments or imprecise times, but no outright fabrications.
- Score 1: Contains fabricated events, wrong weather, or invented todos.

2. TONE
Rally's voice is encouraging, empowering, and action-oriented. It frames
challenges as opportunities, celebrates hard work, and helps the family feel
prepared — never overwhelmed, stressed, or burdened.
- Score 5: Consistently empowering. Challenges framed as opportunities.
- Score 3: Mostly positive but with flat or neutral phrasing.
- Score 1: Defeatist, stressful, or makes the day sound burdensome.

Few-shot examples for tone:
  GOOD (5): "You've got a full day ahead — let's make it count!"
  BAD  (1): "You have a lot of obligations today that will be difficult to manage."

3. ACTIONABILITY
The briefing and schedule should help the family take action. The briefing
surfaces only items needing attention today or very soon (1-2 days). Schedule
entries identify time gaps as opportunities for todos. Advice is specific.
- Score 5: Briefing highlights timely, actionable items. Specific advice.
- Score 3: Some actionable content but also vague or untimely items.
- Score 1: No actionable guidance. Generic filler.

Few-shot examples for actionability:
  GOOD (5): "The plumber is confirmed for 2-4 PM — great window to knock out the grocery run beforehand."
  BAD  (1): "You have some things to do."

4. COMPLETENESS
The summary covers all key events for today from the input calendars,
references todos (mentioning assignees by name when assigned), and integrates
weather and dinner plans where relevant.
- Score 5: All today's events present. Todos with assignees mentioned by name.
- Score 3: Most events covered but some missing. Partial todo/dinner integration.
- Score 1: Major events missing. Todos or dinner plans ignored entirely.

5. GUIDELINE ADHERENCE
The summary follows Rally's specific content rules:
- Schedule shows TODAY's events only, in chronological order
- Weather recommendation mentions clothing appropriate for today
- Dinner prep mentioned only if needed within 48 hours (not 3+ days away)
- No HTML in any values — plain text only
- JSON schema is correct (greeting, weather_summary, schedule array, briefing)
- Score 5: All rules followed perfectly.
- Score 3: Minor violations (e.g. slightly out of order, distant dinner prep mentioned).
- Score 1: Major violations (future events in today's schedule, HTML, wrong schema).

== RESPONSE FORMAT ==
Respond with ONLY a JSON object (no markdown fences):
{{{{
  "groundedness": {{{{"score": <1-5>, "explanation": "<1 sentence>"}}}},
  "tone": {{{{"score": <1-5>, "explanation": "<1 sentence>"}}}},
  "actionability": {{{{"score": <1-5>, "explanation": "<1 sentence>"}}}},
  "completeness": {{{{"score": <1-5>, "explanation": "<1 sentence>"}}}},
  "guideline_adherence": {{{{"score": <1-5>, "explanation": "<1 sentence>"}}}},
  "overall_score": <average of all scores rounded to 1 decimal>,
  "pass": <true if all scores >= 3 AND overall >= 3.5 else false>,
  "summary": "<1 sentence overall assessment>"
}}}}"""

        try:
            response_text = self._call_llm(eval_prompt)
            print(f"Eval response:\n{response_text}")

            try:
                return json.loads(response_text)
            except Exception:
                extracted = self._extract_json_object(response_text)
                if extracted is not None:
                    return extracted
                return {"error": "Failed to parse eval response", "raw": response_text}
        except Exception as e:
            return {"error": f"Eval failed: {e}"}

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


EVAL_DIMENSIONS = [
    "groundedness",
    "tone",
    "actionability",
    "completeness",
    "guideline_adherence",
]


def main():
    """Main entry point for scheduled generation."""
    # Ensure database is initialized
    init_db()

    generator = SummaryGenerator()
    data = generator.generate_summary()

    # Run LLM-as-judge eval (skip with RALLY_SKIP_EVAL=1)
    eval_result = None
    if not os.getenv("RALLY_SKIP_EVAL"):
        eval_result = generator.evaluate_summary(data)

        print(f"\n{'=' * 60}")
        print("EVAL RESULTS")
        print(f"{'=' * 60}")

        if "error" in eval_result:
            print(f"  Eval error: {eval_result['error']}")
        else:
            for dim in EVAL_DIMENSIONS:
                if dim in eval_result:
                    score = eval_result[dim]["score"]
                    expl = eval_result[dim]["explanation"]
                    label = dim.replace("_", " ").title()
                    print(f"  {label:25s} {score}/5  {expl}")
            overall = eval_result.get("overall_score", "N/A")
            passed = eval_result.get("pass", False)
            print(f"  {'Overall':25s} {overall}/5  {'PASS' if passed else 'FAIL'}")
            if eval_result.get("summary"):
                print(f"  {eval_result['summary']}")

        print(f"{'=' * 60}\n")

    # Attach eval results to snapshot data for persistence
    if eval_result:
        data["_eval"] = eval_result

    generator.save_snapshot(data)


if __name__ == "__main__":
    main()
