# Cloudflare configuration for rally-mcp

Everything Cloudflare-side is applied by [`configure.py`](configure.py). It is
idempotent, so re-run it after changing anything, and it uses only Python's
standard library. Step-by-step setup is in
[docs/mcp.md](../../docs/mcp.md#setting-it-up); this page is the
reference for what the script configures and why.

```bash
cp deploy/cloudflare/config.example.toml deploy/cloudflare/config.toml   # gitignored
python3 deploy/cloudflare/configure.py [--github-client-id ID] [--dev-ip IP ...]
```

| File | Contents | In git? |
|---|---|---|
| `deploy/cloudflare/config.toml` | Account id, zone id, hostname, team domain, household emails, GitHub client id, token connection ids, extra MCP IPs | No |
| `~/.config/cloudflare/rally-mcp.token` | Scoped Cloudflare API token (override with `CF_API_TOKEN_FILE`) | No |
| `~/.config/cloudflare/rally-mcp-github.secret` | GitHub OAuth App client secret (override with `GITHUB_CLIENT_SECRET_FILE`) | No |
| `~/.config/cloudflare/rally-mcp-tunnel.token` | Written by the script: the tunnel token for the server's `.env` | No |

None of these values is printed. An empty secret file counts as missing, so a
paste that never landed can't push a blank secret to Cloudflare.

## What is configured

| Piece | Setting |
|---|---|
| **Tunnel** | `rally-mcp`, remotely managed. Ingress: `<hostname>` → `http://rally-mcp:8080`; everything else → `404`. rally-mcp believes `CF-Connecting-IP` only from the cloudflared container (`RALLY_MCP_TRUSTED_PROXIES`, default its container name `rally-mcp-cloudflared`) |
| **DNS** | `<hostname>` CNAME → `<tunnel id>.cfargotunnel.com`, proxied |
| **Identity provider** | GitHub (your OAuth App), the only login method on the app. No one-time PIN |
| **Access policy** | `rally-mcp household`: **Allow**, include **Emails** = `household_emails`, require login method = GitHub. No other include rules, no everyone or domain rules. Session 24 h |
| **Access application** | `rally-mcp`, self-hosted on `<hostname>` (every path, so `/mcp` and every OAuth connection's `/mcp/c/<id>`). Allowed IdPs: GitHub only, with auto-redirect to it. Session 24 h. Not shown in the App Launcher |
| **Token connection bypass** | One application per id in `token_connection_ids`, `rally-mcp token <id>`, on exactly `<hostname>/mcp/c/<id>`, with the `rally-mcp token connections (bypass)` policy (**Bypass**, include everyone). Applications for ids no longer listed are deleted. See [Token connections](#token-connections) |
| **Managed OAuth** | On. Dynamic Client Registration on; redirect URIs `https://claude.ai/api/mcp/auth_callback` and `https://claude.com/api/mcp/auth_callback`, plus any localhost/loopback URI (Claude Code). Access token 15 min, grant session 24 h |
| **Bots** | Bot Fight Mode, AI bot blocking and crawler protection all off (zone-wide) |
| **IP Access Rules** | Allow Anthropic's outbound `160.79.104.0/21` and `153.61.208.0/20`, as their 24 /24 blocks (zone rules accept only /16 and /24). Source: [platform.claude.com/docs/en/api/ip-addresses](https://platform.claude.com/docs/en/api/ip-addresses), checked 2026-11-05 |
| **WAF custom rules** | (1) Skip Browser Integrity Check, Security Level and UA blocking on the hostname. (2) **Path lockdown**: block everything on the hostname except `/mcp`, `/mcp/c/*`, `/.well-known/oauth-protected-resource*`, `/.well-known/oauth-authorization-server*`, `/.well-known/cloudflare-access-protected-resource*`, `/.well-known/openid-configuration*` and `/cdn-cgi/access/*`. (3) **IP allowlist**: `/mcp` and `/mcp/c/*` only from Anthropic's ranges, `extra_mcp_ips` and any `--dev-ip`s |
| **Rate limiting** | 60 requests per 10 s, per IP and colo, on `/mcp` and `/mcp/c/*` (the Free plan's one rule) |

The script prints the Access application's **AUD tag**. That value goes in the
server's `.env` as `CF_ACCESS_AUD`.

### Where the OAuth endpoints live

Discovery is served on the protected hostname. The authorization server
itself is the team domain, which Cloudflare runs outside your zone:

| Endpoint | URL |
|---|---|
| Protected resource metadata | `https://<hostname>/.well-known/oauth-protected-resource/mcp` (and `/.well-known/cloudflare-access-protected-resource/mcp`, which the `401` points to) |
| Authorization server metadata | `https://<hostname>/.well-known/oauth-authorization-server` |
| Authorize | `https://<team domain>/cdn-cgi/access/oauth/authorization` |
| Token | `https://<team domain>/cdn-cgi/access/oauth/token` |
| Register (DCR) | `https://<team domain>/cdn-cgi/access/oauth/registration` |
| Revoke | `https://<team domain>/cdn-cgi/access/oauth/revoke` |

So the browser-facing login and authorize pages are not under the `/mcp` IP
allowlist. They must not be, or authorizing from your own browser would fail.

### Known interaction: the IP allow rule skips the WAF

An IP Access Rule set to *Allow* also exempts those addresses from the WAF
custom rules. For Anthropic's ranges the path lockdown is therefore enforced by
rally-mcp, which answers `404` to anything but `/mcp` and `/mcp/c/<id>`,
rather than at the edge. Access still guards every OAuth path for everyone.

## Token connections

A token connection (Rally → Connections, *Signs in with: Token*) is for an
agent that cannot complete a browser login, like the phone assistant. Its
path, `/mcp/c/<id>`, has to skip Access, so:

1. Create the connection in Rally first, and copy its token from the dialog
   (it is shown once) into the agent's `Authorization: Bearer` header.
2. Add its id to `token_connection_ids` in `config.toml`, and the agent
   provider's outbound ranges to `extra_mcp_ips` (and to the connection's own
   allowlist in Rally). Re-run `configure.py`.
3. After revoking the connection in Rally, remove its id and re-run, which
   deletes its bypass application.

What guards a bypassed path, since Access no longer does: the edge's IP
allowlist; rally-mcp, which requires the bearer token there (and will not
treat an Access JWT as one); and Rally, which checks the token's HMAC, its
expiry, the connection's status and its IP allowlist on every request. An
OAuth connection's path is never bypassed, and a token on it is ignored.

Cloudflare Access **service tokens** (`CF-Access-Client-Id` /
`CF-Access-Client-Secret` headers, a *Service Auth* policy) would add a second
credential at the edge, if the agent can send two custom headers. They are not
configured by the script; if you use one, keep the bearer token as well, since
rally-mcp and Rally check that and not the service token.

## The API token

Create a **custom** user API token with an expiry. Permissions:

- **Account** (all accounts, or just yours): Cloudflare Tunnel: Edit; Access:
  Apps and Policies: Edit; Access: Organizations, Identity Providers, and
  Groups: Edit; Account Firewall Access Rules: Edit.
- **Zone** (specific zone: yours): DNS: Edit; Zone WAF: Edit; Firewall
  Services: Edit; Bot Management: Edit; Zone Settings: Edit.

Delete the token once the setup is settled. The script only needs it when you
change something.

## Development IPs

`--dev-ip` adds an address to the edge allowlist for `/mcp` and `/mcp/c/*` so
Claude Code on that machine can connect directly (add it to the connection's
own allowlist in Rally too). Re-run without it to remove it. Without one,
Claude Code is blocked at the edge everywhere, while claude.ai, Claude
Desktop and the mobile apps connect from Anthropic's range.

## Revoking access

In Zero Trust: remove the email from the `rally-mcp household` policy
(`household_emails` in `config.toml`, then re-run), or revoke the person's
sessions under Access → Users. In Rally, disable or revoke the connection on
the Connections page. Switching MCP off in Rally's Settings refuses
everything regardless of Cloudflare.
