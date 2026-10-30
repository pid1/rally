# Audit log

Rally records every read and every write of its data, from every source — the
web UI, the API, Claude through the MCP server, and the background jobs — in
its own database (`audit_events`). All household data is treated as
sensitive, so reads are logged too.

Open it from the sidebar: **Audit Log**, under Settings.

## What gets recorded

| Event | When |
|---|---|
| `<entity>.create` / `.update` / `.delete` | A row changed. `before`/`after` hold the changed fields only. |
| `<entity>.read` | A request (or job) read rows of a table. One event per table per request, with the ids returned. |
| `http.request` | Every request: method, path, route and status. A failed or refused request is `failure`/`denied` with the error. |
| `mcp.enabled` / `mcp.disabled` | Someone switched the MCP server on or off. |
| `mcp.connection_authorized` | A household member's MCP connection was seen for the first time. |
| `mcp.service_auth_failed`, `mcp.identity_unresolved`, `mcp.route_not_allowed`, `mcp.disabled`, `mcp.token_validation_failed` | An MCP request was refused, at Rally or at rally-mcp. |
| `alert.sent` / `alert.failed` / `alert.skipped` | An alert was delivered, or could not be. |
| `audit.export` | The log was exported (format, filters, row count). |

Each event also carries who acted (`actor_type`, `actor_id`, `actor_name`),
the `source` (`web`, `api`, `mcp`, `job`), a `request_id` shared by every
event from one request or tool call, and `client_info`: source IP, user agent,
path and method, plus whatever identity could be learned.

## Who acted

- **On the tailnet** Rally still has no login; identity is *recorded, not
  enforced*. Rally asks tailscaled who owns the source address (the LocalAPI
  `whois`, through `RALLY_TAILSCALE_SOCKET`) and records the Tailscale login,
  display name, node name and ID, tailnet IP and OS. Behind Tailscale Serve it
  uses the `Tailscale-User-Login` / `Tailscale-User-Name` headers instead, but
  only from `RALLY_TRUSTED_PROXIES`. A family member whose **Sign-in email**
  (Settings → Family Members) matches the login is named on the event. A
  *tagged* node belongs to no user (Tailscale reports `tagged-devices` for all
  of them), so it is recorded by device, as `device:<node name>`.
- **Through MCP** every call is tied to the household member who approved the
  connection: their Cloudflare Access email and JWT subject, the connection
  id, the Claude client (claude.ai, Claude Desktop, Claude Code), the public IP
  (`CF-Connecting-IP`) and `Cf-Ray`, and the tool and its arguments.
- **Jobs** (the 4 AM generator, the notification loop, the CLI) are recorded
  under the job's own name.

A request whose identity cannot be resolved is still served on the tailnet,
recorded as `unknown`, and flagged on the Audit Log page.

## Guarantees

- **Nothing writes without being logged.** The hooks live in the data layer
  (SQLAlchemy session events), not in routers, so every session Rally opens is
  audited. A change and its audit row commit together; a change that fails
  still leaves a `failure` event.
- **Append-only.** Database triggers refuse `UPDATE` and `DELETE` on
  `audit_events`; Rally has no code path that does either.
- **No secrets.** Passwords, tokens, API keys, Access JWTs, Pushover keys and
  anything named like them are replaced with `[redacted]` before writing,
  including inside JSON values.
- **Retention:** unbounded for now. With two users the table stays small.

## Investigating

The page is a reverse-chronological feed filterable by date range, actor,
source, action, entity, outcome and request. Its URL is the filter, so links
are shareable:

- **Entity history** — "Change history" on any to-do or event opens
  `/audit?entity_type=todos&entity_id=42&include_reads=false`.
- **Request drill-down** — click a request id to see every event from that
  request or tool call. Alerts link straight to it.
- **Export** — CSV or JSON of the current filters (up to 10,000 rows).

Viewing, searching and exporting are audited like any other read.

The API behind the page: `GET /api/audit/events`, `GET /api/audit/facets`,
`GET /api/audit/export?format=csv|json`.

## Alerts

See [mcp.md](mcp.md#alerts): every new MCP connection and every denied MCP
request sends a Pushover alert to the people on the alert list.
