"""Daily family summary generator."""

import json
import os
import tomllib
from datetime import timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

from rally.calendars import collect_occurrences, default_window
from rally.database import SessionLocal, init_db
from rally.models import AISettingsHistory, DashboardSnapshot, FamilyMember, Setting
from rally.utils.timezone import now_utc, today_utc

# A specific STEM concept should not repeat within this many days. Different
# sub-topics within the same broader area are still allowed inside the window.
STEM_REPEAT_WINDOW_DAYS = 60

# Token budget for a single LLM call. On the Anthropic Messages API this cap
# covers thinking tokens *and* visible output, so a reasoning-heavy call can
# burn most of it before the JSON body is finished.
LLM_MAX_TOKENS = 4000


class LLMTruncatedError(Exception):
    """Raised when the model stopped because it exhausted the token budget.

    A truncated response is perfectly well-formed right up to the cut, so it
    fails JSON parsing exactly like malformed output would. Keeping this
    distinct from json.JSONDecodeError stops that misdiagnosis at the source.
    """


def _describe_usage(usage) -> str:
    """Render whichever token counters the provider reported, skipping the rest.

    Providers disagree on names (Anthropic ``input_tokens`` vs OpenAI
    ``prompt_tokens``) and stubs in the test suite report none at all, so every
    field is optional and the first name to supply a given label wins.
    """
    if usage is None:
        return "usage=unavailable"

    counters = (
        ("input_tokens", "input"),
        ("prompt_tokens", "input"),
        ("output_tokens", "output"),
        ("completion_tokens", "output"),
        ("total_tokens", "total"),
        ("cache_creation_input_tokens", "cache_write"),
        ("cache_read_input_tokens", "cache_read"),
    )

    parts: list[str] = []
    seen: set[str] = set()
    for attr, label in counters:
        value = getattr(usage, attr, None)
        if value is not None and label not in seen:
            seen.add(label)
            parts.append(f"{label}={value}")

    # OpenAI-compatible servers report reasoning tokens in a nested object;
    # Anthropic folds thinking into output_tokens and reports nothing here.
    details = getattr(usage, "completion_tokens_details", None)
    reasoning = getattr(details, "reasoning_tokens", None)
    if reasoning is not None:
        parts.append(f"reasoning={reasoning}")

    return ", ".join(parts) if parts else "usage=unavailable"


def _error_summary(detail: str) -> dict:
    """Placeholder summary rendered when generation fails, matching the schema."""
    return {
        "greeting": "⚠️ Unable to generate today's summary.",
        "weather_summary": detail,
        "schedule": [],
        "briefing": "The system will retry at the next scheduled interval.",
    }


