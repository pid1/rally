"""Shared test fixtures.

Provides an isolated in-memory database and test client, seed factories for the
core models, a deterministic-time fixture, a timezone-setting fixture, and
fixtures that stub the external boundaries (HTTP, LLM clients, CalDAV) so no
test ever touches the network.

The app's real database (``rally.db``) is never touched: the ``get_db`` override
replaces it for request handling, and the ``TestClient`` is used without its
lifespan context manager so the startup ``init_db()`` (which would create tables
on the real engine) never runs.
"""

import importlib
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from rally.database import Base, get_db
from rally.main import app
from rally.models import DinnerPlan, FamilyMember, RecurringTodo, Setting, Todo

# --- Database + client ---------------------------------------------------------


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


# --- Seed factories ------------------------------------------------------------
#
# Each returns a callable so a test can seed several rows with per-call overrides,
# e.g. `make_todo("Buy milk", completed_at=PAST)`. All commit and refresh so the
# returned instance has its primary key populated.


@pytest.fixture
def make_member(db_session: Session):
    def _make(name: str = "Alex", **kwargs) -> FamilyMember:
        member = FamilyMember(name=name, **kwargs)
        db_session.add(member)
        db_session.commit()
        db_session.refresh(member)
        return member

    return _make


@pytest.fixture
def make_todo(db_session: Session):
    def _make(
        title: str = "A task",
        *,
        description: str | None = None,
        completed: bool = True,
        completed_at: datetime | None = datetime(2020, 1, 1, 12, 0, 0),
        assigned_to: int | None = None,
        due_date: str | None = None,
        created_at: datetime | None = None,
        **kwargs,
    ) -> Todo:
        todo = Todo(
            title=title,
            description=description,
            completed=completed,
            completed_at=completed_at,
            assigned_to=assigned_to,
            due_date=due_date,
            **kwargs,
        )
        if created_at is not None:
            todo.created_at = created_at
        db_session.add(todo)
        db_session.commit()
        db_session.refresh(todo)
        return todo

    return _make


@pytest.fixture
def make_recurring_todo(db_session: Session):
    def _make(
        title: str = "Recurring task",
        *,
        recurrence_type: str = "daily",
        recurrence_day: int | None = None,
        custom_rule: dict | None = None,
        active: bool = True,
        **kwargs,
    ) -> RecurringTodo:
        rt = RecurringTodo(
            title=title,
            recurrence_type=recurrence_type,
            recurrence_day=recurrence_day,
            custom_rule=custom_rule,
            active=active,
            **kwargs,
        )
        db_session.add(rt)
        db_session.commit()
        db_session.refresh(rt)
        return rt

    return _make


@pytest.fixture
def make_dinner_plan(db_session: Session):
    def _make(
        date: str = "2026-01-01",
        *,
        plan: str = "Pasta",
        meal_type: str = "Dinner",
        rating: int | None = None,
        **kwargs,
    ) -> DinnerPlan:
        dp = DinnerPlan(date=date, plan=plan, meal_type=meal_type, rating=rating, **kwargs)
        db_session.add(dp)
        db_session.commit()
        db_session.refresh(dp)
        return dp

    return _make


@pytest.fixture
def make_setting(db_session: Session):
    def _make(key: str, value: str) -> Setting:
        row = db_session.get(Setting, key)
        if row is None:
            row = Setting(key=key, value=value)
            db_session.add(row)
        else:
            row.value = value
        db_session.commit()
        return row

    return _make


# --- Deterministic time --------------------------------------------------------
#
# `today_utc()`/`today_local()` resolve `now_utc` from the timezone module at call
# time, so patching the canonical function covers them transitively. Modules that
# did `from rally.utils.timezone import now_utc` and *call it directly* each hold
# their own binding, so those must be patched individually. (Module-level column
# defaults like `default=now_utc` capture the function at class-definition time
# and are not affected — seed timestamps explicitly when they matter.)
_NOW_UTC_IMPORTERS = (
    "rally.routers.recurring_todos",
    "rally.routers.todos",
    "rally.routers.dashboard",
    "rally.routers.settings",
    "rally.generator.generate",
)


