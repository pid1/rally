"""Rally database models."""

from datetime import datetime

from sqlalchemy import JSON, Boolean, Integer, String, Text
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
    """Calendar feed model — each calendar is linked to a family member."""

    __tablename__ = "calendars"

    id: Mapped[int] = mapped_column(primary_key=True)
    label: Mapped[str] = mapped_column(String(100))  # Display name, e.g. "Google Family"
    url: Mapped[str] = mapped_column(Text)  # ICS feed URL
    family_member_id: Mapped[int] = mapped_column(Integer)  # FK to family_members.id
    owner_email: Mapped[str | None] = mapped_column(
        String(200), nullable=True
    )  # For declined-event detection
    created_at: Mapped[datetime] = mapped_column(default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(default=now_utc, onupdate=now_utc)


class Setting(Base):
    """Key-value settings store."""

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(default=now_utc, onupdate=now_utc)


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
    completed: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(default=now_utc, onupdate=now_utc)


class RecurringTodo(Base):
    """Recurring todo template model."""

    __tablename__ = "recurring_todos"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    recurrence_type: Mapped[str] = mapped_column(String(20))  # daily, weekly, monthly
    recurrence_day: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )  # 0-6 for weekly, 1-31 for monthly
    assigned_to: Mapped[int | None] = mapped_column(Integer, nullable=True)
    has_due_date: Mapped[bool] = mapped_column(default=False)
    active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(default=now_utc, onupdate=now_utc)


class DinnerPlan(Base):
    """Dinner plan model - meal plans by date."""

    __tablename__ = "dinner_plans"

    id: Mapped[int] = mapped_column(primary_key=True)
    date: Mapped[str] = mapped_column(String(10))  # YYYY-MM-DD (multiple plans per date allowed)
    plan: Mapped[str] = mapped_column(Text)
    attendee_ids: Mapped[list | None] = mapped_column(
        JSON, nullable=True
    )  # JSON array of family_member IDs (who's eating)
    cook_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )  # FK to family_members.id (who's cooking)
    created_at: Mapped[datetime] = mapped_column(default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(default=now_utc, onupdate=now_utc)
