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
from rally.models import (
    Calendar,
    DinnerPlan,
    Event,
    EventAttendee,
    FamilyMember,
    RecurringTodo,
    Setting,
    ShoppingItem,
    ShoppingItemHistory,
    ShoppingStore,
    Todo,
)

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
def make_store(db_session: Session):
    def _make(name: str = "Costco", **kwargs) -> ShoppingStore:
        store = ShoppingStore(name=name, **kwargs)
        db_session.add(store)
        db_session.commit()
        db_session.refresh(store)
        return store

    return _make


@pytest.fixture
def make_shopping_item(db_session: Session):
    def _make(
        name: str = "Milk",
        *,
        note: str | None = None,
        store_id: int | None = None,
        completed: bool = False,
        completed_at: datetime | None = None,
        created_at: datetime | None = None,
        **kwargs,
    ) -> ShoppingItem:
        item = ShoppingItem(
            name=name,
            note=note,
            store_id=store_id,
            completed=completed,
            completed_at=completed_at,
            **kwargs,
        )
        if created_at is not None:
            item.created_at = created_at
        db_session.add(item)
        db_session.commit()
        db_session.refresh(item)
        return item

    return _make


@pytest.fixture
def make_item_history(db_session: Session):
    def _make(
        name: str = "Milk",
        *,
        store_id: int | None = None,
        times_added: int = 1,
        last_added_at: datetime | None = None,
        **kwargs,
    ) -> ShoppingItemHistory:
        row = ShoppingItemHistory(
            name_key=name.strip().casefold(),
            name=name,
            store_id=store_id,
            times_added=times_added,
            **kwargs,
        )
        if last_added_at is not None:
            row.last_added_at = last_added_at
        db_session.add(row)
        db_session.commit()
        db_session.refresh(row)
        return row

    return _make


@pytest.fixture
def make_native_calendar(db_session: Session, make_member):
    """A Rally-owned calendar, plus the member who owns it if none is given."""

    def _make(owner=None, label: str | None = None) -> Calendar:
        owner = owner or make_member("Alex")
        calendar = Calendar(
            label=label or f"{owner.name}'s Calendar",
            url="",
            family_member_id=owner.id,
            cal_type="native",
        )
        db_session.add(calendar)
        db_session.commit()
        db_session.refresh(calendar)
        return calendar

    return _make


@pytest.fixture
def make_event(db_session: Session, make_native_calendar):
    """A native event, with times given as local wall clock in ``tzid``.

    Times go through the same ``resolve_event_times`` the API uses, so a test
    never hand-computes a UTC instant — hand-computed instants are how a test
    ends up asserting the bug rather than the behaviour.
    """
    from rally.calendars.inputs import resolve_event_times

    def _make(
        title: str = "An event",
        *,
        start: str = "2026-08-11T09:00",
        end: str | None = None,
        all_day: bool = False,
        tzid: str = "America/Chicago",
        rrule: str | None = None,
        notify_minutes_before: int | None = None,
        attendees: list | None = None,
        calendar=None,
        location: str | None = None,
        description: str | None = None,
    ) -> Event:
        calendar = calendar or make_native_calendar()
        times = resolve_event_times(start=start, end=end, all_day=all_day, tzid=tzid)
        event = Event(
            calendar_id=calendar.id,
            uid=f"rally-test-{title.lower().replace(' ', '-')}-{start}@rally.local",
            title=title,
            location=location,
            description=description,
            rrule=rrule,
            notify_minutes_before=notify_minutes_before,
            **times,
        )
        db_session.add(event)
        db_session.commit()
        db_session.refresh(event)

        for member in attendees or []:
            db_session.add(EventAttendee(event_id=event.id, family_member_id=member.id))
        if attendees:
            db_session.commit()
        return event

    return _make


@pytest.fixture
def mock_pushover(monkeypatch):
    """Stub the Pushover transport. No test may reach the real API.

    Records every delivery in ``.sent``; ``.fail_with(message)`` makes the next
    and subsequent sends raise until ``.succeed()``.
    """
    import rally.notifications as notifications

    class Recorder:
        def __init__(self):
            self.sent: list[dict] = []
            self._error: str | None = None

        def fail_with(self, message: str) -> None:
            self._error = message

        def succeed(self) -> None:
            self._error = None

        def __call__(self, token, user_key, message, *, title="Rally", device=None):
            if self._error:
                raise notifications.PushoverError(self._error)
            self.sent.append(
                {
                    "token": token,
                    "user": user_key,
                    "message": message,
                    "title": title,
                    "device": device,
                }
            )

    recorder = Recorder()
    monkeypatch.setattr(notifications, "send_pushover", recorder)
    return recorder


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
    "rally.routers.shopping",
    "rally.routers.dashboard",
    "rally.routers.events",
    "rally.routers.settings",
    "rally.generator.generate",
    "rally.notifications",
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

    class FakeModels:
        def retrieve(self, model_id):
            calls.append(("models.retrieve", model_id))
            return SimpleNamespace(id=model_id, max_tokens=200000)

    class FakeAnthropic:
        def __init__(self, **kwargs):
            self.init_kwargs = kwargs
            self.messages = FakeMessages()
            self.models = FakeModels()

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
