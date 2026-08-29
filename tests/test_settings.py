"""Tests for the settings router: key-value settings, the versioned AI settings
and LLM config (save / history / rollback), and calendars CRUD.

The connectivity-test endpoints (test-llm, test-weather, calendars/{id}/test)
are covered separately in Phase 5 with the external-boundary stubs.
"""

from datetime import UTC, datetime

from rally.models import AISettingsHistory, Calendar, LLMSettingsHistory
from rally.notification_prefs import KIND_KEYS

T1 = datetime(2026, 1, 1, tzinfo=UTC)
T2 = datetime(2026, 1, 2, tzinfo=UTC)
T3 = datetime(2026, 1, 3, tzinfo=UTC)


# --- Key-value settings --------------------------------------------------------


def test_settings_bulk_upsert_roundtrip(client):
    client.put("/api/settings", json={"settings": {"a": "1", "b": "2"}})
    assert client.get("/api/settings").json()["settings"] == {"a": "1", "b": "2"}

    # Upsert an existing key and add a new one.
    client.put("/api/settings", json={"settings": {"a": "9", "c": "3"}})
    assert client.get("/api/settings").json()["settings"] == {
        "a": "9",
        "b": "2",
        "c": "3",
    }


# --- AI settings versioning ----------------------------------------------------


def test_ai_get_empty_when_unset(client):
    ai = client.get("/api/settings/ai").json()
    assert ai["agent_voice"] == {
        "field_name": "agent_voice",
        "value": "",
        "history_id": None,
    }
    assert ai["family_context"]["value"] == ""


def test_ai_save_creates_snapshot_and_sets_active(client):
    body = client.put("/api/settings/ai/agent_voice", json={"value": "cheerful"}).json()

    assert body["field_name"] == "agent_voice"
    assert body["value"] == "cheerful"
    assert body["history_id"] is not None

    ai = client.get("/api/settings/ai").json()
    assert ai["agent_voice"]["value"] == "cheerful"
    assert ai["agent_voice"]["history_id"] == body["history_id"]
    # Other field is untouched.
    assert ai["family_context"]["history_id"] is None


def test_ai_history_is_newest_first(client, frozen_now):
    frozen_now(T1)
    client.put("/api/settings/ai/agent_voice", json={"value": "v1"})
    frozen_now(T2)
    v2 = client.put("/api/settings/ai/agent_voice", json={"value": "v2"}).json()

    hist = client.get("/api/settings/ai/agent_voice/history").json()

    assert hist["field_name"] == "agent_voice"
    assert hist["current_history_id"] == v2["history_id"]
    assert [h["value"] for h in hist["history"]] == ["v2", "v1"]


def test_ai_rollback_reactivates_without_new_row(client, db_session, frozen_now):
    frozen_now(T1)
    v1 = client.put("/api/settings/ai/agent_voice", json={"value": "v1"}).json()
    frozen_now(T2)
    client.put("/api/settings/ai/agent_voice", json={"value": "v2"}).json()
    assert client.get("/api/settings/ai").json()["agent_voice"]["value"] == "v2"

    frozen_now(T3)
    rb = client.post(
        "/api/settings/ai/agent_voice/rollback", json={"history_id": v1["history_id"]}
    ).json()

    assert rb["value"] == "v1"
    assert rb["history_id"] == v1["history_id"]
    assert (
        client.get("/api/settings/ai").json()["agent_voice"]["history_id"]
        == v1["history_id"]
    )

    # No new row was inserted; last_used_at on the reactivated row was bumped.
    rows = db_session.query(AISettingsHistory).filter_by(field_name="agent_voice").all()
    assert len(rows) == 2
    reactivated = db_session.get(AISettingsHistory, v1["history_id"])
    assert reactivated.last_used_at > reactivated.created_at


def test_ai_save_unknown_field_404(client):
    assert client.put("/api/settings/ai/bogus", json={"value": "x"}).status_code == 404


