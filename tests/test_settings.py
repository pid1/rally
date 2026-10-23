"""Tests for the settings router: key-value settings, the versioned AI settings
and LLM config (save / history / rollback), and calendars CRUD.

The connectivity-test endpoints (test-llm, test-weather, calendars/{id}/test)
are covered separately in Phase 5 with the external-boundary stubs.
"""

import json
from datetime import UTC, datetime

import pytest

from rally import member_prefs
from rally.member_prefs import CALENDAR_DEFAULT_VIEW
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


def test_llm_config_save_writes_derived_keys_anthropic(client, mock_llm):
    body = client.put(
        "/api/settings/llm/config",
        json={"provider": "anthropic", "model": "claude-x"},
    ).json()

    assert body == {"provider": "anthropic", "model": "claude-x", "history_id": body["history_id"]}
    assert body["history_id"] is not None

    cfg = client.get("/api/settings/llm/config").json()
    assert (cfg["provider"], cfg["model"]) == ("anthropic", "claude-x")

    settings = client.get("/api/settings").json()["settings"]
    assert settings["llm_provider"] == "anthropic"
    assert settings["llm_anthropic_model"] == "claude-x"
    # No token budget is stored any more, and saving makes no Models API call:
    # the generator resolves the model's maximum itself at run time.
    assert not any("max_tokens" in key for key in settings)
    assert mock_llm.calls == []


def test_llm_config_save_local_uses_local_model_key(client):
    client.put("/api/settings/llm/config", json={"provider": "local", "model": "llama"})

    settings = client.get("/api/settings").json()["settings"]
    assert settings["llm_provider"] == "local"
    assert settings["llm_local_model"] == "llama"
    assert not any("max_tokens" in key for key in settings)


def test_llm_config_save_ignores_legacy_max_tokens_fields(client, db_session):
    """A stale browser tab still sends max_tokens / max_tokens_mode — even the
    values the old form could produce (0 placeholder, model_max on local).
    Those must not 422, and must not be stored."""
    for payload in (
        {
            "provider": "anthropic",
            "model": "claude-x",
            "max_tokens": 0,
            "max_tokens_mode": "model_max",
        },
        {"provider": "local", "model": "llama", "max_tokens": 8000, "max_tokens_mode": "custom"},
        {"provider": "local", "model": "llama", "max_tokens": "bogus", "max_tokens_mode": "x"},
    ):
        resp = client.put("/api/settings/llm/config", json=payload)
        assert resp.status_code == 200, resp.text
        assert "max_tokens" not in resp.json()

    settings = client.get("/api/settings").json()["settings"]
    assert not any("max_tokens" in key for key in settings)
    for row in db_session.query(LLMSettingsHistory).all():
        assert "max_tokens" not in json.loads(row.value)


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
        "/api/settings/llm/config",
        json={"provider": "anthropic", "model": "claude-1"},
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


def _legacy_snapshot(db_session, **config):
    """An llm_config snapshot as saved before token limits were removed."""
    row = LLMSettingsHistory(
        field_name="llm_config",
        value=json.dumps(config),
        created_at=T1,
        last_used_at=T1,
    )
    db_session.add(row)
    db_session.commit()
    return row.id


def test_llm_config_history_lists_legacy_snapshots_with_max_tokens(client, db_session):
    _legacy_snapshot(
        db_session,
        provider="anthropic",
        model="claude-old",
        max_tokens=128000,
        max_tokens_mode="model_max",
    )

    hist = client.get("/api/settings/llm/config/history").json()

    assert len(hist["history"]) == 1
    entry = hist["history"][0]
    assert (entry["provider"], entry["model"]) == ("anthropic", "claude-old")
    assert "max_tokens" not in entry
    assert "max_tokens_mode" not in entry


def test_llm_config_rollback_to_legacy_snapshot_restores_pair_only(client, db_session, mock_llm):
    """Rolling back to a snapshot that carries max_tokens restores its provider
    and model and nothing else — no budget key comes back, no API call is made."""
    legacy_id = _legacy_snapshot(
        db_session,
        provider="anthropic",
        model="claude-old",
        max_tokens=4000,
        max_tokens_mode="custom",
    )
    client.put("/api/settings/llm/config", json={"provider": "local", "model": "llama"})

    resp = client.post("/api/settings/llm/config/rollback", json={"history_id": legacy_id})

    assert resp.status_code == 200
    assert resp.json() == {"provider": "anthropic", "model": "claude-old", "history_id": legacy_id}
    settings = client.get("/api/settings").json()["settings"]
    assert settings["llm_provider"] == "anthropic"
    assert settings["llm_anthropic_model"] == "claude-old"
    assert not any("max_tokens" in key for key in settings)
    assert mock_llm.calls == []


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
            "password": "app-pass",
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
        client.put("/api/settings", json={"settings": {"home_location": "Highland Village, TX"}})
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
    client.put(f"/api/family/{emma.id}", json={"notifications": {"event_change": False}})

    rows = {
        kind["kind"]: kind for kind in client.get("/api/notifications/overview").json()["kinds"]
    }

    assert rows["event_change"]["receiving"] == ["Dad"]
    assert rows["event_change"]["muted"] == ["Emma"]
    assert rows["event_change"]["no_key"] == ["Jake"]


