# Claude (MCP)

Rally can let Claude — claude.ai on the web, desktop and mobile, voice, and
Claude Code — read and update the family's to-dos and calendar through a
remote MCP server, added in Claude as a custom connector. Rally itself stays
exactly as it was: no login, reachable only over the tailnet.

```
Claude ──HTTPS──▶ Cloudflare edge ──▶ Cloudflare Access (Managed OAuth, GitHub login)
                                          │ only the household
                                          ▼
                              Cloudflare Tunnel (cloudflared, next to Rally)
                                          │ rally_mcp_edge network
                                          ▼
                                 rally-mcp (mcp/) ── validates the Access JWT again
                                          │ rally_mcp_backend network (internal)
                                          ▼
                                 Rally API ── checks rally-mcp's service secret,
                                              maps the email to a family member,
                                              audits everything
```

- **rally-mcp** (`mcp/`, image `ghcr.io/pid1/rally-mcp`) speaks MCP over
  Streamable HTTP at `/mcp` and calls Rally's existing API. Its only auth logic
  is validating Cloudflare Access's JWT.
- **Cloudflare Tunnel** publishes only rally-mcp, at
  `https://<your hostname>/mcp`. No router ports are opened and nothing else
  on the server is reachable.
- **Cloudflare Access with Managed OAuth** is the OAuth authorization server,
  including the Dynamic Client Registration Claude requires. GitHub is the only
  login method and the Allow policy names exactly the household's emails.

## Tools

| Tool | Does | Annotations |
|---|---|---|
| `list_todos` | Open to-dos plus those completed today, paginated | read-only |
| `create_todo` | New to-do with optional due date and assignee | write |
| `update_todo` | Change title, description, due date or assignee | write |
| `complete_todo` | Mark done (or not done) | write |
| `delete_todo` | Delete a to-do | **destructive** |
| `list_events` | Occurrences between two dates (≤ 31 days), paginated | read-only |
| `create_event` | New event on the family's Rally calendar | write |
| `get_daily_summary` | Today's generated summary and the Daily Note | read-only |

**Prompt injection.** Events from external calendars (ICS, Google, iCloud)
and the generated summary come back inside `untrusted_external_content`, never
mixed into Rally's own fields, and every tool description says such content is
data and never instructions. Results return only what was asked, a page at a
time.

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
rules. Those rules turn bot blocking off, allow Anthropic's IP range, block
every path but `/mcp` and the OAuth discovery documents, and rate-limit
`/mcp`. The script is safe to re-run after any change. It prints the Access
application's **AUD tag** and writes the tunnel token to
`~/.config/cloudflare/rally-mcp-tunnel.token`; you need both in the next step.
Each setting is explained in
[deploy/cloudflare/README.md](../deploy/cloudflare/README.md).

To test from Claude Code on your own machine, add `--dev-ip <your public IP>`.
`/mcp` only accepts Anthropic's addresses otherwise. Re-run without it when
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

3. **Set up rally-mcp's folder.** Copy `deploy/rally-mcp/` to the Docker host
   and run `cp .env.example .env`. Fill in `RALLY_MCP_PUBLIC_HOST`,
   `CF_ACCESS_TEAM_DOMAIN`, `TUNNEL_TOKEN`, `CF_ACCESS_AUD` and the same
   `RALLY_MCP_SERVICE_TOKEN`. Keep `.env` private (`chmod 600 .env`) and
   never commit it.

4. **Start it**, with Docker Compose:

   ```bash
   docker compose up -d
   ```

   or, where Compose isn't installed (stock Unraid), with plain Docker:

   ```bash
   ./up.sh
   ```

   Both start `rally-mcp` and `rally-mcp-cloudflared` and create two
   networks. `rally_mcp_edge` holds the tunnel and rally-mcp.
   `rally_mcp_backend` is internal, with no internet access, and connects
   rally-mcp to Rally and nothing else.

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
   curl -si https://rally-mcp.example.com/mcp | head -1
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