def test_ai_history_unknown_field_404(client):
    assert client.get("/api/settings/ai/bogus/history").status_code == 404


def test_ai_rollback_unknown_field_404(client):
    resp = client.post("/api/settings/ai/bogus/rollback", json={"history_id": 1})
    assert resp.status_code == 404


def test_ai_rollback_missing_history_404(client):
    resp = client.post(
        "/api/settings/ai/agent_voice/rollback", json={"history_id": 9999}
    )
    assert resp.status_code == 404


# --- LLM config versioning -----------------------------------------------------


def test_llm_config_get_empty_when_unset(client):
    assert client.get("/api/settings/llm/config").json() == {
        "provider": "",
        "model": "",
        "max_tokens": None,
        "max_tokens_mode": None,
        "history_id": None,
    }


def test_llm_config_save_writes_derived_keys_anthropic(client):
    body = client.put(
        "/api/settings/llm/config",
        json={"provider": "anthropic", "model": "claude-x", "max_tokens": 16000},
    ).json()

    assert (body["provider"], body["model"]) == ("anthropic", "claude-x")
    assert body["max_tokens"] == 16000
    assert body["max_tokens_mode"] == "custom"
    assert body["history_id"] is not None

    cfg = client.get("/api/settings/llm/config").json()
    assert (cfg["provider"], cfg["model"], cfg["max_tokens"]) == (
        "anthropic",
        "claude-x",
        16000,
    )

    settings = client.get("/api/settings").json()["settings"]
    assert settings["llm_provider"] == "anthropic"
    assert settings["llm_anthropic_model"] == "claude-x"
    assert settings["llm_anthropic_max_tokens"] == "16000"
    assert settings["llm_anthropic_max_tokens_mode"] == "custom"
    # The other provider's budget key is untouched.
    assert "llm_local_max_tokens" not in settings


def test_llm_config_save_local_uses_local_model_key(client):
    body = client.put(
        "/api/settings/llm/config",
        json={"provider": "local", "model": "llama", "max_tokens": 8000},
    ).json()

    assert body["max_tokens_mode"] == "custom"

    settings = client.get("/api/settings").json()["settings"]
    assert settings["llm_provider"] == "local"
    assert settings["llm_local_model"] == "llama"
    assert settings["llm_local_max_tokens"] == "8000"
    # Local has no budget-mode setting — the radio only exists for Anthropic.
    assert "llm_anthropic_max_tokens_mode" not in settings


def test_llm_config_save_defaults_max_tokens_when_omitted(client):
    """Existing callers that don't send max_tokens still work — 4000/custom."""
    body = client.put(
        "/api/settings/llm/config", json={"provider": "local", "model": "m"}
    ).json()

    assert body["max_tokens"] == 4000
    assert body["max_tokens_mode"] == "custom"


def test_llm_config_save_rejects_non_positive_max_tokens(client):
    resp = client.put(
        "/api/settings/llm/config",
        json={"provider": "local", "model": "m", "max_tokens": 0},
    )
    assert resp.status_code == 422
    # A plain string, not a Pydantic-validation-error array — this is a
    # deliberate HTTPException from the handler (see the next test for why
    # positivity can't be a Field-level constraint), and the browser renders
    # `detail` directly in the verify modal, so an array would show as the
    # useless literal text "[object Object]".
    assert isinstance(resp.json()["detail"], str)


def test_llm_config_save_model_max_accepts_placeholder_zero_max_tokens(
    client, make_setting, mock_llm
):
    """Regression: the browser blanks the Max Tokens field while "Model
    maximum" is selected and unresolved, which serializes to 0 — and mode is
    what the client actually has selected when the field goes blank, so this
    must succeed exactly like any other model_max save, not 422 on the way in."""
    make_setting("llm_anthropic_api_key", "sk-test")

    resp = client.put(
        "/api/settings/llm/config",
        json={
            "provider": "anthropic",
            "model": "claude-x",
            "max_tokens": 0,
            "max_tokens_mode": "model_max",
        },
    )

    assert resp.status_code == 200
    assert resp.json()["max_tokens"] == 200000