@pytest.fixture
def frozen_now(monkeypatch):
    """Freeze `now_utc` to a chosen instant for the duration of a test.

    Returns a callable: ``frozen_now(datetime(2026, 7, 22, 12, tzinfo=UTC))``.
    A naive datetime is assumed to be UTC.
    """
    import rally.utils.timezone as tz

    def freeze(instant: datetime) -> datetime:
        if instant.tzinfo is None:
            instant = instant.replace(tzinfo=UTC)

        def fake_now_utc() -> datetime:
            return instant

        monkeypatch.setattr(tz, "now_utc", fake_now_utc)
        for name in _NOW_UTC_IMPORTERS:
            module = importlib.import_module(name)
            if getattr(module, "now_utc", None) is not None:
                monkeypatch.setattr(module, "now_utc", fake_now_utc)
        return instant

    return freeze


@pytest.fixture
def local_timezone(make_setting):
    """Set the ``local_timezone`` setting; returns a setter.

    Defaults to a non-UTC zone so timezone-dependent code paths are actually
    exercised rather than collapsing to the UTC no-op.
    """

    def set_tz(tz_name: str = "America/Chicago") -> str:
        make_setting("local_timezone", tz_name)
        return tz_name

    return set_tz


# --- External boundary stubs ---------------------------------------------------
#
# All LLM/HTTP/CalDAV calls are reached via lazy in-function imports, so patching
# the module attribute (requests.get, anthropic.Anthropic, openai.OpenAI,
# caldav.DAVClient) intercepts them regardless of how the caller imported them.


class FakeResponse:
    """Minimal stand-in for a ``requests`` response."""

    def __init__(self, *, text: str = "", status_code: int = 200, json_data=None):
        self.text = text
        self.status_code = status_code
        self._json = json_data

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests

            raise requests.HTTPError(f"HTTP {self.status_code}")


@pytest.fixture
def mock_requests(monkeypatch):
    """Stub ``requests.get``. Configure via ``.set_response(...)`` or
    ``.set_handler(fn)``; inspect ``.calls``."""
    import requests

    calls: list[dict] = []
    holder = {"response": FakeResponse()}

    def fake_get(url, *args, **kwargs):
        calls.append({"url": url, "kwargs": kwargs})
        resp = holder["response"]
        return resp(url, *args, **kwargs) if callable(resp) else resp

    monkeypatch.setattr(requests, "get", fake_get)

    class MockRequests:
        def __init__(self):
            self.calls = calls

        def set_response(self, **kwargs):
            holder["response"] = FakeResponse(**kwargs)

        def set_handler(self, fn):
            holder["response"] = fn

    return MockRequests()


@pytest.fixture
def mock_llm(monkeypatch):
    """Stub the ``anthropic`` and ``openai`` client classes so no LLM is called.

    Both fakes record their ``create`` kwargs in ``.calls`` and return a canned
    completion shaped like the real client responses.
    """
    import anthropic
    import openai

    calls: list[tuple] = []

    class FakeMessages:
        def create(self, **kwargs):
            calls.append(("anthropic", kwargs))
            return SimpleNamespace(content=[SimpleNamespace(text="ok")])

    class FakeAnthropic:
        def __init__(self, **kwargs):
            self.init_kwargs = kwargs
            self.messages = FakeMessages()

    class FakeCompletions:
        def create(self, **kwargs):
            calls.append(("openai", kwargs))
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))])

    class FakeOpenAI:
        def __init__(self, **kwargs):
            self.init_kwargs = kwargs
            self.chat = SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setattr(anthropic, "Anthropic", FakeAnthropic)
    monkeypatch.setattr(openai, "OpenAI", FakeOpenAI)
    return SimpleNamespace(calls=calls)


@pytest.fixture
def mock_caldav(monkeypatch):
    """Stub ``caldav.DAVClient`` so CalDAV connections are never opened.

    Records interactions in ``.calls`` and returns configurable events from
    ``.search()`` (set via ``.set_events([...])``).
    """
    import caldav

    calls: list[tuple] = []
    holder = {"events": []}

    class FakeCalendar:
        def search(self, **kwargs):
            calls.append(("search", kwargs))
            return list(holder["events"])

    class FakePrincipal:
        def calendars(self):
            calls.append(("calendars", {}))
            return [FakeCalendar()]

    class FakeDAVClient:
        def __init__(self, **kwargs):
            calls.append(("client", kwargs))

        def principal(self):
            return FakePrincipal()

    monkeypatch.setattr(caldav, "DAVClient", FakeDAVClient)

    controller = SimpleNamespace(calls=calls)
    controller.set_events = lambda events: holder.__setitem__("events", events)
    return controller