The connector URL is your hostname **with `/mcp` on the end**:
`https://rally-mcp.example.com/mcp`.

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
- **Claude Code** (only from an IP you added with `--dev-ip`):

  ```bash
  claude mcp add --transport http rally https://rally-mcp.example.com/mcp
  ```

  Then run `/mcp` in Claude Code, pick **rally** and choose **Authenticate**.
  A browser opens for the same GitHub sign-in.

Then ask Claude something like "What's on Rally's to-do list?".

### 7. Check it worked

Open **Audit Log** in Rally's sidebar. You should see:

- `mcp.connection_authorized` for you and the client. A Pushover alert goes
  to everyone on the alert list.
- One `http.request` per tool call, under your name, with the tool, the client
  and its IP.

**Settings → Claude (MCP) → MCP connections** lists each person and client
Rally has seen.

### Troubleshooting

| Symptom | Cause and fix |
|---|---|
| claude.ai: "Your account was authorized, but no MCP server was found at the provided URL" | The connector URL is missing `/mcp`. Remove the connector and add it again with the full URL. |
| GitHub sign-in succeeds, then Cloudflare says access denied | That GitHub account's primary email isn't in `household_emails`. Fix `config.toml` and re-run the script. |
| Every tool call fails with "MCP is disabled" | Tick the switch in **Settings → Claude (MCP)**. |
| Calls fail with "No household member has the email …" | Set that person's **Sign-in email** in Settings → Family Members. |
| Claude Code gets `403` on `/mcp` | Your IP isn't on the allowlist. Re-run `configure.py --dev-ip <your IP>`. |
| `401` on every call, and "token validation failed" events in the audit log | `CF_ACCESS_AUD` or `CF_ACCESS_TEAM_DOMAIN` in rally-mcp's `.env` doesn't match the Access app. Copy the AUD the script printed, then restart rally-mcp. |
| "Missing or invalid rally-mcp service credential" | `RALLY_MCP_SERVICE_TOKEN` differs between Rally and rally-mcp. |
| The tunnel never registers | The `TUNNEL_TOKEN` is wrong, or the host can't reach Cloudflare on outbound 7844 or 443. |
| claude.ai's sign-in popup ends on an error page for your hostname's root, instead of returning to claude.ai | That is [anthropics/claude-ai-mcp#410](https://github.com/anthropics/claude-ai-mcp/issues/410). Note the final URL and any `ofid_` reference; Claude Code is unaffected. |

### Turning it off, revoking, updating

- **Off for everyone, at once:** untick the switch in Settings. Every MCP
  request is then refused and logged.
- **One person:** remove their email from `household_emails` and re-run the
  script, or revoke their session in Zero Trust → Access → Users. They can
  also disconnect the connector in Claude.
- **Updating:** pull `ghcr.io/pid1/rally-mcp:latest` and re-run
  `docker compose up -d` (or `./up.sh`). Rally updates as usual.

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
5. On DOCKER_HOST, do step 4 of the guide: generate the service secret there, set it on the Rally container, write rally-mcp's .env (chmod 600), start rally-mcp and the tunnel, and attach Rally to rally_mcp_backend. On Unraid, use up.sh, the Extra Parameters and the "Preserve user defined networks" setting the guide describes.
6. Check: four registered tunnel connections, and curl -si https://HOSTNAME/mcp answering 401 from Cloudflare Access.
7. Tell me to do step 5 (Rally Settings) and step 6 (connect Claude) myself, then confirm in Rally's Audit Log that mcp.connection_authorized appears for my connection.
```

## Alerts

Every new MCP connection authorized and every denied MCP connection or request
sends a Pushover alert to each person on the alert list (Settings → Claude
(MCP)), at their own Pushover key and device. A connection is one person on
one Claude client, so a token refresh is not a new connection. Each alert says
who, which client, from which IP and when, and links to the matching events in
the Audit Log. Sending an alert, and any delivery failure, is logged.

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
