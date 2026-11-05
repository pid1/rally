# rally-mcp

Rally's MCP server. Each client connects on its own pre-created connection's
endpoint, `/mcp/c/<connection id>` (the legacy `/mcp` is the implicit legacy
connection): Claude through Cloudflare Access (Managed OAuth), an agent with a
bearer token. rally-mcp validates the Access JWT, asks Rally whether the
connection, credential and IP are allowed, lists and runs only the
connection's tools, and calls Rally's API on behalf of the connection and the
household member behind it. Every call is recorded in Rally's audit log under
that person and connection.

Each hand-written tool declares its scope with `@scoped("<area>:read")`
(`src/rally_mcp/scopes.py`); generated tools take theirs from Rally.

- Setup, tools, connecting Claude: [../docs/mcp.md](../docs/mcp.md)
- Cloudflare configuration: [../deploy/cloudflare/README.md](../deploy/cloudflare/README.md)
- Deployment on the NAS: [../deploy/rally-mcp/docker-compose.yml](../deploy/rally-mcp/docker-compose.yml)

```bash
uv sync
uv run pytest
```

| Variable | Purpose |
|---|---|
| `RALLY_URL` | Rally's API on the private network (default `http://rally:8000`) |
| `RALLY_MCP_SERVICE_TOKEN` | Shared secret; the same value is set on Rally |
| `CF_ACCESS_TEAM_DOMAIN` | e.g. `myteam.cloudflareaccess.com` |
| `CF_ACCESS_AUD` | The Access application's AUD tag |
| `RALLY_MCP_PUBLIC_HOST` | Public hostname, for Host-header checks |
| `RALLY_MCP_TRUSTED_PROXIES` | Peers whose `CF-Connecting-IP` is believed: names, IPs or CIDRs (default `rally-mcp-cloudflared`, the tunnel container) |
| `PORT` | Listen port (default `8080`) |
