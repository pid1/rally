"""Rally database models."""

from datetime import datetime

from sqlalchemy import JSON, Boolean, CheckConstraint, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from rally.database import Base
from rally.utils.timezone import now_utc


class FamilyMember(Base):
    """Family member model."""

    __tablename__ = "family_members"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    color: Mapped[str] = mapped_column(String(7), default="#333333")  # Hex color for UI
    calendar_key: Mapped[str | None] = mapped_column(
        String(100), nullable=True
    )  # Deprecated, kept for migration compat
    created_at: Mapped[datetime] = mapped_column(default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(default=now_utc, onupdate=now_utc)


class Calendar(Base):
    """Calendar feed model — each calendar is linked to a family member.

    Supports three types:
    - ics: Public ICS feed URL (unauthenticated)
    - caldav_google: Google CalDAV via app-specific password
    - caldav_apple: Apple iCloud CalDAV via app-specific password
    """

    __tablename__ = "calendars"

    id: Mapped[int] = mapped_column(primary_key=True)
    label: Mapped[str] = mapped_column(String(100))  # Display name, e.g. "Google Family"
    url: Mapped[str] = mapped_column(Text)  # ICS feed URL or CalDAV server URL
    family_member_id: Mapped[int] = mapped_column(Integer)  # FK to family_members.id
    owner_email: Mapped[str | None] = mapped_column(
        String(200), nullable=True
    )  # For declined-event detection
    cal_type: Mapped[str] = mapped_column(
        String(20), default="ics"
    )  # ics, caldav_google, caldav_apple
    username: Mapped[str | None] = mapped_column(
        String(200), nullable=True
    )  # Email for CalDAV auth
    password: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )  # App-specific password for CalDAV
    created_at: Mapped[datetime] = mapped_column(default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(default=now_utc, onupdate=now_utc)


class Setting(Base):
    """Key-value settings store."""

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(default=now_utc, onupdate=now_utc)


class AISettingsHistory(Base):
    """Versioned snapshots of AI settings (agent_voice, family_context).

    A new row is inserted on every explicit save of either field. The active
    snapshot for each field is referenced from the settings table via the
    'current_agent_voice_history_id' / 'current_family_context_history_id'
    keys. Rollback re-points the reference and bumps last_used_at — no new
    row is inserted.
    """

    __tablename__ = "ai_settings_history"

    id: Mapped[int] = mapped_column(primary_key=True)
    field_name: Mapped[str] = mapped_column(
        String(50), index=True
    )  # 'agent_voice' or 'family_context'
    value: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(default=now_utc)
    last_used_at: Mapped[datetime] = mapped_column(
        default=now_utc
    )  # Bumped whenever this row becomes the active version (save or rollback)


class LLMSettingsHistory(Base):
    """Versioned snapshots of the coupled LLM provider + model configuration.

    A new row is inserted on every explicit save of the LLM settings, capturing
    the provider and its model together as one unit (value is a JSON object
    {"provider": ..., "model": ...}). The active snapshot is referenced from
    the settings table via the 'current_llm_config_history_id' key. Rollback
    re-points the reference and bumps last_used_at — no new row is inserted.
    """

    __tablename__ = "llm_settings_history"

    id: Mapped[int] = mapped_column(primary_key=True)
    field_name: Mapped[str] = mapped_column(
        String(50), index=True
    )  # Always 'llm_config' (kept for parity with ai_settings_history / future fields)
    value: Mapped[str] = mapped_column(Text)  # JSON: {"provider": ..., "model": ...}
    created_at: Mapped[datetime] = mapped_column(default=now_utc)
    last_used_at: Mapped[datetime] = mapped_column(
        default=now_utc
    )  # Bumped whenever this row becomes the active version (save or rollback)


class StemConceptHistory(Base):
    """History of STEM 'concept of the day' topics that have been used.

    One row per (title, used_on) usage. The generator loads concepts used within
    the last 60 days and instructs the LLM not to repeat those specific topics.
    """

    __tablename__ = "stem_concept_history"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200))  # Concept name as generated
    field: Mapped[str | None] = mapped_column(
        String(50), nullable=True
    )  # Science, Technology, Engineering, or Math
    used_on: Mapped[str] = mapped_column(String(10))  # YYYY-MM-DD (local date used)
    created_at: Mapped[datetime] = mapped_column(default=now_utc)


class DashboardSnapshot(Base):
    """Dashboard snapshot model - stores generated daily summary data."""

    __tablename__ = "dashboard_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    date: Mapped[str] = mapped_column(String(10))  # YYYY-MM-DD
    timestamp: Mapped[datetime] = mapped_column(default=now_utc)
    data: Mapped[dict] = mapped_column(JSON)  # Stores the JSON response from Claude
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(default=now_utc)


class Todo(Base):
    """Todo item model."""

    __tablename__ = "todos"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    due_date: Mapped[str | None] = mapped_column(String(10), nullable=True)  # YYYY-MM-DD
    assigned_to: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )  # FK to family_members.id
    recurring_todo_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )  # FK to recurring_todos.id
    remind_days_before: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )  # Days before due_date to start showing in LLM briefings
    completed: Mapped[bool] = mapped_column(default=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(default=now_utc, onupdate=now_utc)


class RecurringTodo(Base):
    """Recurring todo template model."""

    __tablename__ = "recurring_todos"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    recurrence_type: Mapped[str] = mapped_column(String(20))  # daily, weekly, monthly, custom
    recurrence_day: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )  # 0-6 for weekly, 1-31 for monthly
    custom_rule: Mapped[dict | None] = mapped_column(
        JSON, nullable=True
    )  # rule dict for custom recurrence type
    assigned_to: Mapped[int | None] = mapped_column(Integer, nullable=True)
    has_due_date: Mapped[bool] = mapped_column(default=False)
    remind_days_before: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )  # Days before due_date to start showing in LLM briefings
    last_generated_date: Mapped[str | None] = mapped_column(
        String(10), nullable=True
    )  # YYYY-MM-DD: recurrence date of the most recently generated instance
    active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(default=now_utc, onupdate=now_utc)


class DinnerPlan(Base):
    """Meal plan model - meal plans by date."""

    __tablename__ = "dinner_plans"

    id: Mapped[int] = mapped_column(primary_key=True)
    date: Mapped[str] = mapped_column(String(10))  # YYYY-MM-DD (multiple plans per date allowed)
    meal_type: Mapped[str] = mapped_column(
        String(20), default="Dinner"
    )  # Breakfast, Lunch, Dinner, Snacks
    plan: Mapped[str] = mapped_column(Text)
    attendee_ids: Mapped[list | None] = mapped_column(
        JSON, nullable=True
    )  # JSON array of family_member IDs (who's eating)
    cook_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )  # FK to family_members.id (who's cooking)
    rating: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )  # 1-5 star rating; null means not yet reviewed
    review: Mapped[str | None] = mapped_column(Text, nullable=True)  # Free-text review of the meal
    created_at: Mapped[datetime] = mapped_column(default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(default=now_utc, onupdate=now_utc)

    __table_args__ = (
        CheckConstraint(
            "rating IS NULL OR (rating >= 1 AND rating <= 5)", name="ck_dinner_plan_rating"
        ),
    )
