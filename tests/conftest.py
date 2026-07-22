"""Shared test fixtures: an isolated in-memory database and a test client.

Every test gets a fresh SQLite database that lives only in memory, wired into
the app by overriding the ``get_db`` dependency. The app's real database
(``rally.db``) is never touched: the ``get_db`` override replaces it for request
handling, and the ``TestClient`` is used without its lifespan context manager so
the startup ``init_db()`` (which would create tables on the real engine) never
runs.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from rally.database import Base, get_db
from rally.main import app


@pytest.fixture
def db_session():
    """A fresh in-memory database for a single test.

    ``StaticPool`` keeps every connection pointed at the same in-memory
    database, so rows a test seeds are visible to the request handlers (which
    each open their own session on the same engine).
    """
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    testing_session_local = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = testing_session_local()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


@pytest.fixture
def client(db_session: Session):
    """A ``TestClient`` whose ``get_db`` dependency uses the test database."""

    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()