def test_llm_config_save_local_forces_custom_mode_even_if_client_sends_model_max(
    client,
):
    """Defense in depth: local has no Models API, so the server ignores a stray
    model_max mode from a stale client rather than trusting it."""
    body = client.put(
        "/api/settings/llm/config",
        json={
            "provider": "local",
            "model": "llama",
            "max_tokens": 4000,
            "max_tokens_mode": "model_max",
        },
    ).json()

    assert body["max_tokens_mode"] == "custom"
    assert (
        body["max_tokens"] == 4000
    )  # not resolved — the submitted value passes through


def test_llm_config_save_model_max_resolves_via_models_api(
    client, make_setting, mock_llm
):
    make_setting("llm_anthropic_api_key", "sk-test")

    body = client.put(
        "/api/settings/llm/config",
        json={
            "provider": "anthropic",
            "model": "claude-x",
            "max_tokens": 999,  # ignored — the resolved value wins
            "max_tokens_mode": "model_max",
        },
    ).json()

    assert body["max_tokens"] == 200000  # from FakeModels.retrieve in conftest
    assert body["max_tokens_mode"] == "model_max"
    assert ("models.retrieve", "claude-x") in mock_llm.calls

    settings = client.get("/api/settings").json()["settings"]
    assert settings["llm_anthropic_max_tokens"] == "200000"
    assert settings["llm_anthropic_max_tokens_mode"] == "model_max"


def test_llm_config_save_model_max_rejects_unresolvable_model(
    client, make_setting, monkeypatch
):
    make_setting("llm_anthropic_api_key", "sk-test")

    import anthropic

    class FailingModels:
        def retrieve(self, model_id):
            raise RuntimeError("model not found: bogus-model")

    class FakeAnthropicClient:
        def __init__(self, **kwargs):
            self.models = FailingModels()

    monkeypatch.setattr(anthropic, "Anthropic", FakeAnthropicClient)

    resp = client.put(
        "/api/settings/llm/config",
        json={
            "provider": "anthropic",
            "model": "bogus-model",
            "max_tokens": 4000,
            "max_tokens_mode": "model_max",
        },
    )

    assert resp.status_code == 400
    assert "bogus-model" in resp.json()["detail"]
    # No snapshot was written on the failure path.
    assert client.get("/api/settings/llm/config").json()["history_id"] is None


def test_llm_config_save_model_max_rejects_missing_api_key(client):
    resp = client.put(
        "/api/settings/llm/config",
        json={
            "provider": "anthropic",
            "model": "claude-x",
            "max_tokens": 4000,
            "max_tokens_mode": "model_max",
        },
    )
    assert resp.status_code == 400
    assert "API key" in resp.json()["detail"]


def test_llm_config_history_is_newest_first(client, frozen_now):
    frozen_now(T1)
    client.put("/api/settings/llm/config", json={"provider": "local", "model": "m1"})
    frozen_now(T2)
    b = client.put(
        "/api/settings/llm/config", json={"provider": "anthropic", "model": "m2"}
    ).json()

    hist = client.get("/api/settings/llm/config/history").json()

    assert hist["current_history_id"] == b["history_id"]
    assert [(h["provider"], h["model"]) for h in hist["history"]] == [
        ("anthropic", "m2"),
        ("local", "m1"),
    ]


