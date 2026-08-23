"""Settings and calendars router for Rally."""

import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from rally import notification_prefs
from rally.database import get_db
from rally.models import AISettingsHistory, Calendar, FollowedTeam, LLMSettingsHistory, Setting
from rally.schemas import (
    AI_SETTINGS_FIELDS,
    DEFAULT_LLM_MAX_TOKENS,
    LLM_CONFIG_FIELD,
    UNSET,
    AISettingHistoryEntry,
    AISettingHistoryResponse,
    AISettingRollback,
    AISettingState,
    AISettingValueUpdate,
    CalendarCreate,
    CalendarResponse,
    CalendarUpdate,
    FollowedTeamCreate,
    FollowedTeamResponse,
    FollowedTeamUpdate,
    LLMConfigHistoryEntry,
    LLMConfigHistoryResponse,
    LLMConfigState,
    LLMConfigUpdate,
    NotificationKindOverview,
    NotificationOverviewResponse,
    SettingsResponse,
    SettingsUpdate,
)
from rally.utils.timezone import now_utc

router = APIRouter(tags=["settings"])


# --- Key-value settings ---


@router.get("/api/settings", response_model=SettingsResponse)
def get_settings(db: Session = Depends(get_db)):
    """Get all settings as a flat dict."""
    rows = db.query(Setting).all()
    return SettingsResponse(settings={r.key: r.value for r in rows})


@router.put("/api/settings", response_model=SettingsResponse)
def update_settings(payload: SettingsUpdate, db: Session = Depends(get_db)):
    """Bulk upsert settings."""
    for key, value in payload.settings.items():
        _upsert_setting(db, key, value)
    db.commit()

    rows = db.query(Setting).all()
    return SettingsResponse(settings={r.key: r.value for r in rows})


# --- AI Settings (versioned agent_voice / family_context) ---


def _ai_pointer_key(field_name: str) -> str:
    """Settings key referencing the active ai_settings_history row for a field."""
    return f"current_{field_name}_history_id"


def _validate_ai_field(field_name: str) -> None:
    if field_name not in AI_SETTINGS_FIELDS:
        raise HTTPException(status_code=404, detail=f"Unknown AI settings field: {field_name}")


def _get_current_ai_snapshot(db: Session, field_name: str) -> AISettingsHistory | None:
    """Resolve the active history row for a field via its settings pointer."""
    pointer = db.query(Setting).filter(Setting.key == _ai_pointer_key(field_name)).first()
    if not pointer:
        return None
    return db.get(AISettingsHistory, int(pointer.value))


def _upsert_setting(db: Session, key: str, value: str) -> None:
    """Insert or update a key-value settings row."""
    row = db.query(Setting).filter(Setting.key == key).first()
    if row:
        row.value = value
    else:
        db.add(Setting(key=key, value=value))


def _set_ai_pointer(db: Session, field_name: str, history_id: int) -> None:
    """Upsert the settings pointer for a field to reference a history row."""
    _upsert_setting(db, _ai_pointer_key(field_name), str(history_id))


@router.get("/api/settings/ai", response_model=dict[str, AISettingState])
def get_ai_settings(db: Session = Depends(get_db)):
    """Get the currently active value for each AI settings field."""
    result = {}
    for field_name in AI_SETTINGS_FIELDS:
        row = _get_current_ai_snapshot(db, field_name)
        result[field_name] = AISettingState(
            field_name=field_name,
            value=row.value if row else "",
            history_id=row.id if row else None,
        )
    return result


@router.put("/api/settings/ai/{field_name}", response_model=AISettingState)
def save_ai_setting(field_name: str, payload: AISettingValueUpdate, db: Session = Depends(get_db)):
    """Explicitly save an AI settings field — inserts a new history snapshot."""
    _validate_ai_field(field_name)
    now = now_utc()  # Single timestamp so created_at == last_used_at on insert
    row = AISettingsHistory(
        field_name=field_name, value=payload.value, created_at=now, last_used_at=now
    )
    db.add(row)
    db.flush()  # Assign row.id before pointing the setting at it
    _set_ai_pointer(db, field_name, row.id)
    db.commit()
    db.refresh(row)
    return AISettingState(field_name=field_name, value=row.value, history_id=row.id)


