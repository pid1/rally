"""Tests for the settings router: key-value settings, the versioned AI settings
and LLM config (save / history / rollback), and calendars CRUD.

The connectivity-test endpoints (test-llm, test-weather, calendars/{id}/test)
are covered separately in Phase 5 with the external-boundary stubs.
"""

from datetime import UTC, datetime

from rally.models import AISettingsHistory, Calendar, LLMSettingsHistory

T1 = datetime(2026, 1, 1, tzinfo=UTC)
T2 = datetime(2026, 1, 2, tzinfo=UTC)
T3 = datetime(2026, 1, 3, tzinfo=UTC)


# --- Key-value settings --------------------------------------------------------


def test_settings_bulk_upsert_roundtrip(client):
    client.put("/api/settings", json={"settings": {"a": "1", "b": "2"}})
    assert client.get("/api/settings").json()["settings"] == {"a": "1", "b": "2"}

    # Upsert an existing key and add a new one.
    client.put("/api/settings", json={"settings": {"a": "9", "c": "3"}})
    assert client.get("/api/settings").json()["settings"] == {"a": "9", "b": "2", "c": "3"}


# --- AI settings versioning ----------------------------------------------------


def test_ai_get_empty_when_unset(client):
    ai = client.get("/api/settings/ai").json()
    assert ai["agent_voice"] == {"field_name": "agent_voice", "value": "", "history_id": None}
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
    assert client.get("/api/settings/ai").json()["agent_voice"]["history_id"] == v1["history_id"]

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
    resp = client.post("/api/settings/ai/agent_voice/rollback", json={"history_id": 9999})
    assert resp.status_code == 404


# --- LLM config versioning -----------------------------------------------------


def test_llm_config_get_empty_when_unset(client):
    assert client.get("/api/settings/llm/config").json() == {
        "provider": "",
        "model": "",
        "history_id": None,
    }


def test_llm_config_save_writes_derived_keys_anthropic(client):
    body = client.put(
        "/api/settings/llm/config", json={"provider": "anthropic", "model": "claude-x"}
    ).json()

    assert (body["provider"], body["model"]) == ("anthropic", "claude-x")
    assert body["history_id"] is not None

    cfg = client.get("/api/settings/llm/config").json()
    assert (cfg["provider"], cfg["model"]) == ("anthropic", "claude-x")

    settings = client.get("/api/settings").json()["settings"]
    assert settings["llm_provider"] == "anthropic"
    assert settings["llm_anthropic_model"] == "claude-x"


def test_llm_config_save_local_uses_local_model_key(client):
    client.put("/api/settings/llm/config", json={"provider": "local", "model": "llama"})

    settings = client.get("/api/settings").json()["settings"]
    assert settings["llm_provider"] == "local"
    assert settings["llm_local_model"] == "llama"


def test_llm_config_history_is_newest_first(client, frozen_now):
    frozen_now(T1)
    client.put("/api/settings/llm/config", json={"provider": "local", "model": "m1"})
    frozen_now(T2)
    b = client.put("/api/settings/llm/config", json={"provider": "anthropic", "model": "m2"}).json()

    hist = client.get("/api/settings/llm/config/history").json()

    assert hist["current_history_id"] == b["history_id"]
    assert [(h["provider"], h["model"]) for h in hist["history"]] == [
        ("anthropic", "m2"),
        ("local", "m1"),
    ]


def test_llm_config_rollback_restores_pair_and_derived_keys(client, db_session, frozen_now):
    frozen_now(T1)
    a = client.put(
        "/api/settings/llm/config", json={"provider": "anthropic", "model": "claude-1"}
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

    assert (rb["provider"], rb["model"]) == ("anthropic", "claude-1")
    cfg = client.get("/api/settings/llm/config").json()
    assert (cfg["provider"], cfg["model"]) == ("anthropic", "claude-1")

    # Derived keys the generator reads are restored too, not just the pointer.
    settings = client.get("/api/settings").json()["settings"]
    assert settings["llm_provider"] == "anthropic"
    assert settings["llm_anthropic_model"] == "claude-1"

    # No new snapshot row.
    assert db_session.query(LLMSettingsHistory).count() == 2


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


def test_calendar_create_persists_password_but_never_returns_it(client, db_session, make_member):
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
