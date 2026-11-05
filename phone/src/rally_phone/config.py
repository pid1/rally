"""Where rally-phone finds the agent, the phone number and the owner's name.

``scripts/setup.py --apply`` writes ``~/.config/rally-phone/config.json``
(outside the repo; it holds IDs, not secrets). Environment variables override
it. The API key is never stored here: see ``secrets.py``.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

CONFIG_PATH = Path(
    os.environ.get("RALLY_PHONE_CONFIG", Path.home() / ".config" / "rally-phone" / "config.json")
)


class NotConfigured(RuntimeError):
    pass


@dataclass(frozen=True)
class Settings:
    agent_id: str
    phone_number_id: str
    owner_name: str
    assistant_number: str
    base_url: str = "https://api.elevenlabs.io"
    allowed_country_codes: tuple[str, ...] = ("1",)
    ringing_timeout_secs: int = 45

    @classmethod
    def load(cls, path: Path | None = None) -> Settings:
        path = path or CONFIG_PATH
        data: dict = {}
        if path.exists():
            data = json.loads(path.read_text())
        env = {
            "agent_id": os.environ.get("ELEVENLABS_AGENT_ID"),
            "phone_number_id": os.environ.get("ELEVENLABS_PHONE_NUMBER_ID"),
            "owner_name": os.environ.get("RALLY_PHONE_OWNER_NAME"),
            "assistant_number": os.environ.get("RALLY_PHONE_ASSISTANT_NUMBER"),
            "base_url": os.environ.get("ELEVENLABS_BASE_URL"),
        }
        data |= {k: v for k, v in env.items() if v}
        required = ("agent_id", "phone_number_id", "owner_name", "assistant_number")
        missing = [k for k in required if not data.get(k)]
        if missing:
            raise NotConfigured(
                f"rally-phone is not set up ({', '.join(missing)} missing). "
                "Run `uv run scripts/setup.py all --apply` in phone/ first."
            )
        if "allowed_country_codes" in data:
            data["allowed_country_codes"] = tuple(data["allowed_country_codes"])
        known = cls.__dataclass_fields__
        return cls(**{k: v for k, v in data.items() if k in known})


def write_config(values: dict, path: Path | None = None) -> Path:
    path = path or CONFIG_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    current = json.loads(path.read_text()) if path.exists() else {}
    path.write_text(json.dumps(current | values, indent=2) + "\n")
    path.chmod(0o600)
    return path
