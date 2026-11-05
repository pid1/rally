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
| `http.request` | Every request: method, path, route, status and `result_bytes` (the response's size), plus `result_items` when the route reports how many items it returned (`audit_context.note_result_size()`). A failed or refused request is `failure`/`denied` with the error. |
| `mcp.enabled` / `mcp.disabled` | Someone switched the MCP server on or off. |
| `mcp.connection_authorized` | A household member signed in to an MCP connection from a client for the first time. |
| `connection.created` / `connection.updated` / `connection.revoked` | Someone created, changed (permissions, allowlist, status, name) or revoked a connection on the Connections page. The data layer's `connection.create` / `connection.update` diffs sit beside them, with the token hash redacted. |
| `connection.token_created` / `connection.token_first_used` | A token connection's token was created, or used for the first time. |
| `connection.token_rejected` | A token connection's request carried no token, the wrong one, or an expired one. |
| `mcp.connection_unknown`, `mcp.connection_disabled`, `mcp.connection_revoked`, `mcp.connection_mismatch` | A request named no connection Rally has, a disabled or revoked one, or an OAuth connection that belongs to someone else. |
| `mcp.ip_rejected` | A request came from an address not on its connection's allowlist. |
| `mcp.permission_denied` | A tool the connection may not use, or a route that tool may not reach, refused by rally-mcp or by Rally. |
| `mcp.service_auth_failed`, `mcp.identity_unresolved`, `mcp.route_not_allowed`, `mcp.disabled`, `mcp.token_validation_failed` | An MCP request was refused, at Rally or at rally-mcp. |
| `alert.sent` / `alert.failed` / `alert.skipped` | An alert was delivered, or could not be. |
| `audit.export` | The log was exported (format, filters, row count). |
| `busy.check_availability` / `busy.find_free_slots` | A busy-only tool answered (see [mcp.md](mcp.md#busy-only-tools)). `after.result_size` is how many blocks or slots it returned. |
| `busy.book_appointment` | A booking was requested through a connection. `after.event_id` names the event; the booking itself is also an `event.create` (with `source`, the booking connection). |

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
- **Through MCP** every call is tied to its **connection** (`connection_id`,
  `connection_name`, `auth_method` in `client_info`) and to the household
  member behind it: for an OAuth connection the member who signed in (their
  Cloudflare Access email and JWT subject, and the sign-in's `grant_id`), for a
  token connection the connection itself (`actor_id` `connection:<id>`, its
  name as `actor_name`) on behalf of the member who created the token
  (`authorized_by`). Also recorded: the client (claude.ai, Claude Desktop,
  Claude Code), the public IP (`CF-Connecting-IP`, believed only from the
  tunnel) and `Cf-Ray`, the tool and its arguments, and the result's size.
  Busy-only calls are recorded the same way, so each booking or availability
  check names the connection, the IP, the tool, its arguments and how much it
  returned.
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

See [mcp.md](mcp.md#alerts): connections created, changed or revoked, first
sign-ins and first token uses, and every denied MCP request (unknown or
disabled connection, IP off the allowlist, token rejected, permission denied)
send a Pushover alert to the people on the alert list.
