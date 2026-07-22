"""Settings and calendars router for Rally."""

import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from rally.database import get_db
from rally.models import AISettingsHistory, Calendar, LLMSettingsHistory, Setting
from rally.schemas import (
    AI_SETTINGS_FIELDS,
    LLM_CONFIG_FIELD,
    AISettingHistoryEntry,
    AISettingHistoryResponse,
    AISettingRollback,
    AISettingState,
    AISettingValueUpdate,
    CalendarCreate,
    CalendarResponse,
    CalendarUpdate,
    LLMConfigHistoryEntry,
    LLMConfigHistoryResponse,
    LLMConfigState,
    LLMConfigUpdate,
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
    """Unpack a snapshot row's coupled JSON value into provider/model."""
    config = json.loads(row.value)
    return {"provider": config.get("provider", ""), "model": config.get("model", "")}


def _apply_llm_config(db: Session, provider: str, model: str) -> None:
    """Write the provider + model into the plain settings keys read by the generator."""
    _upsert_setting(db, "llm_provider", provider)
    model_key = "llm_anthropic_model" if provider == "anthropic" else "llm_local_model"
    _upsert_setting(db, model_key, model)


@router.get("/api/settings/llm/config", response_model=LLMConfigState)
def get_llm_config(db: Session = Depends(get_db)):
    """Get the currently active LLM provider + model configuration."""
    row = _get_current_llm_snapshot(db)
    if not row:
        return LLMConfigState(provider="", model="", history_id=None)
    return LLMConfigState(**_llm_config_from_row(row), history_id=row.id)


@router.put("/api/settings/llm/config", response_model=LLMConfigState)
def save_llm_config(payload: LLMConfigUpdate, db: Session = Depends(get_db)):
    """Explicitly save the LLM provider + model pair — inserts a new history snapshot."""
    now = now_utc()  # Single timestamp so created_at == last_used_at on insert
    row = LLMSettingsHistory(
        field_name=LLM_CONFIG_FIELD,
        value=json.dumps({"provider": payload.provider, "model": payload.model}),
        created_at=now,
        last_used_at=now,
    )
    db.add(row)
    db.flush()  # Assign row.id before pointing the setting at it
    _upsert_setting(db, LLM_CONFIG_POINTER_KEY, str(row.id))
    _apply_llm_config(db, payload.provider, payload.model)
    db.commit()
    return LLMConfigState(provider=payload.provider, model=payload.model, history_id=row.id)


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
    """Make an existing snapshot the active version — restores provider and model together."""
    row = db.get(LLMSettingsHistory, payload.history_id)
    if not row or row.field_name != LLM_CONFIG_FIELD:
        raise HTTPException(status_code=404, detail="History entry not found")

    row.last_used_at = now_utc()
    _upsert_setting(db, LLM_CONFIG_POINTER_KEY, str(row.id))
    config = _llm_config_from_row(row)
    _apply_llm_config(db, config["provider"], config["model"])
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
            return {"success": True, "message": f"Connected to {model}"}
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
    """Delete a calendar feed."""
    db_cal = db.query(Calendar).filter(Calendar.id == cal_id).first()
    if not db_cal:
        raise HTTPException(status_code=404, detail="Calendar not found")

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

        else:
            return {"success": False, "error": f"Unknown calendar type: {cal_type}"}
    except Exception as e:
        return {"success": False, "error": str(e)}