@router.get("/api/settings/ai/{field_name}/history", response_model=AISettingHistoryResponse)
def get_ai_setting_history(field_name: str, db: Session = Depends(get_db)):
    """List all snapshots for a field, newest first."""
    _validate_ai_field(field_name)
    rows = (
        db.query(AISettingsHistory)
        .filter(AISettingsHistory.field_name == field_name)
        .order_by(AISettingsHistory.created_at.desc(), AISettingsHistory.id.desc())
        .all()
    )
    current = _get_current_ai_snapshot(db, field_name)
    return AISettingHistoryResponse(
        field_name=field_name,
        current_history_id=current.id if current else None,
        history=[AISettingHistoryEntry.model_validate(r) for r in rows],
    )


@router.post("/api/settings/ai/{field_name}/rollback", response_model=AISettingState)
def rollback_ai_setting(field_name: str, payload: AISettingRollback, db: Session = Depends(get_db)):
    """Make an existing snapshot the active version — no new history row."""
    _validate_ai_field(field_name)
    row = db.get(AISettingsHistory, payload.history_id)
    if not row or row.field_name != field_name:
        raise HTTPException(status_code=404, detail="History entry not found")

    row.last_used_at = now_utc()
    _set_ai_pointer(db, field_name, row.id)
    db.commit()
    db.refresh(row)
    return AISettingState(field_name=field_name, value=row.value, history_id=row.id)


# --- LLM Config (versioned provider + model, coupled as a single snapshot) ---

LLM_CONFIG_POINTER_KEY = f"current_{LLM_CONFIG_FIELD}_history_id"


def _get_current_llm_snapshot(db: Session) -> LLMSettingsHistory | None:
    """Resolve the active llm_settings_history row via its settings pointer."""
    pointer = db.query(Setting).filter(Setting.key == LLM_CONFIG_POINTER_KEY).first()
    if not pointer:
        return None
    return db.get(LLMSettingsHistory, int(pointer.value))


def _llm_config_from_row(row: LLMSettingsHistory) -> dict:
    """Unpack a snapshot row's coupled JSON value into its fields.

    The ``.get()`` defaults are a safety net for a row written between deploy
    and migration (or if the migration is ever skipped) — every row the
    migration touches already carries both keys.
    """
    config = json.loads(row.value)
    return {
        "provider": config.get("provider", ""),
        "model": config.get("model", ""),
        "max_tokens": config.get("max_tokens", DEFAULT_LLM_MAX_TOKENS),
        "max_tokens_mode": config.get("max_tokens_mode", "custom"),
    }


def _apply_llm_config(
    db: Session, provider: str, model: str, max_tokens: int, max_tokens_mode: str
) -> None:
    """Write the resolved config into the plain settings keys read by the generator.

    Each provider owns its own max-tokens key, so switching providers never
    lets one provider's budget leak onto the other.
    """
    _upsert_setting(db, "llm_provider", provider)
    model_key = "llm_anthropic_model" if provider == "anthropic" else "llm_local_model"
    _upsert_setting(db, model_key, model)
    max_tokens_key = (
        "llm_anthropic_max_tokens" if provider == "anthropic" else "llm_local_max_tokens"
    )
    _upsert_setting(db, max_tokens_key, str(max_tokens))
    if provider == "anthropic":
        _upsert_setting(db, "llm_anthropic_max_tokens_mode", max_tokens_mode)


