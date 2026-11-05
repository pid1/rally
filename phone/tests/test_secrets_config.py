import json

import pytest

from rally_phone import config, secrets


def test_env_wins(monkeypatch, tmp_path):
    (tmp_path / "dotenv").write_text("elevenlabs=from-dotenv\n")
    monkeypatch.setenv("ELEVENLABS_API_KEY", "from-env")
    assert secrets.get_secret(secrets.ELEVENLABS_API_KEY) == "from-env"


def test_dotenv_then_keychain(monkeypatch, tmp_path):
    (tmp_path / "dotenv").write_text("# c\nopenai=x\nexport elevenlabs = 'from-dotenv'\n")
    assert secrets.get_secret(secrets.ELEVENLABS_API_KEY) == "from-dotenv"
    (tmp_path / "dotenv").write_text("openai=x\n")
    monkeypatch.setattr(secrets, "_keychain", lambda s: f"kc:{s}")
    assert secrets.get_secret(secrets.ELEVENLABS_API_KEY) == "kc:elevenlabs-api-key"


def test_missing_secret_names_where_to_put_it():
    with pytest.raises(secrets.MissingSecret, match="elevenlabs="):
        secrets.get_secret(secrets.ELEVENLABS_API_KEY)
    assert secrets.get_secret(secrets.ELEVENLABS_API_KEY, required=False) is None


def test_redact():
    assert secrets.redact("a SECRET b", ["SECRET", None]) == "a <redacted> b"


def test_settings_from_file_and_env(monkeypatch, tmp_path):
    with pytest.raises(config.NotConfigured):
        config.Settings.load()
    path = config.write_config(
        {"agent_id": "a1", "phone_number_id": "p1", "owner_name": "O", "assistant_number": "+1"}
    )
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    s = config.Settings.load()
    assert (s.agent_id, s.phone_number_id, s.owner_name) == ("a1", "p1", "O")
    monkeypatch.setenv("ELEVENLABS_AGENT_ID", "a2")
    assert config.Settings.load().agent_id == "a2"
    config.write_config({"allowed_country_codes": ["1", "44"]})
    assert config.Settings.load().allowed_country_codes == ("1", "44")
    assert json.loads(path.read_text())["agent_id"] == "a1"
