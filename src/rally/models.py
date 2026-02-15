"""Rally database models."""

from datetime import datetime

from sqlalchemy import JSON, Boolean, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from rally.database import Base
from rally.utils.timezone import now_utc


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
    completed: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(
        default=now_utc, onupdate=now_utc
    )


class DinnerPlan(Base):
    """Dinner plan model - meal plans by date."""

    __tablename__ = "dinner_plans"

    id: Mapped[int] = mapped_column(primary_key=True)
    date: Mapped[str] = mapped_column(String(10), unique=True)  # YYYY-MM-DD
    plan: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(
        default=now_utc, onupdate=now_utc
    )