def _resolve_model_max_tokens(model: str, api_key: str) -> int:
    """Resolve a model's maximum output tokens via the Anthropic Models API.

    Raises HTTPException(400) with a message safe to show the operator inline
    on any failure — an unrecognized model name, a missing key, or any other
    provider-side error. Only Anthropic publishes this endpoint, so this is
    never called for the local provider.
    """
    if not model:
        raise HTTPException(status_code=400, detail="Set a model before resolving its maximum.")
    if not api_key:
        raise HTTPException(
            status_code=400,
            detail="Set an Anthropic API key before resolving the model's maximum.",
        )

    import anthropic

    client = anthropic.Anthropic(api_key=api_key)
    try:
        info = client.models.retrieve(model)
    except Exception as e:
        raise HTTPException(
            status_code=400, detail=f"Couldn't look up {model}'s maximum: {e}"
        ) from None
    return info.max_tokens


@router.get("/api/settings/llm/config", response_model=LLMConfigState)
def get_llm_config(db: Session = Depends(get_db)):
    """Get the currently active LLM provider + model configuration."""
    row = _get_current_llm_snapshot(db)
    if not row:
        return LLMConfigState(provider="", model="", max_tokens=None, max_tokens_mode=None)
    return LLMConfigState(**_llm_config_from_row(row), history_id=row.id)


@router.put("/api/settings/llm/config", response_model=LLMConfigState)
def save_llm_config(payload: LLMConfigUpdate, db: Session = Depends(get_db)):
    """Explicitly save the LLM config — inserts a new history snapshot.

    In ``model_max`` mode, ``max_tokens`` is resolved from the Anthropic Models
    API here, at save time — the generator later reads the stored number rather
    than re-resolving on every run. The mode only applies to Anthropic; a local
    save always writes ``custom`` regardless of what the client sends.
    """
    mode = payload.max_tokens_mode if payload.provider == "anthropic" else "custom"
    max_tokens = payload.max_tokens
    if mode == "model_max":
        api_key_row = db.query(Setting).filter(Setting.key == "llm_anthropic_api_key").first()
        api_key = api_key_row.value if api_key_row else ""
        max_tokens = _resolve_model_max_tokens(payload.model, api_key)
    elif max_tokens <= 0:
        # Only validated in custom mode — model_max ignores the submitted
        # value entirely (the browser sends a blank/0 placeholder while it's
        # unresolved, and that must not fail the request before it gets here).
        raise HTTPException(status_code=422, detail="max_tokens must be a positive integer.")

    now = now_utc()  # Single timestamp so created_at == last_used_at on insert
    row = LLMSettingsHistory(
        field_name=LLM_CONFIG_FIELD,
        value=json.dumps(
            {
                "provider": payload.provider,
                "model": payload.model,
                "max_tokens": max_tokens,
                "max_tokens_mode": mode,
            }
        ),
        created_at=now,
        last_used_at=now,
    )
    db.add(row)
    db.flush()  # Assign row.id before pointing the setting at it
    _upsert_setting(db, LLM_CONFIG_POINTER_KEY, str(row.id))
    _apply_llm_config(db, payload.provider, payload.model, max_tokens, mode)
    db.commit()
    return LLMConfigState(
        provider=payload.provider,
        model=payload.model,
        max_tokens=max_tokens,
        max_tokens_mode=mode,
        history_id=row.id,
    )


@router.get("/api/settings/llm/config/history", response_model=LLMConfigHistoryResponse)
def get_llm_config_history(db: Session = Depends(get_db)):
    """List all LLM configuration snapshots, newest first."""
    rows = (
        db.query(LLMSettingsHistory)
        .filter(LLMSettingsHistory.field_name == LLM_CONFIG_FIELD)
        .order_by(LLMSettingsHistory.created_at.desc(), LLMSettingsHistory.id.desc())
        .all()
    )
    current = _get_current_llm_snapshot(db)
    return LLMConfigHistoryResponse(
        current_history_id=current.id if current else None,
        history=[
            LLMConfigHistoryEntry(
                id=r.id,
                **_llm_config_from_row(r),
                created_at=r.created_at,
                last_used_at=r.last_used_at,
            )
            for r in rows
        ],
    )


