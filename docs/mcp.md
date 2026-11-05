# Claude (MCP)

Rally can let Claude — claude.ai on the web, desktop and mobile, voice, and
Claude Code — read and update the family's to-dos and calendar through a
remote MCP server, added in Claude as a custom connector. Other agents (a
phone assistant, say) can connect the same way with narrower permissions.
Rally itself stays exactly as it was: no login, reachable only over the
tailnet.

Every client connects through a **connection** that a household member set up
in advance on Rally's **Connections** page: its own URL, how it signs in, which
tools it may use and which addresses it may connect from. See
[Connections](#connections).

```
Claude ──HTTPS──▶ Cloudflare edge ──▶ Cloudflare Access (Managed OAuth, GitHub login;
   /mcp/c/<id>                            │  bypassed only on token connections' paths)
                                          ▼
                              Cloudflare Tunnel (cloudflared, next to Rally)
                                          │ rally_mcp_edge network
                                          ▼
                                 rally-mcp (mcp/) ── validates the Access JWT again (or takes
                                          │          the bearer token), asks Rally about
                                          │          the connection, lists only its tools
                                          │ rally_mcp_backend network (internal)
                                          ▼
                                 Rally API ── checks rally-mcp's service secret, the
                                              connection, its IP allowlist and token,
                                              the tool's permission, maps the email
                                              to a family member, audits everything
```

- **rally-mcp** (`mcp/`, image `ghcr.io/pid1/rally-mcp`) speaks MCP over
  Streamable HTTP at each connection's `/mcp/c/<connection id>` (and the
  legacy `/mcp`) and calls Rally's existing API. It validates Cloudflare
  Access's JWT, and asks Rally whether each request's connection may be served.
- **Cloudflare Tunnel** publishes only rally-mcp, at
  `https://<your hostname>/mcp/c/<id>`. No router ports are opened and nothing
  else on the server is reachable.
- **Cloudflare Access with Managed OAuth** is the OAuth authorization server,
  including the Dynamic Client Registration Claude requires. GitHub is the only
  login method and the Allow policy names exactly the household's emails.

## Tools

Claude can do almost anything in Rally that a person can in the web UI: about
120 tools across to-dos and recurring to-dos, the calendar, daily notes, the
meal planner, the shopping list, packing lists, emergency preparedness,
followed teams, the AI briefing's settings and the dashboard.

**Hand-written tools** cover the everyday requests, tuned for Claude: names
instead of ids, pagination, and calendar content marked field by field.

| Tool | Does | Annotations | Scope |
|---|---|---|---|
| `list_todos` | Open to-dos plus those completed today, paginated | read-only | `todos:read` |
| `create_todo` | New to-do with optional due date and assignee | write | `todos:write` |
| `update_todo` | Change title, description, due date or assignee | write | `todos:write` |
| `complete_todo` | Mark done (or not done) | write | `todos:write` |
| `delete_todo` | Delete a to-do | **destructive** | `todos:write` |
| `list_events` | Occurrences between two dates (≤ 31 days), paginated | read-only | `calendar:read` |
| `create_event` | New event on the family's Rally calendar | write | `calendar:write` |
| `get_daily_summary` | Today's generated summary and the Daily Note | read-only | `dashboard:read` |

**Generated tools** cover everything else, one per API operation, for example
`shopping_list_items`, `shopping_create_item`, `prep_list_items`,
`prep_refresh_item`, `create_note`, `create_meal_plan`, `packing_list_days`,
`packing_check_all`, `update_event`. Rally decides the list
(`src/rally/audit/mcp_policy.py`) and serves it to rally-mcp as an OpenAPI
document at start-up, so a new Rally feature becomes a Claude tool without
touching rally-mcp. Restart rally-mcp after upgrading Rally to pick them up.
Every `GET` is read-only and every delete is marked destructive, as are
packing-list resets and removals. Tools that send pushes or call out (notify
attendees, run the preparedness digest or review, regenerate the dashboard,
sync calendars) are marked as reaching the outside world.

