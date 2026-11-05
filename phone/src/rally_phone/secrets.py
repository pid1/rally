"""Secrets come from the environment, ``~/.env`` or the macOS Keychain, never from arguments.

Lookup order for each secret: the environment variable, then a
``name=value`` line in ``~/.env`` (mode 600; for example
``elevenlabs=<key>``), then a Keychain generic password.

Values are returned to the caller and never logged, printed or put in an
exception message. ``redact`` masks them wherever request details are shown
(``setup.py --dry-run``).
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

# (environment variable, ~/.env key, Keychain service)
ELEVENLABS_API_KEY = ("ELEVENLABS_API_KEY", "elevenlabs", "elevenlabs-api-key")
TWILIO_ACCOUNT_SID = ("TWILIO_ACCOUNT_SID", "twilio_account_sid", "twilio-account-sid")
TWILIO_AUTH_TOKEN = ("TWILIO_AUTH_TOKEN", "twilio_auth_token", "twilio-auth-token")
RALLY_PHONE_MCP_TOKEN = ("RALLY_PHONE_MCP_TOKEN", "rally_phone_mcp_token", "rally-phone-mcp-token")

DOTENV = Path(os.environ.get("RALLY_PHONE_DOTENV", Path.home() / ".env"))

REDACTED = "<redacted>"


class MissingSecret(RuntimeError):
    pass


def _keychain(service: str) -> str | None:
    if shutil.which("security") is None:
        return None
    try:
        out = subprocess.run(
            ["security", "find-generic-password", "-s", service, "-w"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except OSError, subprocess.SubprocessError:
        return None
    value = out.stdout.strip()
    return value or None


def _dotenv(key: str, path: Path | None = None) -> str | None:
    path = path or DOTENV
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return None
    for line in lines:
        name, sep, value = line.strip().removeprefix("export ").partition("=")
        if sep and name.strip() == key:
            return value.strip().strip("'\"") or None
    return None


def get_secret(spec: tuple[str, str, str], *, required: bool = True) -> str | None:
    env_name, dotenv_key, service = spec
    value = os.environ.get(env_name, "").strip() or _dotenv(dotenv_key) or _keychain(service)
    if value is None and required:
        raise MissingSecret(
            f"{env_name} is not set, ~/.env has no '{dotenv_key}=' line and the Keychain has "
            f"no '{service}' item. See phone/README.md (Setup)."
        )
    return value


def redact(text: str, secrets: list[str | None]) -> str:
    for s in secrets:
        if s:
            text = text.replace(s, REDACTED)
    return text