def test_the_overview_names_the_install_wide_switch_behind_a_kind(client):
    rows = {
        kind["kind"]: kind for kind in client.get("/api/notifications/overview").json()["kinds"]
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


# --- The behavioral settings catalog -------------------------------------------


def test_the_catalog_lists_every_setting_with_its_choices_and_default(client):
    body = client.get("/api/preferences/catalog").json()

    keys = [setting["key"] for setting in body["settings"]]
    assert keys == list(member_prefs.SETTING_KEYS)

    calendar = next(s for s in body["settings"] if s["key"] == CALENDAR_DEFAULT_VIEW)
    assert calendar["default"] == member_prefs.AUTO
    assert {choice["value"] for choice in calendar["choices"]} == {
        choice.value for choice in member_prefs.CALENDAR_VIEW_CHOICES
    }


def test_every_setting_defaults_to_auto_over_the_wire(client):
    """A concrete default would move a device nobody had configured."""
    body = client.get("/api/preferences/catalog").json()

    for setting in body["settings"]:
        assert setting["default"] == member_prefs.AUTO
        assert any(choice["value"] == member_prefs.AUTO for choice in setting["choices"])


def test_the_catalog_reads_nothing_and_needs_no_family(client):
    """Configuration, not state: it answers on a brand new install."""
    assert client.get("/api/preferences/catalog").status_code == 200


# --- what a calendar, a member and a team need (#272) --------------------------


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({"cal_type": "ics", "url": "  "}, "A URL can't be empty."),
        (
            {"cal_type": "caldav_apple", "url": "https://caldav.icloud.com/", "password": "p"},
            "An email can't be empty.",
        ),
        (
            {"cal_type": "caldav_google", "url": "https://x/", "username": "a@b.c"},
            "A password can't be empty.",
        ),
    ],
)
def test_a_calendar_missing_what_its_type_needs_is_refused(client, make_member, payload, message):
    member = make_member("Dad")
    response = client.post(
        "/api/calendars", json={"label": "Cal", "family_member_id": member.id, **payload}
    )
    assert response.status_code == 422
    assert response.json()["detail"] == message
    assert client.get("/api/calendars").json() == []


def test_a_native_calendar_needs_no_url(client, make_member):
    member = make_member("Dad")
    response = client.post(
        "/api/calendars",
        json={"label": "Dad", "family_member_id": member.id, "cal_type": "native"},
    )
    assert response.status_code == 201


def test_editing_a_caldav_calendar_keeps_the_stored_password(client, make_member):
    member = make_member("Dad")
    cal = client.post(
        "/api/calendars",
        json={
            "label": "iCloud",
            "family_member_id": member.id,
            "cal_type": "caldav_apple",
            "url": "https://caldav.icloud.com/",
            "username": "dad@icloud.com",
            "password": "app-pass",
        },
    ).json()
    response = client.put(f"/api/calendars/{cal['id']}", json={"label": "Dad's iCloud"})
    assert response.status_code == 200
    response = client.put(f"/api/calendars/{cal['id']}", json={"username": " "})
    assert response.status_code == 422
    assert response.json()["detail"] == "An email can't be empty."


def test_a_blank_calendar_label_is_refused(client, make_member):
    member = make_member("Dad")
    cal = _make_calendar(client, member.id)
    assert client.put(f"/api/calendars/{cal['id']}", json={"label": "  "}).status_code == 422
    response = client.post(
        "/api/calendars",
        json={"label": " ", "url": "https://ex/c.ics", "family_member_id": member.id},
    )
    assert response.status_code == 422
    assert "A label can't be empty." in response.text


def test_a_blank_member_name_is_refused(client, make_member):
    member = make_member("Dad")
    response = client.post("/api/family", json={"name": "   "})
    assert response.status_code == 422
    assert "A name can't be empty." in response.text
    assert client.put(f"/api/family/{member.id}", json={"name": " "}).status_code == 422
