"""``python -m rally_mcp``: serve the MCP endpoint with uvicorn."""

import uvicorn

from rally_mcp.config import Settings
from rally_mcp.generated import fetch_spec
from rally_mcp.server import build


def main() -> None:
    settings = Settings.from_env()
    # The tool list is Rally's to decide; fetched once at start (restart
    # rally-mcp after upgrading Rally to pick up new endpoints).
    spec = fetch_spec(settings.rally_url, settings.service_token)
    _, app = build(settings, spec=spec)
    # Behind cloudflared: trust its X-Forwarded-* only from the Docker network.
    uvicorn.run(
        app, host="0.0.0.0", port=settings.port, proxy_headers=True, forwarded_allow_ips="*"
    )


if __name__ == "__main__":
    main()
