# rally-mcp

Rally's MCP server. Claude connects to it through Cloudflare Access (Managed
OAuth); it validates the Access JWT, checks that MCP is enabled in Rally, and
calls Rally's API on behalf of the household member who signed in. Every call
is recorded in Rally's audit log under that person and connection.

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
| `PORT` | Listen port (default `8080`) |
