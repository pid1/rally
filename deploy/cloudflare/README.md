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
| `deploy/cloudflare/config.toml` | Account id, zone id, hostname, team domain, household emails, GitHub client id | No |
| `~/.config/cloudflare/rally-mcp.token` | Scoped Cloudflare API token (override with `CF_API_TOKEN_FILE`) | No |
| `~/.config/cloudflare/rally-mcp-github.secret` | GitHub OAuth App client secret (override with `GITHUB_CLIENT_SECRET_FILE`) | No |
| `~/.config/cloudflare/rally-mcp-tunnel.token` | Written by the script: the tunnel token for the server's `.env` | No |

None of these values is printed. An empty secret file counts as missing, so a
paste that never landed can't push a blank secret to Cloudflare.

## What is configured

| Piece | Setting |
|---|---|
| **Tunnel** | `rally-mcp`, remotely managed. Ingress: `<hostname>` → `http://rally-mcp:8080`; everything else → `404` |
| **DNS** | `<hostname>` CNAME → `<tunnel id>.cfargotunnel.com`, proxied |
| **Identity provider** | GitHub (your OAuth App), the only login method on the app. No one-time PIN |
| **Access policy** | `rally-mcp household`: **Allow**, include **Emails** = `household_emails`, require login method = GitHub. No other include rules, no everyone or domain rules. Session 24 h |
| **Access application** | `rally-mcp`, self-hosted on `<hostname>`. Allowed IdPs: GitHub only, with auto-redirect to it. Session 24 h. Not shown in the App Launcher |
| **Managed OAuth** | On. Dynamic Client Registration on; redirect URIs `https://claude.ai/api/mcp/auth_callback` and `https://claude.com/api/mcp/auth_callback`, plus any localhost/loopback URI (Claude Code). Access token 15 min, grant session 24 h |
| **Bots** | Bot Fight Mode, AI bot blocking and crawler protection all off (zone-wide) |
| **IP Access Rules** | Allow Anthropic's `160.79.104.0/21`, as its eight /24 blocks (zone rules accept only /16 and /24) |
| **WAF custom rules** | (1) Skip Browser Integrity Check, Security Level and UA blocking on the hostname. (2) **Path lockdown**: block everything on the hostname except `/mcp`, `/.well-known/oauth-protected-resource*`, `/.well-known/oauth-authorization-server*`, `/.well-known/cloudflare-access-protected-resource*`, `/.well-known/openid-configuration*` and `/cdn-cgi/access/*`. (3) **IP allowlist**: `/mcp` only from `160.79.104.0/21` and any `--dev-ip`s |
| **Rate limiting** | 60 requests per 10 s, per IP and colo, on `/mcp` (the Free plan's one rule) |

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
custom rules. For Anthropic's range the path lockdown is therefore enforced by
rally-mcp, which answers `404` to anything but `/mcp`, rather than at the edge.
Access still guards `/mcp` for everyone.

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

`--dev-ip` adds an address to the `/mcp` allowlist so Claude Code on that
machine can connect directly. Re-run without it to remove it. Without one,
Claude Code is blocked at the edge everywhere, while claude.ai, Claude
Desktop and the mobile apps connect from Anthropic's range.

## Revoking access

In Zero Trust: remove the email from the `rally-mcp household` policy
(`household_emails` in `config.toml`, then re-run), or revoke the person's
sessions under Access → Users. Switching MCP off in Rally's Settings refuses
everything regardless of Cloudflare.