**What Claude cannot reach**, by design:

| Area | Why |
|---|---|
| Raw settings, LLM settings, connectivity tests | They hold API keys and the Pushover token in plaintext |
| Calendars | CalDAV passwords and private feed URLs |
| Changing family members | Pushover keys, and the sign-in emails MCP identity is resolved by. Reading the family (names, colors) is allowed, without Pushover keys |
| Devices | A device id is the browser's own identity token |
| The audit log | It is for people to read (out of scope for v1) |
| MCP's own switch and plumbing | Claude cannot turn itself on, or see its own gate |
| Connections | Claude cannot create, widen or re-enable its own connection, or see its tokens |

Rally enforces the same list. An MCP request for any of these is refused,
logged and alerted, even if rally-mcp were compromised.

**Prompt injection.** Events from external calendars (ICS, Google, iCloud),
the generated summary, the preparedness review, sports schedules and
calendar-sync messages come back inside `untrusted_external_content`, never
mixed into Rally's own fields. Every such tool's description says that
content is data and never instructions. Results return only what was asked,
a page at a time where Rally pages.

## Connections

A connection is a named client, created in advance on **Connections** (in
Rally's sidebar, under Settings): "Claude", "Home Assistant". Nothing connects
without one. Each has:

| Field | What it is |
|---|---|
| **Name** | What the audit log and alerts call it |
| **Endpoint** | Its own URL, `https://<hostname>/mcp/c/<connection id>`. The path is what binds a request to the connection; no header can change it |
| **Signs in with** | **OAuth**: the Cloudflare Access login (GitHub), for Claude. The household member who signs in first owns it, and nobody else's sign-in is accepted on it. **Token**: a bearer token (`Authorization: Bearer rly_…`), for an agent that cannot sign in. Rally generates it, shows it **once**, and stores only an HMAC of it; it can expire and be revoked |
| **Permissions** | A preset, then adjusted tool by tool (which makes it **Custom**) |
| **Allowed from** | IP addresses or CIDR ranges. **Empty means nowhere.** Buttons add a provider's published ranges |
| **Status** | **Enabled**, **Disabled** (refuses everything, can be re-enabled) or **Revoked** (for good; a token is forgotten) |
| **Authorized by** | The member who completed OAuth on it, or who created its token |

A new connection starts **disabled**, and the global MCP switch in Settings
stays off by default as before: nothing works until both are on.

### Permissions and scopes

Every tool touches one **scope**: `<area>:read` or `<area>:write` for each
area (`todos`, `calendar`, `notes`, `meals`, `shopping`, `packing`,
`preparedness`, `sports`, `dashboard`, `briefing`, `notifications`,
`preferences`, `family`).

| Preset | Tools |
|---|---|
| **Full access** | Every tool, including ones Rally adds later |
| **Read-only** | Every tool whose scope is `:read` |
| **Custom** | Exactly the tools ticked |

Permissions are enforced twice. rally-mcp lists only the connection's tools
in `tools/list` and refuses any other tool even when it is called by name.
Rally then checks every API call again: the connection named, its status, its
credential and allowlist, that the tool is one of the connection's, and that
the route is one that tool may reach (a generated tool its own route, a
hand-written tool routes of its own scope). So a compromised rally-mcp still
cannot do more than the connection it names allows. Every refusal is logged and
alerted.

### IP allowlists and the tunnel

The address checked is the caller's public IP, from Cloudflare's
`CF-Connecting-IP` header. rally-mcp believes that header **only on requests
that arrive from the cloudflared container**: it compares the TCP peer
address with `RALLY_MCP_TRUSTED_PROXIES` (default `rally-mcp-cloudflared`,
the container's name, resolved through Docker's DNS; IPs and CIDRs also work).
From any other peer — Rally on the backend network, say — the header is
ignored and the peer's own address is checked instead, so a direct request
cannot claim an allowed address. rally-mcp runs uvicorn without proxy headers
for the same reason: `X-Forwarded-For` must not change what the peer is.

Provider presets (`rally.connections.IP_PRESETS`):

| Preset | Ranges | Source |
|---|---|---|
| Anthropic (Claude) | `160.79.104.0/21`, `153.61.208.0/20` | Anthropic's published outbound ranges, [platform.claude.com/docs/en/api/ip-addresses](https://platform.claude.com/docs/en/api/ip-addresses), checked 2026-11-05 |

Claude Code on your own machine calls from your own IP, not Anthropic's: add
it to that connection's allowlist (and `--dev-ip` at the edge) while you use it.

### Token connections and Cloudflare Access

A token connection's path has to skip the Access login, since the agent cannot
sign in. `deploy/cloudflare/configure.py` adds one Access application per id
listed in `token_connection_ids` (in `config.toml`), on exactly
`<hostname>/mcp/c/<id>`, with a **Bypass** policy; every other path, OAuth
connections' included, stays behind the household Allow policy. On a
bypassed path the token and the allowlist are the only guard, both checked by
Rally on every request. See [deploy/cloudflare/README.md](../deploy/cloudflare/README.md#token-connections).

### The legacy `/mcp` path

Installs that already had Claude connected keep working: migration 043 turns
the existing `/mcp` into a **legacy** connection (OAuth, Full access, enabled,
allowed from Anthropic's ranges), flagged **Legacy /mcp** on the Connections
page. Unlike a new OAuth connection it is not bound to one person, because
each household member connected their own Claude to that one URL. Re-create
it as a pre-created connection (below), then revoke it. A fresh install has no
legacy connection, so `/mcp` is refused.

### Re-create the Claude connection

Do this once per person, on their own Claude account, after upgrading:

1. **Connections → New Connection.** Name it `Claude` (or `Claude - Jon`),
   **Signs in with: OAuth**, **Permissions: Full access**, then **Add
   Anthropic (Claude) ranges** under *Allowed from*. Tick **Enable it now**
   and Save.
2. Set **rally-mcp address** at the bottom of the page if it is empty
   (`https://rally-mcp.example.com`), and copy the connection's URL:
   `https://rally-mcp.example.com/mcp/c/<id>`.
3. In Claude: **Settings → Connectors**, remove the old Rally connector, then
   **Add custom connector** with the new URL and connect. Sign in with GitHub
   as before. That sign-in binds the connection to you.
4. Check the Audit Log for `mcp.connection_authorized` naming the new
   connection, and the Connections page for your sign-in under it.
5. When everyone has moved, **Revoke** the *Claude (legacy /mcp)* connection.
   `/mcp` then answers every request with a refusal (and an alert).

## Setting it up

About half an hour, once. You can also hand it to an agent: see [Agent instructions](#agent-instructions) below.

### What you need

- Rally running in Docker, on a host that can also run two more containers.
- A Cloudflare account (the Free plan is enough) with a domain on it, and
  **Zero Trust** enabled. Its team domain looks like `myteam.cloudflareaccess.com`.
- A GitHub account for each person who should have access. Note each
  account's **verified primary email**: that address, not the username, is
  what Cloudflare checks.
- Optionally, Pushover, for the alerts on new connections and refused requests.
- `python3` (3.11 or newer) on the machine where you run the Cloudflare script.

Throughout, `rally-mcp.example.com` stands for the hostname you choose,
`myteam.cloudflareaccess.com` for your team domain, and `you@example.com` for
a household member's GitHub email.

### 1. Make a Cloudflare API token

In the Cloudflare dashboard: **My Profile → API Tokens → Create Token →
Create Custom Token**. Give it an expiry (30 days is plenty) and these
permissions:

| Scope | Permissions (all **Edit**) |
|---|---|
| Account | Cloudflare Tunnel; Access: Apps and Policies; Access: Organizations, Identity Providers, and Groups; Account Firewall Access Rules |
| Zone (just your domain) | DNS; Zone WAF; Firewall Services; Bot Management; Zone Settings |

Save the token to a file only you can read. Run this, paste the token at the
prompt (nothing echoes), and press Enter:

```bash
mkdir -p ~/.config/cloudflare && (umask 077; read -rs "?Token: " t && print -rn -- "$t" > ~/.config/cloudflare/rally-mcp.token)
```

(That is zsh, the macOS default. In bash, use `read -rsp "Token: " t && printf %s "$t" > …`.)

### 2. Make a GitHub OAuth App

GitHub → **Settings → Developer settings → OAuth Apps → New OAuth App**:

- **Homepage URL**: `https://rally-mcp.example.com`
- **Authorization callback URL**: `https://myteam.cloudflareaccess.com/cdn-cgi/access/callback`

After registering it, note the **Client ID**, click **Generate a new client
secret**, and save the secret the same way. It is 40 characters:

```bash
(umask 077; read -rs "?Secret: " t && print -rn -- "$t" > ~/.config/cloudflare/rally-mcp-github.secret)
```

### 3. Configure Cloudflare

```bash
cp deploy/cloudflare/config.example.toml deploy/cloudflare/config.toml
```

Fill in `deploy/cloudflare/config.toml`; it is gitignored, so your values stay
out of the repository:

- `account_id`: the 32-character id in the dashboard URL.
- `zone_id`: Domain → Overview → API.
- `hostname`: e.g. `rally-mcp.example.com`.
- `team_domain`: Zero Trust → Settings.
- `household_emails`: exactly the people allowed in.
- `github_client_id`: from step 2.

Then run:

```bash
python3 deploy/cloudflare/configure.py
```

It sets up the tunnel, DNS, GitHub as the only login method, an Allow policy
for exactly those emails, the Access application with Managed OAuth (the
OAuth server Claude needs, including dynamic client registration), and edge
rules. Those rules turn bot blocking off, allow Anthropic's IP ranges, block
every path but `/mcp`, `/mcp/c/*` and the OAuth discovery documents, and
rate-limit the MCP paths. It also gives each token connection listed in
`token_connection_ids` its Access bypass. The script is safe to re-run after any change. It prints the Access
application's **AUD tag** and writes the tunnel token to
`~/.config/cloudflare/rally-mcp-tunnel.token`; you need both in the next step.
Each setting is explained in
[deploy/cloudflare/README.md](../deploy/cloudflare/README.md).

To test from Claude Code on your own machine, add `--dev-ip <your public IP>`.
The MCP paths only accept Anthropic's addresses (and `extra_mcp_ips`) at the
edge otherwise. Re-run without it when
you're done.

### 4. Run rally-mcp next to Rally

1. **Generate a shared secret.** rally-mcp proves itself to Rally with it:

   ```bash
   python3 -c "import secrets; print(secrets.token_urlsafe(48))"
   ```

2. **Give Rally the secret.** Add the environment variable
   `RALLY_MCP_SERVICE_TOKEN=<secret>` to the Rally container. If Rally's host
   is on Tailscale, also mount tailscaled's socket read-only, so the audit log
   can name the device behind every tailnet request:
   `-v /var/run/tailscale/tailscaled.sock:/var/run/tailscale/tailscaled.sock:ro`.

3. **Create the networks** (once):

   ```bash
   docker network create rally_mcp_edge
   docker network create --internal rally_mcp_backend
   ```

   `rally_mcp_edge` holds the tunnel and rally-mcp. `rally_mcp_backend` is
   internal, with no internet access, and connects rally-mcp to Rally and
   nothing else.

4. **Start rally-mcp and its tunnel.** Both need `RALLY_MCP_PUBLIC_HOST`,
   `CF_ACCESS_TEAM_DOMAIN`, `CF_ACCESS_AUD` and the same
   `RALLY_MCP_SERVICE_TOKEN` (rally-mcp), and `TUNNEL_TOKEN` (the tunnel).

   - **Unraid:** copy the two templates in `deploy/rally-mcp/unraid/` to
     `/boot/config/plugins/dockerMan/templates-user/` on the flash drive.
     Then, in the Docker tab, choose **Add Container**, pick
     `my-rally-mcp`, fill in the values and apply. Do the same for
     `my-rally-mcp-cloudflared`. The templates carry the networks and the
     hardening (`--read-only --cap-drop ALL --security-opt
     no-new-privileges:true`) in **Extra Parameters**; keep them.
   - **Anywhere else, with Docker Compose:** copy `deploy/rally-mcp/` to
     the host, `cp .env.example .env`, fill it in, keep it private
     (`chmod 600 .env`, never commit it) and run `docker compose up -d`.

5. **Attach Rally to the backend network.** Rally keeps its own network for
   the web UI and its outbound calls, and joins this one as a second network:

   ```bash
   docker network create rally_net    # once; skip if Rally already has its own network
   ```

   Then recreate Rally with `--network=rally_net --network=rally_mcp_backend`
   (Docker won't combine the default `bridge` with named networks). On
   **Unraid**, put exactly that in the Rally template's **Extra Parameters**,
   because Unraid ignores the template's own network once Extra Parameters
   names one. Also set **Settings → Docker → Preserve user defined networks**
   to **Yes**, or Unraid deletes the networks whenever Docker restarts.

6. **Check the tunnel.** `docker logs rally-mcp-cloudflared` should show four
   "Registered tunnel connection" lines, and

   ```bash
   curl -si https://rally-mcp.example.com/mcp/c/check | head -1
   ```

   should answer `401` from Cloudflare Access (or `403` from a machine
   outside the allowlist), never a timeout.

### 5. Turn it on in Rally

1. **Settings → Family Members**: give each person their **Sign-in email**,
   the verified primary GitHub email from `household_emails`. Rally
   attributes MCP calls by this address, and refuses calls from an email that
   matches nobody.
2. **Settings → Claude (MCP)**:
   - Tick **Let Claude read and update Rally through the MCP server**.
   - Choose who gets **access alerts** (each needs a Pushover key).
   - Set **Rally address for alert links** to the address you open Rally at,
     e.g. `http://rally.myhouse.ts.net:8000`.
   - Save.

   Turning MCP on or off is itself recorded in the audit log.

### 6. Connect Claude

First create the connection: **Connections → New Connection**, OAuth, Full
access (or less), the Anthropic ranges, enabled (see [Re-create the Claude
connection](#re-create-the-claude-connection) for the same steps). The
connector URL is that connection's endpoint:
`https://rally-mcp.example.com/mcp/c/<id>`.

- **claude.ai** (and with it Claude Desktop and the mobile apps):
  1. **Settings → Connectors → Add custom connector**. Name it Rally, paste
     the URL, and leave the OAuth client ID and secret empty, because Claude
     registers itself with Cloudflare.
  2. Click **Connect**. A Cloudflare page sends you to GitHub; sign in as one
     of the household accounts.
  3. Under the connector's **Tool permissions**, choose what Claude may run
     without asking. A reasonable start: read-only tools allowed, write and
     delete tools on **Needs approval**.

  Each person connects from their own Claude account.
- **Claude Code** (only from an IP you added with `--dev-ip`, and to the
  connection's allowlist):

  ```bash
  claude mcp add --transport http rally https://rally-mcp.example.com/mcp/c/<id>
  ```

  Then run `/mcp` in Claude Code, pick **rally** and choose **Authenticate**.
  A browser opens for the same GitHub sign-in.

Then ask Claude something like "What's on Rally's to-do list?".

### 7. Check it worked

Open **Audit Log** in Rally's sidebar. You should see:

- `mcp.connection_authorized` for you, the client and the connection. A
  Pushover alert goes to everyone on the alert list.
- One `http.request` per tool call, under your name, with the tool, its
  arguments, the connection, the client, its IP and the result's size.

**Connections** lists each connection with the people and clients that have
signed in to it.

### Troubleshooting

| Symptom | Cause and fix |
|---|---|
| claude.ai: "Your account was authorized, but no MCP server was found at the provided URL" | The connector URL is not a connection's full endpoint. Remove the connector and add it again with the URL from the Connections page. |
| `403` and `mcp.connection_unknown` in the audit log | The URL names no connection Rally has (or it is the legacy `/mcp` on an install without one). Create the connection first. |
| `403` and `mcp.connection_disabled` / `mcp.connection_revoked` | Enable it on the Connections page (a revoked one has to be created again). |
| `403` and `mcp.ip_rejected` | The caller's address is not on the connection's allowlist (empty means nowhere). Add it, or the provider's preset. If the rejected address is a Docker one (`172.…`), rally-mcp is not recognising cloudflared: check `RALLY_MCP_TRUSTED_PROXIES` and the cloudflared container's name. |
| `403` and `mcp.connection_mismatch` | Someone else already signed in to that OAuth connection. Each person creates their own. |
| `401` and `connection.token_rejected` | Wrong, revoked or expired token on a token connection. Create a new connection for a new token. |
| A tool is missing from Claude's list, or `mcp.permission_denied` | The connection's permissions don't include it. Edit the connection. |
| GitHub sign-in succeeds, then Cloudflare says access denied | That GitHub account's primary email isn't in `household_emails`. Fix `config.toml` and re-run the script. |
| Every tool call fails with "MCP is disabled" | Tick the switch in **Settings → Claude (MCP)**. |
| Calls fail with "No household member has the email …" | Set that person's **Sign-in email** in Settings → Family Members. |
| Claude Code gets `403` from Cloudflare | Your IP isn't on the edge allowlist. Re-run `configure.py --dev-ip <your IP>`, and add the IP to the connection too. |
| `401` on every call, and "token validation failed" events in the audit log | `CF_ACCESS_AUD` or `CF_ACCESS_TEAM_DOMAIN` in rally-mcp's `.env` doesn't match the Access app. Copy the AUD the script printed, then restart rally-mcp. |
| "Missing or invalid rally-mcp service credential" | `RALLY_MCP_SERVICE_TOKEN` differs between Rally and rally-mcp. |
| The tunnel never registers | The `TUNNEL_TOKEN` is wrong, or the host can't reach Cloudflare on outbound 7844 or 443. |
| claude.ai's sign-in popup ends on an error page for your hostname's root, instead of returning to claude.ai | That is [anthropics/claude-ai-mcp#410](https://github.com/anthropics/claude-ai-mcp/issues/410). Note the final URL and any `ofid_` reference; Claude Code is unaffected. |

### Turning it off, revoking, updating

- **Off for everyone, at once:** untick the switch in Settings. Every MCP
  request is then refused and logged.
- **One connection:** **Disable** or **Revoke** it on the Connections page.
  The next request on it is refused, at rally-mcp and at Rally.
- **One person:** remove their email from `household_emails` and re-run the
  script, or revoke their session in Zero Trust → Access → Users, and revoke
  their connections. They can also disconnect the connector in Claude.
- **Updating:** a push to `main` publishes both images. On Unraid, rally-mcp
  and its tunnel are templates like Rally, so CA Auto Update keeps all three
  current: tick `rally`, `rally-mcp` and `rally-mcp-cloudflared` under
  Settings → Auto Update Applications → Docker. The tunnel template follows
  `cloudflare/cloudflared:latest`. With Compose, pull and re-run
  `docker compose up -d`.

### Agent instructions

Paste this into an AI agent that can drive a web browser, run commands on your
machine, and reach the Docker host (SSH or a terminal there). Fill in the four
capitalized values first.

```text
Set up Rally's MCP connector, following docs/mcp.md ("Setting it up") in https://github.com/pid1/rally exactly.
HOSTNAME is the public hostname to use (inside a domain already on my Cloudflare account). TEAM_DOMAIN is my Cloudflare Zero Trust team domain. EMAILS are the GitHub primary emails of the people allowed in. DOCKER_HOST is how you reach the machine running Rally's container.

Rules:
- Ask me before any step that needs a password, a two-factor code, or creating an account, and let me do those myself.
- Never print, paste into chat, or put on a command line any secret: the Cloudflare API token, the GitHub client secret, the tunnel token, or the rally-mcp service secret. When one is shown in the browser, ask me to paste it into a hidden terminal prompt that writes it to the file the guide names.
- Back up Rally's database (sqlite3 .backup) before recreating the Rally container. Tell me before anything that restarts Docker itself.
- Do not commit deploy/cloudflare/config.toml or deploy/rally-mcp/.env.

1. Clone the repo. Read docs/mcp.md and deploy/cloudflare/README.md in full.
2. In the Cloudflare dashboard, create the custom API token from step 1 with exactly those permissions and a 30-day expiry. Have me save it to ~/.config/cloudflare/rally-mcp.token.
3. On GitHub, create the OAuth App from step 2. Have me save its client secret to ~/.config/cloudflare/rally-mcp-github.secret.
4. Write deploy/cloudflare/config.toml from config.example.toml with my values, and run python3 deploy/cloudflare/configure.py. Report its output, and note the AUD tag.
5. On DOCKER_HOST, do step 4 of the guide: generate the service secret there, set it on the Rally container, write rally-mcp's .env (chmod 600), start rally-mcp and the tunnel, and attach Rally to rally_mcp_backend. On Unraid, use the two templates in deploy/rally-mcp/unraid/, the Extra Parameters and the "Preserve user defined networks" setting the guide describes, and add rally-mcp and rally-mcp-cloudflared to CA Auto Update.
6. Check: four registered tunnel connections, and curl -si https://HOSTNAME/mcp/c/check answering 401 from Cloudflare Access.
7. Tell me to do step 5 (Rally Settings) and step 6 (create the connection on Rally's Connections page, then connect Claude) myself, then confirm in Rally's Audit Log that mcp.connection_authorized appears for my connection.
```

## Alerts

A Pushover alert goes to each person on the alert list (Settings → Claude
(MCP)), at their own Pushover key and device, for:

- a connection created (with or without a token), changed (permissions,
  allowlist, enabled or disabled, renamed) or revoked;
- a first sign-in on a connection (one person on one client: a token refresh
  is not a new sign-in), and a token used for the first time;
- every refusal: an unknown, disabled or revoked connection, an address off
  the allowlist, a token rejected or expired, someone else's sign-in, a tool
  the connection may not use, MCP switched off.

Each alert says who, which connection and client, from which IP and when, and
links to the matching events in the Audit Log. Sending an alert, and any
delivery failure, is logged.

## Known risk: claude.ai and Access Managed OAuth

[anthropics/claude-ai-mcp#410](https://github.com/anthropics/claude-ai-mcp/issues/410)
reports that claude.ai connector OAuth fails against a Cloudflare Access
Managed OAuth endpoint while Claude Code connects fine to the same URL: after
the Access login the popup lands on the protected resource's root instead of
`https://claude.ai/api/mcp/auth_callback`. The suspected cause is the RFC 8707
`resource` parameter on `/authorize` and `/token`.

Test Claude Code first, then claude.ai web, Desktop and mobile. For each,
record pass/fail, the final redirect URL, any `ofid_` reference shown, and the
cloudflared and Access logs. The root URL answers `403` at the edge (path
lockdown) or `404` from rally-mcp, so the #410 symptom shows as an error page
on `https://<your hostname>/` rather than a return to claude.ai.

Per-connection paths (`/mcp/c/<id>`) are new and have not yet been tried
against a live Access Managed OAuth: the protected resource metadata for them
is `/.well-known/oauth-protected-resource/mcp/c/<id>`, which the edge allows.
Test Claude Code on a new connection first.