def test_llm_config_rollback_restores_pair_and_derived_keys(
    client, db_session, frozen_now
):
    frozen_now(T1)
    a = client.put(
        "/api/settings/llm/config",
        json={"provider": "anthropic", "model": "claude-1", "max_tokens": 32000},
    ).json()
    frozen_now(T2)
    client.put("/api/settings/llm/config", json={"provider": "local", "model": "llama"})

    # Currently local/llama.
    settings = client.get("/api/settings").json()["settings"]
    assert settings["llm_provider"] == "local"
    assert settings["llm_local_model"] == "llama"

    frozen_now(T3)
    rb = client.post(
        "/api/settings/llm/config/rollback", json={"history_id": a["history_id"]}
    ).json()

    assert (rb["provider"], rb["model"], rb["max_tokens"]) == (
        "anthropic",
        "claude-1",
        32000,
    )
    cfg = client.get("/api/settings/llm/config").json()
    assert (cfg["provider"], cfg["model"], cfg["max_tokens"]) == (
        "anthropic",
        "claude-1",
        32000,
    )

    # Derived keys the generator reads are restored too, not just the pointer.
    settings = client.get("/api/settings").json()["settings"]
    assert settings["llm_provider"] == "anthropic"
    assert settings["llm_anthropic_model"] == "claude-1"
    assert settings["llm_anthropic_max_tokens"] == "32000"

    # No new snapshot row.
    assert db_session.query(LLMSettingsHistory).count() == 2


def test_llm_config_rollback_restores_max_tokens_verbatim_without_reresolving(
    client, make_setting, mock_llm
):
    """A model_max snapshot's stored number is restored as-is on rollback — the
    Models API is never called again, even though it's available."""
    make_setting("llm_anthropic_api_key", "sk-test")
    a = client.put(
        "/api/settings/llm/config",
        json={
            "provider": "anthropic",
            "model": "claude-x",
            "max_tokens": 1,
            "max_tokens_mode": "model_max",
        },
    ).json()
    assert a["max_tokens"] == 200000  # resolved at save time

    client.put("/api/settings/llm/config", json={"provider": "local", "model": "llama"})
    calls_before_rollback = len(mock_llm.calls)

    rb = client.post(
        "/api/settings/llm/config/rollback", json={"history_id": a["history_id"]}
    ).json()

    assert rb["max_tokens"] == 200000
    assert rb["max_tokens_mode"] == "model_max"
    assert len(mock_llm.calls) == calls_before_rollback  # no fresh models.retrieve call


def test_llm_config_rollback_missing_history_404(client):
    resp = client.post("/api/settings/llm/config/rollback", json={"history_id": 9999})
    assert resp.status_code == 404


# --- Calendars CRUD ------------------------------------------------------------


