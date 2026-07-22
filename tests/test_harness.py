"""Self-tests for the shared harness fixtures (conftest.py).

These prove the fixtures behave as the rest of the suite will rely on: the seed
factories persist rows, `frozen_now` freezes time everywhere it matters, the
timezone setter takes effect, and the external-boundary stubs intercept calls so
no network happens.
"""

from datetime import UTC, datetime

from rally.models import DinnerPlan, FamilyMember, RecurringTodo, Setting, Todo

# --- Seed factories ------------------------------------------------------------


def test_seed_factories_persist_rows(
    db_session, make_member, make_todo, make_recurring_todo, make_dinner_plan, make_setting
):
    member = make_member("Dad")
    todo = make_todo("Buy milk", assigned_to=member.id)
    rt = make_recurring_todo("Water plants", recurrence_type="weekly", recurrence_day=1)
    dp = make_dinner_plan("2026-02-01", plan="Tacos", rating=5)
    make_setting("local_timezone", "America/Chicago")

    assert member.id is not None and todo.id is not None
    assert rt.id is not None and dp.id is not None
    assert db_session.query(FamilyMember).count() == 1
    assert db_session.query(Todo).count() == 1
    assert db_session.query(RecurringTodo).count() == 1
    assert db_session.query(DinnerPlan).count() == 1
    assert db_session.get(Setting, "local_timezone").value == "America/Chicago"


def test_make_setting_upserts(make_setting, db_session):
    make_setting("k", "v1")
    make_setting("k", "v2")

    assert db_session.get(Setting, "k").value == "v2"
    assert db_session.query(Setting).filter(Setting.key == "k").count() == 1


# --- Deterministic time --------------------------------------------------------


def test_frozen_now_patches_canonical_and_derived(frozen_now):
    import rally.utils.timezone as tz

    instant = frozen_now(datetime(2026, 3, 15, 9, 30, tzinfo=UTC))

    assert tz.now_utc() == instant
    assert tz.today_utc() == instant.date()
    # today_local resolves now_utc at call time, so it follows the freeze too.
    assert tz.today_local("America/Chicago") == datetime(2026, 3, 15, 4, 30).date()


def test_frozen_now_reaches_by_name_importers(client, make_todo, frozen_now):
    # todos.py imported now_utc by name; PUT sets completed_at = now_utc() on the
    # completed transition. If the binding weren't patched, this would use real time.
    frozen_now(datetime(2026, 3, 15, 9, 30, tzinfo=UTC))
    todo = make_todo("Reopen me", completed=False, completed_at=None)

    resp = client.put(f"/api/todos/{todo.id}", json={"completed": True})

    assert resp.status_code == 200, resp.text
    assert resp.json()["completed_at"].startswith("2026-03-15T09:30")


# --- Timezone setter -----------------------------------------------------------


def test_local_timezone_fixture_sets_setting(client, local_timezone):
    local_timezone("America/New_York")

    settings = client.get("/api/settings").json()["settings"]
    assert settings["local_timezone"] == "America/New_York"


# --- External boundary stubs ---------------------------------------------------

_DWML = (
    "<dwml><data type='current observations'><parameters>"
    "<temperature type='apparent'><value>72</value></temperature>"
    "<weather><weather-conditions weather-summary='Sunny'/></weather>"
    "</parameters></data></dwml>"
)


def test_mock_requests_intercepts_weather(client, make_setting, mock_requests):
    make_setting("weather_nws_url", "https://forecast.example/nws")
    mock_requests.set_response(text=_DWML, status_code=200)

    resp = client.post("/api/settings/test-weather").json()

    assert resp["success"] is True
    assert "72" in resp["message"]
    assert mock_requests.calls[0]["url"] == "https://forecast.example/nws"


def test_mock_llm_intercepts_anthropic(client, make_setting, mock_llm):
    make_setting("llm_provider", "anthropic")
    make_setting("llm_anthropic_api_key", "sk-test")
    make_setting("llm_anthropic_model", "claude-test")

    resp = client.post("/api/settings/test-llm").json()

    assert resp["success"] is True
    assert mock_llm.calls and mock_llm.calls[0][0] == "anthropic"


def test_mock_llm_intercepts_openai_local(client, make_setting, mock_llm):
    make_setting("llm_provider", "local")
    make_setting("llm_local_base_url", "http://localhost:1234/v1")
    make_setting("llm_local_model", "local-model")

    resp = client.post("/api/settings/test-llm").json()

    assert resp["success"] is True
    assert mock_llm.calls and mock_llm.calls[0][0] == "openai"


def test_mock_caldav_intercepts_client(mock_caldav):
    import caldav

    sentinel = object()
    mock_caldav.set_events([sentinel])

    calendars = caldav.DAVClient(url="https://dav.example").principal().calendars()
    events = calendars[0].search(event=True)

    assert events == [sentinel]
    assert ("client", {"url": "https://dav.example"}) in mock_caldav.calls
