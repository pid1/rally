"""Tests for the seed CLI: it populates sample data and is idempotent."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from rally import cli
from rally.database import Base
from rally.models import Calendar, DashboardSnapshot, DinnerPlan, FamilyMember, Setting, Todo
from rally.utils.timezone import today_utc

EXPECTED_COUNTS = {
    "family": 4,
    "calendars": 3,
    "settings": 5,
    "todos": 6,
    "dinner": 16,  # 6 upcoming + 10 past
    "snapshots": 1,
}


@pytest.fixture
def cli_db(monkeypatch):
    """Point cli.SessionLocal at an isolated in-memory DB and stub init_db."""
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(bind=engine)
    testing_session_local = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    monkeypatch.setattr(cli, "SessionLocal", testing_session_local)
    monkeypatch.setattr(cli, "init_db", lambda: None)
    session = testing_session_local()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def _counts(session):
    return {
        "family": session.query(FamilyMember).count(),
        "calendars": session.query(Calendar).count(),
        "settings": session.query(Setting).count(),
        "todos": session.query(Todo).count(),
        "dinner": session.query(DinnerPlan).count(),
        "snapshots": session.query(DashboardSnapshot).count(),
    }


def test_seed_populates_sample_data(cli_db):
    cli.seed()
    assert _counts(cli_db) == EXPECTED_COUNTS


def test_seed_creates_past_meals_for_history_filters(cli_db):
    """Seeded history must span all meal types and a range of ratings so the
    Previous Meals meal-type and rating filters have data to act on."""
    cli.seed()
    today = today_utc().strftime("%Y-%m-%d")

    past = cli_db.query(DinnerPlan).filter(DinnerPlan.date < today).all()

    assert past, "expected seeded meals in the past for the Previous Meals page"
    # All four meal types are represented.
    assert {p.meal_type for p in past} == {"Breakfast", "Lunch", "Dinner", "Snacks"}
    # A spread of ratings, including at least one unrated meal.
    ratings = {p.rating for p in past}
    assert None in ratings
    assert len([r for r in ratings if r is not None]) >= 3


def test_seed_is_idempotent(cli_db):
    cli.seed()
    cli.seed()
    # seed() clears before inserting, so a second run yields the same counts.
    assert _counts(cli_db) == EXPECTED_COUNTS


def test_seed_handles_error_and_rolls_back(cli_db, monkeypatch, capsys):
    def boom(*args, **kwargs):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(cli, "FamilyMember", boom)

    cli.seed()  # the error is caught, not raised

    assert "Error seeding" in capsys.readouterr().out
    # The aborted insert rolled back, so nothing was seeded.
    assert _counts(cli_db)["family"] == 0
