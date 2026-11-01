"""One rule for the family's timezone (#279).

The Settings row, else ``config.toml``'s ``local_timezone``, else UTC — asked by
the web app, the generator and the 4 AM job alike. Before, the app fell back to
UTC while the 4 AM job read ``config.toml``, so on an install with no Settings
row the app's "today" ran ahead every evening and Add Packing List refused
today.
"""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from rally.utils.settings import local_timezone_name, resolve_timezone_name, today_local_str

# 8:41 PM CDT on Oct 14 — already Oct 15 in UTC, as in the report.
EVENING = datetime(2026, 10, 15, 1, 41, tzinfo=UTC)


def test_the_settings_row_wins(db_session, local_timezone, config_toml):
    config_toml.write_text('local_timezone = "Europe/London"\n')
    local_timezone("America/Denver")
    assert local_timezone_name(db_session) == "America/Denver"


def test_no_row_falls_back_to_config_toml(db_session, config_toml):
    config_toml.write_text('local_timezone = "America/Chicago"\n')
    assert local_timezone_name(db_session) == "America/Chicago"


def test_an_empty_row_falls_back_to_config_toml(db_session, make_setting, config_toml):
    config_toml.write_text('local_timezone = "America/Chicago"\n')
    make_setting("local_timezone", "")
    assert local_timezone_name(db_session) == "America/Chicago"


def test_neither_is_utc(db_session):
    assert local_timezone_name(db_session) == "UTC"


def test_an_unreadable_config_toml_is_utc(db_session, config_toml):
    config_toml.write_text("local_timezone = \n")
    assert local_timezone_name(db_session) == "UTC"


def test_the_generator_asks_the_same_rule():
    """The generator already holds the parsed file, so it hands it over."""
    assert resolve_timezone_name("America/Denver", {"local_timezone": "UTC"}) == "America/Denver"
    assert resolve_timezone_name(None, {"local_timezone": "America/Chicago"}) == "America/Chicago"
    assert resolve_timezone_name("", {"local_timezone": "America/Chicago"}) == "America/Chicago"
    assert resolve_timezone_name(None, {}) == "UTC"


def test_the_4am_job_asks_the_same_rule():
    entrypoint = (Path(__file__).parent.parent / "entrypoint.sh").read_text()
    assert "configured_timezone_name" in entrypoint
    assert "America/Chicago" not in entrypoint


def test_todays_packing_list_is_accepted_in_the_evening(
    client, db_session, frozen_now, config_toml
):
    """The report: no Settings row, ``config.toml`` in Chicago, 8:41 PM."""
    config_toml.write_text('local_timezone = "America/Chicago"\n')
    frozen_now(EVENING)

    assert today_local_str(db_session) == "2026-10-14"
    assert 'const LOCAL_TIMEZONE = "America/Chicago"' in client.get("/packing-lists").text

    response = client.post(
        "/api/packing-list-days",
        json={"name": "Swim", "date": "2026-10-14", "pack_days_before": 0},
    )
    assert response.status_code == 201, response.text


@pytest.mark.parametrize(
    "page", ["/notes", "/notes/previous", "/meal-planner", "/meal-planner/previous"]
)
def test_pages_are_handed_the_same_zone(client, config_toml, page):
    """These used to read the Settings row in the browser and fall back to UTC."""
    config_toml.write_text('local_timezone = "America/Chicago"\n')
    assert 'const localTimezone = "America/Chicago"' in client.get(page).text


def test_settings_refuses_an_empty_timezone(client):
    for value in ("", "   "):
        response = client.put("/api/settings", json={"settings": {"local_timezone": value}})
        assert response.status_code == 422
        assert response.json()["detail"] == "Timezone is a required field."
    assert "local_timezone" not in client.get("/api/settings").json()["settings"]


def test_settings_refuses_an_unknown_timezone(client):
    response = client.put("/api/settings", json={"settings": {"local_timezone": "Mars/Olympus"}})
    assert response.status_code == 422
    assert "local_timezone" not in client.get("/api/settings").json()["settings"]


def test_settings_saves_a_known_timezone_trimmed(client):
    response = client.put(
        "/api/settings", json={"settings": {"local_timezone": " America/Chicago "}}
    )
    assert response.status_code == 200
    assert response.json()["settings"]["local_timezone"] == "America/Chicago"


def test_settings_without_a_timezone_key_is_unaffected(client):
    response = client.put("/api/settings", json={"settings": {"stem_concept_enabled": "true"}})
    assert response.status_code == 200


def test_the_settings_page_requires_a_timezone(client):
    html = client.get("/settings").text
    assert '<select id="general-timezone" required>' in html
    assert "'Timezone is a required field.'" in html