def _make_calendar(client, member_id, **overrides):
    payload = {"label": "Cal", "url": "https://ex/c.ics", "family_member_id": member_id}
    payload.update(overrides)
    resp = client.post("/api/calendars", json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_calendar_create_persists_password_but_never_returns_it(
    client, db_session, make_member
):
    member = make_member("Dad")

    body = _make_calendar(
        client,
        member.id,
        label="Family",
        cal_type="caldav_apple",
        username="user@example.com",
        password="app-specific-secret",
    )

    assert body["label"] == "Family"
    assert body["username"] == "user@example.com"
    assert body["password"] is None  # stripped from every response

    # ...but it is stored for the connectivity test to use.
    stored = db_session.get(Calendar, body["id"])
    assert stored.password == "app-specific-secret"


def test_calendar_list_ordered_by_label(client, make_member):
    member = make_member("Dad")
    _make_calendar(client, member.id, label="Bravo")
    _make_calendar(client, member.id, label="Alpha")

    labels = [c["label"] for c in client.get("/api/calendars").json()]

    assert labels == ["Alpha", "Bravo"]


def test_calendar_get_found_and_404(client, make_member):
    member = make_member("Dad")
    cal = _make_calendar(client, member.id)

    got = client.get(f"/api/calendars/{cal['id']}").json()
    assert got["id"] == cal["id"]
    assert got["password"] is None

    assert client.get("/api/calendars/9999").status_code == 404


def test_calendar_update_and_password_still_stripped(client, make_member):
    member = make_member("Dad")
    cal = _make_calendar(client, member.id, label="Old")

    body = client.put(
        f"/api/calendars/{cal['id']}", json={"label": "New", "password": "changed"}
    ).json()

    assert body["label"] == "New"
    assert body["password"] is None


def test_calendar_update_all_fields(client, make_member):
    dad = make_member("Dad")
    mom = make_member("Mom")
    cal = _make_calendar(client, dad.id)

    body = client.put(
        f"/api/calendars/{cal['id']}",
        json={
            "url": "https://new/c.ics",
            "family_member_id": mom.id,
            "owner_email": "owner@example.com",
            "cal_type": "caldav_google",
            "username": "user2",
        },
    ).json()

    assert body["url"] == "https://new/c.ics"
    assert body["family_member_id"] == mom.id
    assert body["owner_email"] == "owner@example.com"
    assert body["cal_type"] == "caldav_google"
    assert body["username"] == "user2"


def test_calendar_update_404(client):
    assert client.put("/api/calendars/9999", json={"label": "x"}).status_code == 404


def test_calendar_delete_and_404(client, db_session, make_member):
    member = make_member("Dad")
    cal = _make_calendar(client, member.id)

    assert client.delete(f"/api/calendars/{cal['id']}").status_code == 204
    assert db_session.get(Calendar, cal["id"]) is None

    assert client.delete("/api/calendars/9999").status_code == 404


class TestHomeLocation:
    """Home location is first-party config, not prose buried in family context."""

    def test_defaults_to_empty(self, db_session):
        from rally.utils.settings import home_location

        assert home_location(db_session) == ""

    def test_reads_the_setting(self, db_session, make_setting):
        from rally.utils.settings import home_location

        make_setting("home_location", "Highland Village, TX")
        assert home_location(db_session) == "Highland Village, TX"

    def test_whitespace_only_reads_as_unset(self, db_session, make_setting):
        from rally.utils.settings import home_location

        make_setting("home_location", "   ")
        assert home_location(db_session) == ""

    def test_round_trips_through_the_api(self, client):
        client.put(
            "/api/settings",
            json={"settings": {"home_location": "Highland Village, TX"}},
        )
        body = client.get("/api/settings").json()
        assert body["settings"]["home_location"] == "Highland Village, TX"


# --- What Rally sends ----------------------------------------------------------


def test_the_overview_lists_every_kind_with_its_audience(client):
    body = client.get("/api/notifications/overview").json()

    assert [kind["kind"] for kind in body["kinds"]] == list(KIND_KEYS)
    assert all(kind["audience"] for kind in body["kinds"])


def test_the_overview_reports_a_missing_application_token(client, make_setting):
    """The first of the five gates: without it nothing sends at all."""
    assert client.get("/api/notifications/overview").json()["token_configured"] is False

    make_setting("pushover_app_token", "app-token")
    assert client.get("/api/notifications/overview").json()["token_configured"] is True


def test_the_overview_says_who_hears_each_kind(client, make_member):
    make_member("Dad", pushover_user_key="dad-key")
    make_member("Jake")
    emma = make_member("Emma", pushover_user_key="emma-key")
    client.put(
        f"/api/family/{emma.id}", json={"notifications": {"event_change": False}}
    )

    rows = {
        kind["kind"]: kind
        for kind in client.get("/api/notifications/overview").json()["kinds"]
    }

    assert rows["event_change"]["receiving"] == ["Dad"]
    assert rows["event_change"]["muted"] == ["Emma"]
    assert rows["event_change"]["no_key"] == ["Jake"]


def test_the_overview_names_the_install_wide_switch_behind_a_kind(client):
    rows = {
        kind["kind"]: kind
        for kind in client.get("/api/notifications/overview").json()["kinds"]
    }

    assert rows["task_assignment"]["settings_key"] == "todo_notify_enabled"
    assert rows["event_reminder"]["settings_key"] is None


def test_shopping_notification_settings_round_trip(client):
    client.put(
        "/api/settings",
        json={
            "settings": {
                "shopping_notify_enabled": "true",
                "shopping_notify_settle_minutes": "10",
            }
        },
    )

    settings = client.get("/api/settings").json()["settings"]

    assert settings["shopping_notify_enabled"] == "true"
    assert settings["shopping_notify_settle_minutes"] == "10"
