"""Tests for the connectivity-test endpoints using the external-boundary stubs.

Covers POST /api/settings/test-llm, /api/settings/test-weather, and
/api/calendars/{id}/test — none of which touch the network here.
"""

_DWML = (
    "<dwml><data type='current observations'><parameters>"
    "<temperature type='apparent'><value>72</value></temperature>"
    "<weather><weather-conditions weather-summary='Sunny'/></weather>"
    "</parameters></data></dwml>"
)


def _make_calendar(client, member_id, **overrides):
    payload = {"label": "Cal", "url": "https://ex/c.ics", "family_member_id": member_id}
    payload.update(overrides)
    resp = client.post("/api/calendars", json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


# --- test-llm ------------------------------------------------------------------


def test_llm_anthropic_success(client, make_setting, mock_llm):
    make_setting("llm_provider", "anthropic")
    make_setting("llm_anthropic_api_key", "sk-test")
    make_setting("llm_anthropic_model", "claude-x")

    body = client.post("/api/settings/test-llm").json()

    assert body["success"] is True
    assert "claude-x" in body["message"]
    assert mock_llm.calls[0][0] == "anthropic"


def test_llm_local_success(client, make_setting, mock_llm):
    make_setting("llm_provider", "local")
    make_setting("llm_local_base_url", "http://localhost:1234/v1")
    make_setting("llm_local_model", "llama")

    body = client.post("/api/settings/test-llm").json()

    assert body["success"] is True
    assert mock_llm.calls[0][0] == "openai"


def test_llm_missing_config_returns_failure(client, make_setting):
    make_setting("llm_provider", "anthropic")  # no key/model

    body = client.post("/api/settings/test-llm").json()

    assert body["success"] is False
    assert "Missing" in body["error"]


def test_llm_local_missing_config_returns_failure(client, make_setting):
    make_setting("llm_provider", "local")  # no base URL / model

    body = client.post("/api/settings/test-llm").json()

    assert body["success"] is False
    assert "Missing" in body["error"]


def test_llm_client_error_returns_failure(client, make_setting, monkeypatch):
    make_setting("llm_provider", "anthropic")
    make_setting("llm_anthropic_api_key", "sk-test")
    make_setting("llm_anthropic_model", "claude-x")

    import anthropic

    class Boom:
        def __init__(self, **kwargs):
            raise RuntimeError("connection refused")

    monkeypatch.setattr(anthropic, "Anthropic", Boom)

    body = client.post("/api/settings/test-llm").json()

    assert body["success"] is False
    assert "connection refused" in body["error"]


# --- test-weather --------------------------------------------------------------


def test_weather_success(client, make_setting, mock_requests):
    make_setting("weather_nws_url", "https://forecast.example/nws")
    mock_requests.set_response(text=_DWML, status_code=200)

    body = client.post("/api/settings/test-weather").json()

    assert body["success"] is True
    assert "72" in body["message"]


def test_weather_missing_url(client):
    body = client.post("/api/settings/test-weather").json()
    assert body["success"] is False
    assert "Missing" in body["error"]


def test_weather_non_dwml_document(client, make_setting, mock_requests):
    make_setting("weather_nws_url", "https://forecast.example/nws")
    mock_requests.set_response(text="<html>nope</html>", status_code=200)

    body = client.post("/api/settings/test-weather").json()

    assert body["success"] is False
    assert "DWML" in body["error"]


def test_weather_unparseable_xml(client, make_setting, mock_requests):
    make_setting("weather_nws_url", "https://forecast.example/nws")
    mock_requests.set_response(text="not xml <<<", status_code=200)

    body = client.post("/api/settings/test-weather").json()

    assert body["success"] is False
    assert "DWML" in body["error"]


def test_weather_request_error_returns_failure(client, make_setting, mock_requests):
    make_setting("weather_nws_url", "https://forecast.example/nws")
    mock_requests.set_response(status_code=500)  # raise_for_status -> HTTPError

    body = client.post("/api/settings/test-weather").json()

    assert body["success"] is False


# --- calendars/{id}/test -------------------------------------------------------


def test_calendar_test_ics_success(client, make_member, mock_requests):
    member = make_member("Dad")
    cal = _make_calendar(client, member.id, cal_type="ics")
    mock_requests.set_response(text="BEGIN:VCALENDAR\nVERSION:2.0\nEND:VCALENDAR", status_code=200)

    body = client.post(f"/api/calendars/{cal['id']}/test").json()

    assert body["success"] is True


def test_calendar_test_ics_invalid_body(client, make_member, mock_requests):
    member = make_member("Dad")
    cal = _make_calendar(client, member.id, cal_type="ics")
    mock_requests.set_response(text="not a calendar", status_code=200)

    body = client.post(f"/api/calendars/{cal['id']}/test").json()

    assert body["success"] is False


def test_calendar_test_caldav_success(client, make_member, mock_caldav):
    member = make_member("Dad")
    cal = _make_calendar(
        client,
        member.id,
        url="https://dav.example",
        cal_type="caldav_apple",
        username="user",
        password="secret",
    )

    body = client.post(f"/api/calendars/{cal['id']}/test").json()

    assert body["success"] is True
    assert "1 calendar" in body["message"]  # FakePrincipal returns one calendar


def test_calendar_test_caldav_missing_credentials(client, make_member):
    member = make_member("Dad")
    cal = _make_calendar(client, member.id, cal_type="caldav_apple")  # no username/password

    body = client.post(f"/api/calendars/{cal['id']}/test").json()

    assert body["success"] is False
    assert "credentials" in body["error"].lower()


def test_calendar_test_unknown_type(client, make_member):
    member = make_member("Dad")
    cal = _make_calendar(client, member.id, cal_type="mystery")

    body = client.post(f"/api/calendars/{cal['id']}/test").json()

    assert body["success"] is False
    assert "Unknown calendar type" in body["error"]


def test_calendar_test_caldav_error_returns_failure(client, make_member, monkeypatch):
    member = make_member("Dad")
    cal = _make_calendar(
        client,
        member.id,
        url="https://dav.example",
        cal_type="caldav_apple",
        username="user",
        password="secret",
    )

    import caldav

    class Boom:
        def __init__(self, **kwargs):
            raise RuntimeError("dav down")

    monkeypatch.setattr(caldav, "DAVClient", Boom)

    body = client.post(f"/api/calendars/{cal['id']}/test").json()

    assert body["success"] is False
    assert "dav down" in body["error"]


def test_calendar_test_404(client):
    assert client.post("/api/calendars/9999/test").status_code == 404
