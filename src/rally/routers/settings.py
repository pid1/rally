"""Settings and calendars router for Rally."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from rally.database import get_db
from rally.models import Calendar, Setting
from rally.schemas import (
    CalendarCreate,
    CalendarResponse,
    CalendarUpdate,
    SettingsResponse,
    SettingsUpdate,
)

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
        existing = db.query(Setting).filter(Setting.key == key).first()
        if existing:
            existing.value = value
        else:
            db.add(Setting(key=key, value=value))
    db.commit()

    rows = db.query(Setting).all()
    return SettingsResponse(settings={r.key: r.value for r in rows})


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
    """Test OpenWeather API connectivity using current DB settings."""
    settings = {r.key: r.value for r in db.query(Setting).all()}

    api_key = settings.get("weather_api_key", "")
    lat = settings.get("weather_lat", "")
    lon = settings.get("weather_lon", "")

    if not all([api_key, lat, lon]):
        return {"success": False, "error": "Missing API key, latitude, or longitude"}

    try:
        import requests

        response = requests.get(
            "https://api.openweathermap.org/data/3.0/onecall",
            params={
                "lat": lat,
                "lon": lon,
                "appid": api_key,
                "units": "imperial",
                "exclude": "minutely,hourly,daily,alerts",
            },
            timeout=10,
        )
        response.raise_for_status()
        data = response.json()
        temp = data.get("current", {}).get("temp", "?")
        weather_list = data.get("current", {}).get("weather", [{}])
        desc = weather_list[0].get("description", "") if weather_list else ""
        return {"success": True, "message": f"Connected: {temp}\u00b0F, {desc}"}
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