class SummaryGenerator:
    """Generate daily family summaries with calendar, weather, and todos."""

    # Class-level defaults for the sports watchlist, so a generator assembled
    # without __init__ still has a defined optional-feature state. The pending
    # list is an empty tuple rather than a list: it is only ever rebound, never
    # mutated, so a shared mutable default cannot leak between instances.
    sports_watchlist_enabled = False
    _pending_sports_notices: tuple | list = ()

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

        # Token budget for this provider. Read after self.provider is set
        # above, since the resolution depends on which provider is active.
        self.max_tokens = self._resolve_max_tokens(db_settings)

        # Get local timezone: DB setting > config.toml > UTC
        tz_name = db_settings.get("local_timezone", self.config.get("local_timezone", "UTC"))
        self.local_tz_name = tz_name
        self.local_tz = ZoneInfo(tz_name)

        # Store DB settings for use by other methods
        self._db_settings = db_settings

        # Optional: STEM "concept of the day" for the family (toggle in Settings)
        self.stem_concept_enabled = db_settings.get("stem_concept_enabled", "false") == "true"

        # Optional: fold the open shopping list into the briefing (toggle in Settings)
        self.shopping_list_in_summary_enabled = (
            db_settings.get("shopping_list_in_summary_enabled", "false") == "true"
        )

        # Optional: 14-day TV and radio listings for followed teams (toggle in Settings)
        self.sports_watchlist_enabled = (
            db_settings.get("sports_watchlist_enabled", "false") == "true"
        )
        # Announcements pending for this run, recorded only once a summary is
        # actually produced — a failed generation must not silently consume a
        # "Coming up" mention.
        self._pending_sports_notices: list = []

        # Optional: owner emails for accurate declined-event detection (config.toml fallback only)
        self.calendar_owners = self.config.get("calendar_owners", {})

    def _resolve_max_tokens(self, db_settings: dict) -> int:
        """Resolve this provider's token budget: DB setting > that provider's
        own config.toml table (e.g. [llm.anthropic]) > the module default.

        Each provider owns its own settings key, so switching providers never
        carries one provider's budget onto the other.
        """
        max_tokens_key = (
            "llm_anthropic_max_tokens" if self.provider == "anthropic" else "llm_local_max_tokens"
        )
        if max_tokens_key in db_settings:
            return int(db_settings[max_tokens_key])
        provider_config = self.config.get("llm", {}).get(self.provider, {})
        return int(provider_config.get("max_tokens", LLM_MAX_TOKENS))

    def fetch_calendars(self):
        """Every calendar occurrence in the next 7 days, from every source.

        The work lives in ``rally.calendars`` now: Rally-owned events, ICS
        feeds and CalDAV accounts all normalise to the same ``Occurrence``
        shape, and merging, deduplicating and ordering happen once, there.
        Four bugs used to live in the code this replaced — string-sorted times,
        all-day events rendered as midnight, a dedupe key that dropped the
        second same-named event of a day, and a window measured in UTC dates.

        The window is measured in **local** dates, so "the next seven days"
        means what the family means by it.
        """
        db = SessionLocal()
        try:
            start_day, end_day = default_window(self.local_tz, days=7)
            result = collect_occurrences(
                db,
                start_day=start_day,
                end_day_exclusive=end_day,
                local_tz=self.local_tz,
                config=self.config,
            )
        except Exception as exc:
            print(f"Error loading calendars: {exc}")
            return []
        finally:
            db.close()

        for failure in result.failures:
            print(f"  Warning: calendar source unavailable: {failure}")
        return result.occurrences

    def _weather_url(self) -> str | None:
        """Resolve the configured NWS forecast URL (DB settings, then config.toml)."""
        url = self._db_settings.get("weather_nws_url")
        if not url:
            url = self.config.get("weather", {}).get("nws_url")
        return url or None

    def fetch_weather(self) -> str | None:
        """Fetch the raw NWS forecast (DWML XML) from the configured URL."""
        url = self._weather_url()
        if not url:
            print("No NWS forecast URL configured.")
            return None

        try:
            response = requests.get(
                url,
                timeout=10,
                headers={"User-Agent": "Rally family dashboard (https://github.com/pid1/rally)"},
            )
            response.raise_for_status()
            return response.text
        except Exception as e:
            print(f"Error fetching weather: {e}")
            return None

    def format_weather(self, weather: str | None) -> str:
        """Parse NWS DWML XML into clean, human-readable forecast text.

        The National Weather Service feed reports times already in the location's
        local timezone and includes plain-English worded forecasts, so the LLM
        sees unambiguous weather data without any unit or timezone conversion.
        """
        if not weather:
            return "No weather data available."

        import xml.etree.ElementTree as ET

        try:
            root = ET.fromstring(weather)
        except ET.ParseError as e:
            print(f"Error parsing weather XML: {e}")
            return "No weather data available."

        def text_of(el) -> str | None:
            return el.text.strip() if el is not None and el.text and el.text.strip() else None

        lines: list[str] = []

        # Current conditions
        current = root.find(".//data[@type='current observations']")
        if current is not None:
            params = current.find("parameters")
            if params is not None:
                temp = text_of(params.find("temperature/value"))
                conditions_el = params.find("weather/weather-conditions")
                summary = (
                    conditions_el.get("weather-summary") if conditions_el is not None else None
                )
                humidity = text_of(params.find("humidity/value"))

                parts = []
                if temp:
                    parts.append(f"{temp}°F")
                if summary:
                    parts.append(summary)
                if parts:
                    line = "Current conditions: " + ", ".join(parts)
                    if humidity:
                        line += f". Humidity {humidity}%"
                    lines.append(line)

        # Worded forecast (Today / Tonight / weekday labels paired with text)
        forecast = root.find(".//data[@type='forecast']")
        if forecast is not None:
            params = forecast.find("parameters")
            worded = params.find("wordedForecast") if params is not None else None
            if worded is not None:
                layout_key = worded.get("time-layout")
                period_names: list[str] = []
                for time_layout in forecast.findall("time-layout"):
                    key = time_layout.find("layout-key")
                    if key is not None and key.text == layout_key:
                        period_names = [
                            svt.get("period-name", "")
                            for svt in time_layout.findall("start-valid-time")
                        ]
                        break

                texts = [text_of(t) for t in worded.findall("text")]
                forecast_lines = []
                for i, txt in enumerate(texts):
                    if not txt:
                        continue
                    label = period_names[i] if i < len(period_names) and period_names[i] else None
                    forecast_lines.append(f"  {label}: {txt}" if label else f"  {txt}")

                if forecast_lines:
                    if lines:
                        lines.append("")
                    lines.append("Forecast:")
                    lines.extend(forecast_lines)

        if not lines:
            return "No weather data available."

        return "\n".join(lines)

    def load_family_members(self) -> dict[int, str]:
        """Load family members from database, returning id -> name mapping."""
        db = SessionLocal()
        try:
            members = db.query(FamilyMember).all()
            return {m.id: m.name for m in members}
        finally:
            db.close()

    def load_todos(self) -> str:
        """Load outstanding todos from database for LLM context.

        Respects the remind_days_before window: if a todo has a due_date and
        remind_days_before set, it is excluded until today >= due_date - remind_days_before.
        """

        db = SessionLocal()
        try:
            import re
            from datetime import datetime

            from rally.models import Todo

            today = now_utc().astimezone(self.local_tz).date()

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
                # Apply reminder window filter: skip tasks outside their window
                if todo.due_date and todo.remind_days_before is not None:
                    try:
                        due = datetime.strptime(todo.due_date, "%Y-%m-%d").date()
                        window_start = due - timedelta(days=todo.remind_days_before)
                        if today < window_start:
                            continue
                    except ValueError, TypeError, OverflowError:
                        pass  # If date is unparseable or calculation fails, include the todo

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

            if not lines:
                return "No todos currently active."

            return "\n".join(lines)
        finally:
            db.close()

    def load_dinner_plans(self) -> str:
        """Load meal plans for next 7 days from database for LLM context."""
        db = SessionLocal()
        try:
            from datetime import datetime

            from rally.models import DinnerPlan

            today = now_utc().astimezone(self.local_tz).date()

            # Get all dates in the range
            date_range = [(today + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(7)]

            # Get plans for next 7 days (multiple per date possible)
            plans = (
                db.query(DinnerPlan)
                .filter(DinnerPlan.date.in_(date_range))
                .order_by(DinnerPlan.date.asc(), DinnerPlan.id.asc())
                .all()
            )

            if not plans:
                return "No meal plans for the next 7 days."

            # Load family members for attendee/cook names
            members = self.load_family_members()

            # Format plans for LLM
            lines = []
            for plan in plans:
                plan_date = datetime.strptime(plan.date, "%Y-%m-%d").date()
                days_away = (plan_date - today).days
                meal_type = getattr(plan, "meal_type", "Dinner") or "Dinner"

                if days_away == 0:
                    day_label = f"Today ({meal_type})"
                elif days_away == 1:
                    day_label = f"Tomorrow ({meal_type})"
                else:
                    day_label = (
                        f"{plan_date.strftime('%A')} ({plan_date.strftime('%b %d')}) [{meal_type}]"
                    )

                line = f"{day_label}: {plan.plan}"

                # Annotate who's eating (omit if everyone / not specified)
                if plan.attendee_ids:
                    names = [members.get(mid, f"ID {mid}") for mid in plan.attendee_ids]
                    line += f" [Eating: {', '.join(names)}]"

                # Annotate who's cooking
                if plan.cook_id and plan.cook_id in members:
                    line += f" [Cook: {members[plan.cook_id]}]"

                lines.append(line)

            return "\n".join(lines)
        finally:
            db.close()

    def load_shopping_items(self) -> str:
        """Load open shopping items from database for LLM context.

        Open items only — a completed item is a purchase the family already
        made, and it must never reach the LLM as something still outstanding.
        Grouped under store names, with the catch-all rendered as "Anywhere"
        and ordered last, matching the /shopping page.
        """
        db = SessionLocal()
        try:
            from rally.models import ShoppingItem, ShoppingStore

            items = (
                db.query(ShoppingItem)
                .filter(ShoppingItem.completed == False)  # noqa: E712
                .order_by(ShoppingItem.created_at.desc())
                .all()
            )

            if not items:
                return "No shopping items currently active."

            store_names = {s.id: s.name for s in db.query(ShoppingStore).all()}

            groups: dict[str, list[str]] = {}
            for item in items:
                # An item whose store was deleted falls back to the catch-all.
                label = store_names.get(item.store_id) if item.store_id else None
                entry = item.name
                if item.note:
                    entry += f" - {item.note}"
                groups.setdefault(label or "Anywhere", []).append(entry)

            ordered = sorted(name for name in groups if name != "Anywhere")
            if "Anywhere" in groups:
                ordered.append("Anywhere")

            lines = []
            for label in ordered:
                lines.append(f"{label}:")
                lines.extend(f"  - {entry}" for entry in groups[label])

            return "\n".join(lines)
        finally:
            db.close()

    def load_sports_watchlist(self) -> str:
        """Build the "Tonight" and "Coming up" blocks for followed teams.

        Network failures are caught and returned as an empty section, the same
        shape as ``fetch_weather()``: a third-party outage must degrade to a
        missing section, never a failed summary. Six-to-thirteen HTTP calls run
        concurrently under one short overall budget so they cannot collectively
        delay the 4:00 AM run.
        """
        try:
            from rally.sports.watchlist import (
                build_sections,
                load_announced_keys,
                load_followed_teams,
            )

            db = SessionLocal()
            try:
                followed = load_followed_teams(db)
                if not followed:
                    return ""
                announced = load_announced_keys(db)
            finally:
                db.close()

            today = now_utc().astimezone(self.local_tz).date()
            section, notices = build_sections(followed, self.local_tz, today, announced)
            self._pending_sports_notices = notices
            return section
        except Exception as e:  # noqa: BLE001 — a provider outage is not a failed summary
            print(f"Error building sports watchlist: {e}")
            self._pending_sports_notices = []
            return ""

    def save_sports_notices(self) -> None:
        """Record the "Coming up" announcements this run actually made."""
        if not self._pending_sports_notices:
            return
        try:
            from rally.sports.watchlist import record_notices

            db = SessionLocal()
            try:
                today = now_utc().astimezone(self.local_tz).date()
                record_notices(db, self._pending_sports_notices, today)
                print(f"Recorded {len(self._pending_sports_notices)} sports event notices")
            finally:
                db.close()
        except Exception as e:  # noqa: BLE001
            print(f"Could not record sports event notices: {e}")
        finally:
            self._pending_sports_notices = ()

    def load_recent_stem_concepts(self) -> list[str]:
        """Load titles of STEM concepts used within the last 60 days (newest first).

        These are injected into the generation prompt as a "do not repeat" list.
        A specific topic older than the window is allowed to recur, so it drops
        off the list. Returns an empty list if none are in-window or the history
        table is not available.
        """
        try:
            today = now_utc().astimezone(self.local_tz).date()
            cutoff = (today - timedelta(days=STEM_REPEAT_WINDOW_DAYS)).strftime("%Y-%m-%d")

            db = SessionLocal()
            try:
                from rally.models import StemConceptHistory

                # used_on is an ISO YYYY-MM-DD string, so lexicographic >= is a date compare
                rows = (
                    db.query(StemConceptHistory.title)
                    .filter(StemConceptHistory.used_on >= cutoff)
                    .order_by(StemConceptHistory.id.desc())
                    .all()
                )
                # De-duplicate titles case-insensitively while preserving order
                seen: set[str] = set()
                titles: list[str] = []
                for (title,) in rows:
                    if not title:
                        continue
                    key = title.strip().lower()
                    if key in seen:
                        continue
                    seen.add(key)
                    titles.append(title)
                return titles
            finally:
                db.close()
        except Exception as e:
            print(f"Could not load STEM concept history: {e}")
            return []

    def save_stem_concept(self, concept: dict | None) -> None:
        """Record a used STEM concept in history.

        Deduplicated by (title, used_on) so regenerating the same day doesn't add
        duplicate rows, but the same topic used again on a later date (after the
        60-day window, when the LLM is allowed to reuse it) records a fresh row.
        A no-op when the concept is missing or has no title.
        """
        if not isinstance(concept, dict):
            return
        title = str(concept.get("title", "")).strip()
        if not title:
            return

        try:
            from sqlalchemy import func

            used_on = now_utc().astimezone(self.local_tz).strftime("%Y-%m-%d")

            db = SessionLocal()
            try:
                from rally.models import StemConceptHistory

                existing = (
                    db.query(StemConceptHistory)
                    .filter(
                        func.lower(StemConceptHistory.title) == title.lower(),
                        StemConceptHistory.used_on == used_on,
                    )
                    .first()
                )
                if existing:
                    return

                field = str(concept.get("field", "")).strip() or None
                db.add(StemConceptHistory(title=title, field=field, used_on=used_on))
                db.commit()
                print(f"Recorded STEM concept in history: {title}")
            finally:
                db.close()
        except Exception as e:
            print(f"Could not record STEM concept history: {e}")

    def _load_ai_setting(self, field_name: str) -> str | None:
        """Resolve the active AI setting value via its history pointer in settings."""
        pointer = self._db_settings.get(f"current_{field_name}_history_id")
        if pointer:
            try:
                db = SessionLocal()
                try:
                    row = db.get(AISettingsHistory, int(pointer))
                    if row and row.value:
                        return row.value
                finally:
                    db.close()
            except Exception:
                pass
        # Pre-migration fallback: value stored directly in the settings table
        return self._db_settings.get(field_name)

    def load_home_location(self) -> str:
        """The family's home location, or an empty string when unset."""
        return (self._db_settings.get("home_location") or "").strip()

    def load_context(self) -> str:
        """Load family context from DB settings, falling back to file."""
        value = self._load_ai_setting("family_context")
        if value:
            return value
        return (self.data_dir / "context.txt").read_text()

    def load_voice(self) -> str:
        """Load agent voice profile from DB settings, falling back to file."""
        value = self._load_ai_setting("agent_voice")
        if value:
            return value
        return (self.data_dir / "agent_voice.txt").read_text()

    def load_template(self) -> str:
        """Load HTML template."""
        # Template is in templates/ directory relative to project root
        # Path: generate.py -> generator/ -> rally/ -> src/ -> project_root/
        base_dir = Path(__file__).resolve().parent.parent.parent.parent
        return (base_dir / "templates" / "dashboard.html").read_text()

    def _call_llm(
        self, user_prompt: str, system_prompt: str | None = None, label: str = "llm"
    ) -> str:
        """Call the configured LLM provider and return the response text.

        When a system_prompt is provided it is sent as a separate system message.
        For Anthropic, prompt caching is enabled on the system block so that
        static content (voice, context, guidelines) is cached across calls.

        The stop reason and token usage are logged on every call, successful or
        not, so budget headroom is visible before it becomes an outage. ``label``
        distinguishes the callers in those log lines.

        Raises:
            LLMTruncatedError: the model ran out of token budget mid-response.
        """
        if self.provider == "anthropic":
            kwargs: dict = {
                "model": self.model,
                "max_tokens": self.max_tokens,
                "messages": [{"role": "user", "content": user_prompt}],
            }
            if system_prompt:
                kwargs["system"] = [
                    {
                        "type": "text",
                        "text": system_prompt,
                        "cache_control": {"type": "ephemeral"},
                    }
                ]
            # Streaming, rather than a plain create() call: the SDK refuses a
            # non-streaming request whose max_tokens could exceed its timeout
            # guard, which a "Model maximum" budget (up to a model's full
            # output ceiling) can trip. get_final_message() returns the same
            # Message shape create() did, so nothing below this call changed.
            with self.client.messages.stream(**kwargs) as stream:
                response = stream.get_final_message()

            usage = getattr(response, "usage", None)
            stop_reason = getattr(response, "stop_reason", None)
            print(
                f"[{label}] stop_reason={stop_reason} max_tokens={self.max_tokens} "
                f"{_describe_usage(usage)}"
            )
            if stop_reason == "max_tokens":
                raise LLMTruncatedError(
                    f"LLM response truncated: stop_reason=max_tokens, "
                    f"output_tokens={getattr(usage, 'output_tokens', 'unknown')}, "
                    f"max_tokens={self.max_tokens}. Thinking tokens share this budget — "
                    f"raise the configured Max Tokens or shorten the prompt."
                )

            # Newer models may return thinking blocks before the text block,
            # so filter by type instead of assuming content[0] is text.
            return "".join(b.text for b in response.content if b.type == "text")
        else:
            messages = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            messages.append({"role": "user", "content": user_prompt})
            response = self.client.chat.completions.create(
                model=self.model,
                max_tokens=self.max_tokens,
                messages=messages,
            )

            choices = response.choices
            usage = getattr(response, "usage", None)
            # OpenAI-compatible servers signal budget exhaustion as "length".
            finish_reason = getattr(choices[0], "finish_reason", None) if choices else None
            print(
                f"[{label}] finish_reason={finish_reason} max_tokens={self.max_tokens} "
                f"{_describe_usage(usage)}"
            )
            if finish_reason == "length":
                raise LLMTruncatedError(
                    f"LLM response truncated: finish_reason=length, "
                    f"completion_tokens={getattr(usage, 'completion_tokens', 'unknown')}, "
                    f"max_tokens={self.max_tokens}. Reasoning tokens share this budget — "
                    f"raise the configured Max Tokens or shorten the prompt."
                )

            return choices[0].message.content if choices else ""

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

    def format_calendar_section(self, occurrences) -> str:
        """Render merged occurrences as the CALENDAR block of the prompt.

        Grouped by local date rather than by feed. The old format led with
        "CALENDAR: Jon's Google" headings, which is an implementation detail of
        where an event was stored — the family thinks in days, and so does the
        model when it plans one.

        End times and an explicit "all day" marker are new here, and both were
        previously impossible: the shape that reached this function had one
        preformatted clock string and nothing else.
        """
        if not occurrences:
            return "No calendar events for the next 7 days."

        from datetime import datetime

        by_date: dict[str, list] = {}
        for occurrence in occurrences:
            by_date.setdefault(occurrence.start_local_date, []).append(occurrence)

        lines: list[str] = []
        for day in sorted(by_date):
            heading = datetime.strptime(day, "%Y-%m-%d").strftime("%A, %B %d")
            lines.append(f"\n  {heading}:")
            for occurrence in by_date[day]:
                if occurrence.all_day:
                    when = "All day"
                else:
                    when = occurrence.time_label(self.local_tz)
                    end_label = occurrence.local_end(self.local_tz).strftime("%I:%M %p").lstrip("0")
                    if end_label != when:
                        when = f"{when}-{end_label}"

                row = f"    - {when} {occurrence.title}"
                if occurrence.location:
                    row += f" at {occurrence.location}"
                if occurrence.description:
                    row += f" ({occurrence.description})"
                if occurrence.spans_days():
                    row += f" [Through {occurrence.end_local_date}]"
                if len(occurrence.attendees) > 1:
                    row += f" [Attending: {', '.join(occurrence.attendees)}]"
                elif occurrence.attendees:
                    row += f" [{occurrence.attendees[0]}]"
                lines.append(row)

        return "\n".join(lines)

    def generate_summary(self) -> dict:
        """Generate the daily summary JSON data using Claude."""
        calendars = self.fetch_calendars()
        weather = self.fetch_weather()
        family_members = self.load_family_members()
        todos = self.load_todos()
        dinner_plans = self.load_dinner_plans()
        context = self.load_context()
        voice = self.load_voice()
        home = self.load_home_location()
        # Only queried when the feature is on — an empty section would burn
        # tokens and invite the model to reference a list that isn't there.
        shopping_items = self.load_shopping_items() if self.shopping_list_in_summary_enabled else ""
        sports_watchlist = self.load_sports_watchlist() if self.sports_watchlist_enabled else ""

        cal_text = self.format_calendar_section(calendars)

        # Format weather into clean local-time text
        weather_text = self.format_weather(weather)

        # Store raw inputs for eval ground truth
        self._generation_context = {
            "cal_text": cal_text,
            "weather": weather_text,
            "todos": todos,
            "dinner_plans": dinner_plans,
            "shopping_items": shopping_items,
            "home_location": home,
            "family_members": ", ".join(family_members.values())
            if family_members
            else "No family members configured.",
        }

        today = now_utc().astimezone(self.local_tz).strftime("%A, %B %d, %Y")

        # Human-readable label for the family's configured timezone. Any bare
        # time in FAMILY CONTEXT (e.g. "7PM") is assumed to be in this zone, so
        # the LLM must not silently reinterpret it as UTC or anything else.
        local_now = now_utc().astimezone(self.local_tz)
        tz_abbrev = local_now.strftime("%Z")
        tz_offset = local_now.strftime("%z")
        if tz_offset:
            tz_offset = f"UTC{tz_offset[:3]}:{tz_offset[3:]}"
        tz_label = self.local_tz_name
        tz_detail = ", ".join(part for part in (tz_abbrev, tz_offset) if part)
        if tz_detail:
            tz_label = f"{self.local_tz_name} (currently {tz_detail})"

        # Optional sections. Each appends its guideline body (no leading number)
        # to optional_guidelines; they are numbered sequentially from 11 below,
        # so no combination of toggles can collide or leave a gap.
        optional_guidelines: list[str] = []

        # Optional STEM "concept of the day" — schema block + guideline, only when enabled
        stem_schema = ""
        if self.stem_concept_enabled:
            stem_schema = """,
  "stem_concept": {
    "title": "Short name of the concept (e.g. 'Buoyancy' or 'Patterns')",
    "field": "One of: Science, Technology, Engineering, Math",
    "explanation": "A warm, plain-language explanation the whole family can understand (1-2 sentences)",
    "activities": [
      {
        "audience": "Who it's for (e.g. 'Ages 4-6', 'Older kids', or a child's name)",
        "idea": "One super-easy, low-prep way to explore the concept during what the family is ALREADY doing today"
      }
    ]
  }"""
            optional_guidelines.append("""STEM CONCEPT OF THE DAY: Include a "stem_concept" object with one simple, everyday STEM concept the family can notice or play with today.
    - Tailor the activities to the ages of the children described in FAMILY CONTEXT. If ages aren't clear, give one idea for younger kids and one for older kids.
    - Every idea MUST be SUPER EASY to fold into what the family is already doing today (a meal, an errand, the weather, a scheduled activity, play or bath time). No special supplies, no extra trips — just a few minutes and a question or observation.
    - Keep it playful, curious, and encouraging — a fun bonus, not homework. Pick a concept that connects naturally to today's schedule, weather, or dinner when possible.
    - DO NOT reuse any of the specific concepts listed under STEM CONCEPTS USED RECENTLY. Exploring a DIFFERENT sub-topic within the same broader area (e.g. a new idea in "weather" or "fractions") is fine — only the specific topics on that list are off-limits.""")

        # Optional shopping list — guideline only when the section is present
        if self.shopping_list_in_summary_enabled:
            optional_guidelines.append(
                "SHOPPING LIST FILTERING: The SHOPPING LIST section below is pre-filtered to open "
                "items. Only mention shopping items that explicitly appear in that section. Do not "
                'infer, recall, or invent items. If it says "No shopping items currently active," '
                "do not suggest any specific items."
            )

        # Optional sports watchlist — guideline only when the section is present
        if self.sports_watchlist_enabled and sports_watchlist:
            optional_guidelines.append(
                "SPORTS WATCHLIST: The SPORTS section below lists every game and race the "
                "family can watch or listen to. Sports schedules are exactly the kind of "
                "content you have strong, confident, wrong priors about, so treat that "
                "section as the ONLY source of truth. Only mention games, opponents, dates, "
                "times, channels and stations that appear VERBATIM in that section. Do NOT "
                "infer, recall, or invent any matchup, broadcaster, score, standing or "
                'result. If a channel reads "channel TBD", say it is not announced yet — '
                "never guess a network. Never state or imply the outcome of any game, past "
                'or future. If the section says "Nothing on tonight," do not suggest '
                "anything is on."
            )

        optional_guideline_text = "".join(
            f"\n{number}. {body}" for number, body in enumerate(optional_guidelines, start=11)
        )

        # An unset home location omits the whole block rather than sending an
        # empty one: a labelled section with nothing after it invites the model
        # to fill it in.
        home_block = f"\nHOME:\nThe family lives in {home}.\n" if home else ""

        # Static content → system prompt (cached by Anthropic, system role for local models)
        system_prompt = f"""You are creating content for a daily family summary.

AGENT VOICE:
{voice}

FAMILY CONTEXT:
{context}
{home_block}
TIMEZONE:
The family's local timezone is {tz_label}. Every time written in FAMILY CONTEXT
(and anywhere else without an explicit zone) is ALREADY in this local timezone.
Reproduce those times EXACTLY as written — do NOT shift, convert, or reinterpret
them. A bare "7PM" is 7:00 PM local and MUST appear in the schedule as 7:00 PM,
never 2:00 PM or any other shifted value. Treat these times as UTC only if the
text explicitly says so (e.g. "7PM UTC" or "2100 Eastern"); in that one case,
convert into the family's local timezone before using it.

Respond with ONLY a JSON object (no markdown fences) using this exact schema:
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
  "briefing": "Optional warnings or coordination notes. Empty string if nothing notable."{stem_schema}
}}

Guidelines:
1. EVENT ATTRIBUTION: Each calendar is labeled with its owner's name. Attribute events to the calendar owner. If an event is tagged [Attending: X, Y], it means multiple family members are attending — mention all of them, not just the calendar owner.
2. Schedule should show TODAY'S events in chronological order
3. Identify time gaps as opportunities to tackle todos
4. Recommend clothing based on TODAY'S weather and activities
6. Consider family routines and how everyone can support each other. When todos are assigned to specific people, mention them by name.
7. DINNER PREP: Only mention dinner prep in briefing if action is needed TODAY, TOMORROW, or the day after (within 48 hours). Don't mention prep for dinners 3+ days away.
8. The briefing should surface important things that need attention TODAY or VERY SOON (within 1-2 days)
9. If the weather is actively dangerous (snow, thunderstorms, or tornado risk) within the next 7 days, mention it.
10. TASK FILTERING: The TODOS section below is pre-filtered. Only mention, reference, or suggest tasks that explicitly appear in the TODOS section. Do not infer, recall, or invent tasks that are not listed. If the TODOS section says "No todos currently active," do not suggest any specific tasks.
5. LOCAL TIMES: Times in FAMILY CONTEXT are already in the family's local timezone (see TIMEZONE above). Copy them through unchanged — a "7PM" reminder is scheduled at 7:00 PM, not a converted time. Never apply a UTC/local offset to a bare time.{optional_guideline_text}

Do NOT include any HTML in your response. Plain text only for all values."""

        # Build the "avoid repeats" block from STEM concept history (dynamic → user prompt)
        stem_avoid_block = ""
        if self.stem_concept_enabled:
            recent_concepts = self.load_recent_stem_concepts()
            if recent_concepts:
                joined = "\n".join(f"- {t}" for t in recent_concepts)
                stem_avoid_block = (
                    f"\n\nSTEM CONCEPTS USED RECENTLY (within the last {STEM_REPEAT_WINDOW_DAYS} "
                    "days — do NOT reuse any of these specific topics; a different sub-topic in "
                    "the same broader area is fine):\n"
                    f"{joined}"
                )

        shopping_section = ""
        if self.shopping_list_in_summary_enabled:
            shopping_section = f"\n\nSHOPPING LIST (open items):\n{shopping_items}"

        sports_section = ""
        if self.sports_watchlist_enabled and sports_watchlist:
            sports_section = f"\n\nSPORTS (followed teams — TV and radio):\n{sports_watchlist}"

        # Dynamic content → user prompt (changes every generation)
        user_prompt = f"""Create a daily family summary for {today}.

FAMILY MEMBERS:
{", ".join(family_members.values()) if family_members else "No family members configured."}

CALENDAR EVENTS (next 7 days, deduplicated — attribute events to the calendar owner):
{cal_text}

WEATHER FORECAST:
{weather_text}

TODOS:
{todos}

DINNER PLANS (next 7 days):
{dinner_plans}{shopping_section}{sports_section}{stem_avoid_block}"""

        try:
            response_text = self._call_llm(
                user_prompt, system_prompt=system_prompt, label="summary"
            )
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
        # Ordered before the JSONDecodeError handler on purpose: a truncated
        # response is a budget problem, not a formatting one, and reporting it
        # as the latter is what made this failure mode so hard to diagnose.
        except LLMTruncatedError as e:
            print(f"LLM truncation error: {e}")
            print(
                f"Response text: {response_text if 'response_text' in locals() else 'No response'}"
            )
            return _error_summary(f"Truncation Error: {e}")
        except json.JSONDecodeError as e:
            print(f"JSON decode error: {e}")
            print(
                f"Response text: {response_text if 'response_text' in locals() else 'No response'}"
            )
            return _error_summary(f"JSON Error: {e}")
        except Exception as e:
            print(f"General error: {e}")
            return _error_summary(f"Error: {e}")

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

        # When the STEM concept feature is on, that field is intentionally
        # generative and must not be judged against the raw input data.
        stem_eval_note = ""
        if self.stem_concept_enabled:
            stem_eval_note = (
                '\n\nNOTE: The optional "stem_concept" field is intentionally generative '
                "educational content. Do NOT penalize groundedness or completeness for it — "
                "it is not expected to trace to the raw input data."
            )

        # Static evaluation criteria → system prompt (cached / system role)
        eval_system = (
            """You are a quality evaluator for Rally, a family command center.
Your job is to judge the quality of an AI-generated daily family summary by
comparing it against the raw input data that was available to the generator.

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
{
  "groundedness": {"score": <1-5>, "explanation": "<1 sentence>"},
  "tone": {"score": <1-5>, "explanation": "<1 sentence>"},
  "actionability": {"score": <1-5>, "explanation": "<1 sentence>"},
  "completeness": {"score": <1-5>, "explanation": "<1 sentence>"},
  "guideline_adherence": {"score": <1-5>, "explanation": "<1 sentence>"},
  "overall_score": <average of all scores rounded to 1 decimal>,
  "pass": <true if all scores >= 3 AND overall >= 3.5 else false>,
  "summary": "<1 sentence overall assessment>"
}"""
            + stem_eval_note
        )

        # The shopping list is ground truth too, so groundedness can be judged
        # against it. Omitted entirely when the feature is off, matching the
        # generation prompt.
        shopping_ground_truth = ""
        if ctx.get("shopping_items"):
            shopping_ground_truth = f"\n\nSHOPPING LIST:\n{ctx['shopping_items']}"

        # Dynamic data → user prompt
        eval_user = f"""== GENERATED SUMMARY (to evaluate) ==
{summary_json}

== RAW INPUT DATA (ground truth) ==
CALENDAR EVENTS:
{ctx["cal_text"]}

WEATHER DATA:
{ctx["weather"]}

TODOS:
{ctx["todos"]}

DINNER PLANS:
{ctx["dinner_plans"]}{shopping_ground_truth}

FAMILY MEMBERS:
{ctx["family_members"]}"""

        try:
            response_text = self._call_llm(eval_user, system_prompt=eval_system, label="eval")
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

        # Record the STEM concept (if any) so future generations don't repeat it
        self.save_stem_concept(data.get("stem_concept"))

        # Record the "Coming up" announcements, so a notable event is mentioned
        # once rather than in all fourteen summaries leading up to it. Done here
        # rather than at build time so a failed generation doesn't consume one.
        self.save_sports_notices()


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
