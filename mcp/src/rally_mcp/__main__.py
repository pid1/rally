"""``python -m rally_mcp``: serve the MCP endpoint with uvicorn."""

import uvicorn

from rally_mcp.config import Settings
from rally_mcp.server import build


def main() -> None:
    settings = Settings.from_env()
    _, app = build(settings)
    # Behind cloudflared: trust its X-Forwarded-* only from the Docker network.
    uvicorn.run(
        app, host="0.0.0.0", port=settings.port, proxy_headers=True, forwarded_allow_ips="*"
    )


if __name__ == "__main__":
    main()
