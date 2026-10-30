"""rally-mcp settings, all from the environment (see deploy/rally-mcp/.env.example)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} must be set")
    return value


@dataclass(frozen=True)
class Settings:
    # Rally's API, reached over rally-mcp's private Docker network.
    rally_url: str
    # Shared with Rally (RALLY_MCP_SERVICE_TOKEN there); the only thing Rally
    # trusts an MCP identity from.
    service_token: str
    # Cloudflare Access: the team domain that signs the JWT and the Access
    # application's AUD tag it must be issued for.
    team_domain: str
    audience: str
    public_host: str = "localhost"
    mcp_path: str = "/mcp"
    port: int = 8080
    # How long rally-mcp trusts Rally's answer to "is MCP enabled?".
    status_ttl_seconds: float = 10.0
    extra_hosts: list[str] = field(default_factory=list)

    @property
    def issuer(self) -> str:
        return f"https://{self.team_domain}"

    @property
    def certs_url(self) -> str:
        return f"https://{self.team_domain}/cdn-cgi/access/certs"

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            rally_url=os.environ.get("RALLY_URL", "http://rally:8000").rstrip("/"),
            service_token=_required("RALLY_MCP_SERVICE_TOKEN"),
            team_domain=_required("CF_ACCESS_TEAM_DOMAIN"),
            audience=_required("CF_ACCESS_AUD"),
            public_host=_required("RALLY_MCP_PUBLIC_HOST"),
            mcp_path=os.environ.get("RALLY_MCP_PATH", "/mcp"),
            port=int(os.environ.get("PORT", "8080")),
            extra_hosts=[
                h.strip()
                for h in os.environ.get("RALLY_MCP_EXTRA_HOSTS", "").split(",")
                if h.strip()
            ],
        )