@router.post("/api/settings/llm/config/rollback", response_model=LLMConfigState)
def rollback_llm_config(payload: AISettingRollback, db: Session = Depends(get_db)):
    """Make an existing snapshot the active version — restores the whole config together.

    Restores the stored max_tokens verbatim rather than re-resolving it — a
    snapshot is a historical record of what actually ran, and re-resolving on
    rollback would silently change an old configuration.
    """
    row = db.get(LLMSettingsHistory, payload.history_id)
    if not row or row.field_name != LLM_CONFIG_FIELD:
        raise HTTPException(status_code=404, detail="History entry not found")

    row.last_used_at = now_utc()
    _upsert_setting(db, LLM_CONFIG_POINTER_KEY, str(row.id))
    config = _llm_config_from_row(row)
    _apply_llm_config(
        db, config["provider"], config["model"], config["max_tokens"], config["max_tokens_mode"]
    )
    db.commit()
    db.refresh(row)
    return LLMConfigState(**config, history_id=row.id)


# --- Connectivity Tests ---


@router.post("/api/settings/test-llm")
def test_llm_connection(db: Session = Depends(get_db)):
    """Test LLM provider connectivity using current DB settings."""
    settings = {r.key: r.value for r in db.query(Setting).all()}
    provider = settings.get("llm_provider", "local")

    try:
        if provider == "anthropic":
            import anthropic

            api_key = settings.get("llm_anthropic_api_key", "")
            model = settings.get("llm_anthropic_model", "")
            if not api_key or not model:
                return {"success": False, "error": "Missing Anthropic API key or model"}
            client = anthropic.Anthropic(api_key=api_key)
            client.messages.create(
                model=model,
                max_tokens=1,
                messages=[{"role": "user", "content": "hi"}],
            )
            # The verify modal auto-closes on success, so this is the one
            # place a just-resolved "Model maximum" budget is confirmed back
            # to the operator — the helper text under the field is the
            # durable surface for it afterward.
            max_tokens = settings.get("llm_anthropic_max_tokens")
            budget_note = f" (max tokens: {max_tokens})" if max_tokens else ""
            return {"success": True, "message": f"Connected to {model}{budget_note}"}
        else:
            from openai import OpenAI

            base_url = settings.get("llm_local_base_url", "")
            api_key = settings.get("llm_local_api_key", "no-key-needed")
            model = settings.get("llm_local_model", "")
            if not base_url or not model:
                return {"success": False, "error": "Missing base URL or model"}
            client = OpenAI(base_url=base_url, api_key=api_key)
            client.chat.completions.create(
                model=model,
                max_tokens=1,
                messages=[{"role": "user", "content": "hi"}],
            )
            return {"success": True, "message": f"Connected to {model}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.post("/api/settings/test-pushover")
def test_pushover_connection(db: Session = Depends(get_db)):
    """Validate the install's Pushover application token.

    Pushover has a dedicated validation endpoint, but it needs a user key as
    well as a token, so the check sends a real message to the first family
    member who has one. Without any configured key there is nothing to validate
    against, and saying so is more useful than a green tick that proves
    nothing.
    """
    from rally.models import FamilyMember
    from rally.notifications import PushoverError, app_token, send_pushover

    token = app_token(db)
    if not token:
        return {"success": False, "error": "Missing Pushover application token"}

    member = (
        db.query(FamilyMember)
        .filter(FamilyMember.pushover_user_key.isnot(None))
        .filter(FamilyMember.pushover_user_key != "")
        .order_by(FamilyMember.name.asc())
        .first()
    )
    if not member:
        return {
            "success": False,
            "error": "No family member has a Pushover user key yet — add one to test delivery",
        }

    try:
        send_pushover(
            token,
            member.pushover_user_key.strip(),
            "Rally is connected. Event reminders will arrive here.",
            title="Rally",
            device=(member.pushover_device or "").strip() or None,
        )
    except PushoverError as exc:
        return {"success": False, "error": str(exc)}
    return {"success": True, "message": f"Test notification sent to {member.name}"}


@router.get("/api/notifications/overview", response_model=NotificationOverviewResponse)
def notifications_overview(db: Session = Depends(get_db)):
    """Every kind Rally sends, its audience rule, and who currently gets it.

    Read-only on purpose. The editor for a preference is the person's own
    family member record — one editor for one piece of state — and this is the
    screen that answers *"why didn't I get that?"* without making anybody open
    four member modals to find out.
    """
    from rally.notifications import app_token

    return NotificationOverviewResponse(
        token_configured=bool(app_token(db)),
        kinds=[NotificationKindOverview(**row) for row in notification_prefs.overview(db)],
    )


@router.post("/api/settings/test-weather")
def test_weather_connection(db: Session = Depends(get_db)):
    """Test NWS forecast URL connectivity using current DB settings."""
    settings = {r.key: r.value for r in db.query(Setting).all()}

    url = settings.get("weather_nws_url", "")
    if not url:
        return {"success": False, "error": "Missing NWS forecast URL"}

    try:
        import xml.etree.ElementTree as ET

        import requests

        response = requests.get(
            url,
            timeout=10,
            headers={"User-Agent": "Rally family dashboard (https://github.com/pid1/rally)"},
        )
        response.raise_for_status()

        try:
            root = ET.fromstring(response.text)
        except ET.ParseError:
            return {
                "success": False,
                "error": "URL did not return NWS DWML weather data",
            }

        if root.tag != "dwml":
            return {
                "success": False,
                "error": "URL did not return NWS DWML weather data",
            }

        # Surface the current temperature/conditions when available
        current = root.find(".//data[@type='current observations']")
        temp = current.find("parameters/temperature/value") if current is not None else None
        conditions = (
            current.find("parameters/weather/weather-conditions") if current is not None else None
        )
        detail = []
        if temp is not None and temp.text:
            detail.append(f"{temp.text.strip()}\u00b0F")
        if conditions is not None and conditions.get("weather-summary"):
            detail.append(conditions.get("weather-summary"))
        message = "Connected: " + ", ".join(detail) if detail else "Connected to NWS forecast"
        return {"success": True, "message": message}
    except Exception as e:
        return {"success": False, "error": str(e)}


# --- Calendars ---


@router.get("/api/calendars", response_model=list[CalendarResponse])
def list_calendars(db: Session = Depends(get_db)):
    """List all calendar feeds."""
    cals = db.query(Calendar).order_by(Calendar.label.asc()).all()
    return [CalendarResponse.from_calendar(c) for c in cals]


@router.post("/api/calendars", response_model=CalendarResponse, status_code=201)
def create_calendar(cal: CalendarCreate, db: Session = Depends(get_db)):
    """Create a new calendar feed."""
    db_cal = Calendar(
        label=cal.label,
        url=cal.url,
        family_member_id=cal.family_member_id,
        owner_email=cal.owner_email,
        cal_type=cal.cal_type,
        username=cal.username,
        password=cal.password,
    )
    db.add(db_cal)
    db.commit()
    db.refresh(db_cal)
    return CalendarResponse.from_calendar(db_cal)


@router.get("/api/calendars/{cal_id}", response_model=CalendarResponse)
def get_calendar(cal_id: int, db: Session = Depends(get_db)):
    """Get a specific calendar by ID."""
    cal = db.query(Calendar).filter(Calendar.id == cal_id).first()
    if not cal:
        raise HTTPException(status_code=404, detail="Calendar not found")
    return CalendarResponse.from_calendar(cal)


@router.put("/api/calendars/{cal_id}", response_model=CalendarResponse)
def update_calendar(
    cal_id: int,
    cal: CalendarUpdate,
    db: Session = Depends(get_db),
):
    """Update a calendar feed."""
    db_cal = db.query(Calendar).filter(Calendar.id == cal_id).first()
    if not db_cal:
        raise HTTPException(status_code=404, detail="Calendar not found")

    if cal.label is not None:
        db_cal.label = cal.label
    if cal.url is not None:
        db_cal.url = cal.url
    if cal.family_member_id is not None:
        db_cal.family_member_id = cal.family_member_id
    if cal.owner_email is not None:
        db_cal.owner_email = cal.owner_email
    if cal.cal_type is not None:
        db_cal.cal_type = cal.cal_type
    if cal.username is not None:
        db_cal.username = cal.username
    if cal.password is not None:
        db_cal.password = cal.password

    db.commit()
    db.refresh(db_cal)
    return CalendarResponse.from_calendar(db_cal)


@router.delete("/api/calendars/{cal_id}", status_code=204)
def delete_calendar(cal_id: int, db: Session = Depends(get_db)):
    """Delete a calendar feed, or a native calendar and everything in it.

    SQLite does not enforce foreign keys here, so deleting a native calendar
    cascades explicitly: leaving events behind would make them invisible
    everywhere and undeletable from the UI.
    """
    db_cal = db.query(Calendar).filter(Calendar.id == cal_id).first()
    if not db_cal:
        raise HTTPException(status_code=404, detail="Calendar not found")

    if (db_cal.cal_type or "ics") == "native":
        from rally.models import Event, EventAttendee, EventNotification, EventOverride

        event_ids = [row.id for row in db.query(Event).filter(Event.calendar_id == db_cal.id).all()]
        if event_ids:
            for model in (EventAttendee, EventOverride, EventNotification):
                db.query(model).filter(model.event_id.in_(event_ids)).delete(
                    synchronize_session=False
                )
            db.query(Event).filter(Event.id.in_(event_ids)).delete(synchronize_session=False)

    db.delete(db_cal)
    db.commit()
    return None


@router.post("/api/calendars/{cal_id}/test")
def test_calendar_connection(cal_id: int, db: Session = Depends(get_db)):
    """Test calendar feed connectivity for a specific calendar."""
    cal = db.query(Calendar).filter(Calendar.id == cal_id).first()
    if not cal:
        raise HTTPException(status_code=404, detail="Calendar not found")

    cal_type = cal.cal_type or "ics"

    try:
        if cal_type == "ics":
            import requests

            response = requests.get(cal.url, timeout=10)
            response.raise_for_status()
            if "BEGIN:VCALENDAR" not in response.text[:1000]:
                return {"success": False, "error": "URL did not return valid calendar data"}
            return {"success": True, "message": "Calendar feed connected"}

        elif cal_type in ("caldav_google", "caldav_apple"):
            if not cal.username or not cal.password:
                return {"success": False, "error": "Missing CalDAV credentials"}
            import caldav

            default_url = (
                "https://apidata.googleusercontent.com/caldav/v2/"
                if cal_type == "caldav_google"
                else "https://caldav.icloud.com/"
            )
            client = caldav.DAVClient(
                url=cal.url or default_url,
                username=cal.username,
                password=cal.password,
            )
            principal = client.principal()
            server_cals = principal.calendars()
            count = len(server_cals)
            return {"success": True, "message": f"Connected: {count} calendar(s) found"}

        elif cal_type == "native":
            # Nothing to connect to. The useful answer for a Rally-owned
            # calendar is how much is in it, so the button still means
            # something in the same place on the same screen.
            from rally.models import Event

            count = db.query(Event).filter(Event.calendar_id == cal.id).count()
            return {"success": True, "message": f"Rally calendar with {count} event(s)"}

        else:
            return {"success": False, "error": f"Unknown calendar type: {cal_type}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


# --- Followed teams -------------------------------------------------------------
#
# CRUD mirroring the calendar endpoints above, plus a connection test. Teams are
# added by provider + league + key rather than browsed: a team-search browser is
# an explicit non-goal for v1.


@router.get("/api/followed-teams", response_model=list[FollowedTeamResponse])
def list_followed_teams(db: Session = Depends(get_db)):
    """List every followed team and racing series, active or not."""
    return db.query(FollowedTeam).order_by(FollowedTeam.label.asc()).all()


@router.post("/api/followed-teams", response_model=FollowedTeamResponse, status_code=201)
def create_followed_team(team: FollowedTeamCreate, db: Session = Depends(get_db)):
    """Follow a team or racing series."""
    db_team = FollowedTeam(
        provider=team.provider,
        league=team.league,
        team_key=team.team_key or None,
        label=team.label,
        radio_station=team.radio_station or None,
        active=team.active,
    )
    db.add(db_team)
    db.commit()
    db.refresh(db_team)
    return db_team


@router.put("/api/followed-teams/{team_id}", response_model=FollowedTeamResponse)
def update_followed_team(team_id: int, team: FollowedTeamUpdate, db: Session = Depends(get_db)):
    """Update a followed team. Omitted fields are left alone."""
    db_team = db.query(FollowedTeam).filter(FollowedTeam.id == team_id).first()
    if not db_team:
        raise HTTPException(status_code=404, detail="Followed team not found")

    if team.provider is not None:
        db_team.provider = team.provider
    if team.league is not None:
        db_team.league = team.league
    if team.team_key is not UNSET:
        db_team.team_key = team.team_key or None
    if team.label is not None:
        db_team.label = team.label
    if team.radio_station is not UNSET:
        db_team.radio_station = team.radio_station or None
    if team.active is not None:
        db_team.active = team.active

    db.commit()
    db.refresh(db_team)
    return db_team


@router.delete("/api/followed-teams/{team_id}", status_code=204)
def delete_followed_team(team_id: int, db: Session = Depends(get_db)):
    """Stop following a team. Announcement history is left alone."""
    db_team = db.query(FollowedTeam).filter(FollowedTeam.id == team_id).first()
    if not db_team:
        raise HTTPException(status_code=404, detail="Followed team not found")

    db.delete(db_team)
    db.commit()
    return None


@router.post("/api/followed-teams/{team_id}/test")
def test_followed_team(team_id: int, db: Session = Depends(get_db)):
    """Fetch this team's next two weeks and report what came back.

    The provider keys are undocumented and easy to get subtly wrong — a typo in
    ``team_key`` returns an empty schedule rather than an error, which would
    otherwise present as "the sports section just never mentions the Stars".
    """
    db_team = db.query(FollowedTeam).filter(FollowedTeam.id == team_id).first()
    if not db_team:
        raise HTTPException(status_code=404, detail="Followed team not found")

    from datetime import timedelta
    from zoneinfo import ZoneInfo

    from rally.sports import WINDOW_DAYS, espn, mlb
    from rally.utils.settings import local_timezone_name
    from rally.utils.timezone import today_local

    tz_name = local_timezone_name(db)
    tz = ZoneInfo(tz_name)
    today = today_local(tz_name)

    try:
        adapter = mlb if db_team.provider == "mlb" else espn
        schedule = adapter.fetch_schedule(db_team, tz, today, today + timedelta(days=WINDOW_DAYS))
    except Exception as e:  # noqa: BLE001
        return {"success": False, "error": f"Could not reach the provider: {e}", "events": []}

    events = [
        {
            "date": event.local_date.strftime("%Y-%m-%d"),
            "time": event.local_time,
            "name": event.name,
            "tv": event.tv_label,
            "radio": event.radio_label,
        }
        for event in schedule.window
    ]

    if not events:
        # An empty window is genuinely ambiguous — a wrong key and an off-season
        # team look identical from here — so say so rather than claim failure.
        return {
            "success": True,
            "message": (
                f"Reached the provider, but no events in the next {WINDOW_DAYS} days. "
                "That is expected out of season; if the team is in season, check the "
                "league and team key."
            ),
            "events": [],
        }

    return {
        "success": True,
        "message": f"Found {len(events)} event(s) in the next {WINDOW_DAYS} days.",
        "events": events,
    }
