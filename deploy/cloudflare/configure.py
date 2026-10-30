#!/usr/bin/env python3
"""Configure Cloudflare for rally-mcp: tunnel, DNS, Access (Managed OAuth), edge rules.

Idempotent: every resource is looked up by name and updated in place, so the
script is safe to re-run after changing a setting. It uses only the standard
library so it runs anywhere with python3.

Your deployment's values (account, zone, hostname, team domain, the household's
emails) live in deploy/cloudflare/config.toml, which is gitignored — copy
config.example.toml and fill it in. Nothing deployment-specific is in this file.

    python3 deploy/cloudflare/configure.py [--github-client-id ID] [--dev-ip IP ...]

The GitHub OAuth client secret is read from GITHUB_CLIENT_SECRET_FILE (default
~/.config/cloudflare/rally-mcp-github.secret) and never printed. The tunnel
token is written to ~/.config/cloudflare/rally-mcp-tunnel.token for the NAS.

See deploy/cloudflare/README.md for what each setting is and why.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import sys
import tomllib
import urllib.error
import urllib.request
from pathlib import Path

# Filled from config.toml by load_config(); see config.example.toml.
ACCOUNT_ID = ""
ZONE_ID = ""
HOSTNAME = ""
TEAM_DOMAIN = ""
HOUSEHOLD_EMAILS: list[str] = []
GITHUB_CLIENT_ID = ""
CONFIG_FILE = Path(os.environ.get("RALLY_MCP_CF_CONFIG", Path(__file__).with_name("config.toml")))

TUNNEL_NAME = "rally-mcp"
ORIGIN = "http://rally-mcp:8080"
APP_NAME = "rally-mcp"
POLICY_NAME = "rally-mcp household"
IDP_NAME = "GitHub"
ANTHROPIC_RANGE = "160.79.104.0/21"
MCP_PATH = "/mcp"

# Paths Managed OAuth and Access serve on the protected hostname. Everything
# else on the hostname is blocked at the edge (path lockdown). The authorize,
# token, registration and revoke endpoints live on the team domain
# (TEAM_DOMAIN/cdn-cgi/access/oauth/...), outside this zone, so only the
# discovery documents and Access's own /cdn-cgi/access paths are needed here.
# The 401 from /mcp points clients at
# /.well-known/cloudflare-access-protected-resource/mcp.
OAUTH_PATH_PREFIXES = [
    "/.well-known/oauth-protected-resource",
    "/.well-known/oauth-authorization-server",
    "/.well-known/cloudflare-access-protected-resource",
    "/.well-known/openid-configuration",
    "/cdn-cgi/access/",
]

CONFIG_DIR = Path.home() / ".config" / "cloudflare"
API = "https://api.cloudflare.com/client/v4"
RULE_REF_PREFIX = "rally_mcp_"


def load_config() -> None:
    """Read this deployment's values from config.toml into the module globals."""
    global ACCOUNT_ID, ZONE_ID, HOSTNAME, TEAM_DOMAIN, HOUSEHOLD_EMAILS, GITHUB_CLIENT_ID
    if not CONFIG_FILE.exists():
        sys.exit(f"{CONFIG_FILE} not found: copy config.example.toml next to it and fill it in")
    cfg = tomllib.loads(CONFIG_FILE.read_text())
    try:
        ACCOUNT_ID = cfg["account_id"]
        ZONE_ID = cfg["zone_id"]
        HOSTNAME = cfg["hostname"]
        TEAM_DOMAIN = cfg["team_domain"]
        HOUSEHOLD_EMAILS = [e.strip().lower() for e in cfg["household_emails"]]
    except KeyError as exc:
        sys.exit(f"{CONFIG_FILE} is missing {exc}")
    GITHUB_CLIENT_ID = cfg.get("github_client_id", "")
    if not HOUSEHOLD_EMAILS:
        sys.exit("household_emails must name at least one person")


def _token() -> str:
    path = Path(os.environ.get("CF_API_TOKEN_FILE", CONFIG_DIR / "rally-mcp.token")).expanduser()
    return path.read_text().strip()


def cf(method: str, path: str, body: dict | list | None = None) -> dict:
    req = urllib.request.Request(
        API + path,
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={
            "Authorization": f"Bearer {_token()}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req) as resp:
            data = json.load(resp)
    except urllib.error.HTTPError as exc:
        data = json.load(exc)
        if exc.code == 404 and method == "GET":
            return {"success": False, "result": None, "errors": data.get("errors")}
        raise SystemExit(f"{method} {path} failed: {exc.code} {data.get('errors')}") from exc
    if not data.get("success", False):
        raise SystemExit(f"{method} {path} failed: {data.get('errors')}")
    return data


def log(msg: str) -> None:
    print(f"• {msg}")


# --- Tunnel + DNS -----------------------------------------------------------


def ensure_tunnel() -> str:
    acct = f"/accounts/{ACCOUNT_ID}/cfd_tunnel"
    found = cf("GET", f"{acct}?name={TUNNEL_NAME}&is_deleted=false")["result"]
    if found:
        tunnel_id = found[0]["id"]
        log(f"tunnel {TUNNEL_NAME} exists ({tunnel_id})")
    else:
        tunnel_id = cf("POST", acct, {"name": TUNNEL_NAME, "config_src": "cloudflare"})["result"][
            "id"
        ]
        log(f"created tunnel {TUNNEL_NAME} ({tunnel_id})")

    # Only rally-mcp is routed; everything else on the tunnel is a 404.
    cf(
        "PUT",
        f"{acct}/{tunnel_id}/configurations",
        {
            "config": {
                "ingress": [
                    {"hostname": HOSTNAME, "service": ORIGIN, "originRequest": {}},
                    {"service": "http_status:404"},
                ]
            }
        },
    )
    log(f"tunnel ingress: {HOSTNAME} -> {ORIGIN}, everything else -> 404")

    token = cf("GET", f"{acct}/{tunnel_id}/token")["result"]
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    out = CONFIG_DIR / "rally-mcp-tunnel.token"
    out.touch(mode=0o600, exist_ok=True)
    out.write_text(token)
    log(f"tunnel token written to {out} (put it in the NAS .env as TUNNEL_TOKEN)")
    return tunnel_id


def ensure_dns(tunnel_id: str) -> None:
    zone = f"/zones/{ZONE_ID}/dns_records"
    record = {
        "type": "CNAME",
        "name": HOSTNAME,
        "content": f"{tunnel_id}.cfargotunnel.com",
        "proxied": True,
        "comment": "rally-mcp Cloudflare Tunnel (managed by deploy/cloudflare/configure.py)",
    }
    found = cf("GET", f"{zone}?name={HOSTNAME}")["result"]
    if found:
        cf("PUT", f"{zone}/{found[0]['id']}", record)
        log(f"DNS {HOSTNAME} updated")
    else:
        cf("POST", zone, record)
        log(f"DNS {HOSTNAME} created")


# --- Access -----------------------------------------------------------------


def ensure_github_idp(client_id: str | None) -> str | None:
    acct = f"/accounts/{ACCOUNT_ID}/access/identity_providers"
    found = [i for i in cf("GET", acct)["result"] if i["type"] == "github"]
    secret_file = Path(
        os.environ.get("GITHUB_CLIENT_SECRET_FILE", CONFIG_DIR / "rally-mcp-github.secret")
    ).expanduser()
    # An empty file (a paste that never landed) counts as missing, never as a blank secret.
    if not client_id or not secret_file.exists() or not secret_file.read_text().strip():
        if found:
            log(f"GitHub IdP exists ({found[0]['id']}); not updating credentials")
            return found[0]["id"]
        log("GitHub IdP missing: pass --github-client-id and save the secret file")
        return None
    body = {
        "name": IDP_NAME,
        "type": "github",
        "config": {
            "client_id": client_id,
            "client_secret": secret_file.read_text().strip(),
        },
    }
    if found:
        idp_id = found[0]["id"]
        cf("PUT", f"{acct}/{idp_id}", body)
        log(f"GitHub IdP updated ({idp_id})")
    else:
        idp_id = cf("POST", acct, body)["result"]["id"]
        log(f"GitHub IdP created ({idp_id})")
    return idp_id


def ensure_policy(idp_id: str) -> str:
    acct = f"/accounts/{ACCOUNT_ID}/access/policies"
    body = {
        "name": POLICY_NAME,
        "decision": "allow",
        # Exactly the two household emails. With GitHub this is the verified
        # primary email GitHub returns, not the username.
        "include": [{"email": {"email": e}} for e in HOUSEHOLD_EMAILS],
        # Belt and braces with allowed_idps: the login must come from GitHub.
        "require": [{"login_method": {"id": idp_id}}],
        "exclude": [],
        "session_duration": "24h",
    }
    found = [p for p in cf("GET", acct)["result"] if p["name"] == POLICY_NAME]
    if found:
        policy_id = found[0]["id"]
        cf("PUT", f"{acct}/{policy_id}", body)
        log(f"Access policy updated ({policy_id})")
    else:
        policy_id = cf("POST", acct, body)["result"]["id"]
        log(f"Access policy created ({policy_id})")
    return policy_id


def ensure_app(idp_id: str, policy_id: str) -> dict:
    acct = f"/accounts/{ACCOUNT_ID}/access/apps"
    found = [a for a in cf("GET", acct)["result"] if a.get("name") == APP_NAME]
    body = dict(found[0]) if found else {}
    for k in ("id", "uid", "aud", "created_at", "updated_at"):
        body.pop(k, None)
    body.update(
        {
            "name": APP_NAME,
            "type": "self_hosted",
            "domain": HOSTNAME,
            "destinations": [{"type": "public", "uri": HOSTNAME}],
            "allowed_idps": [idp_id],
            "auto_redirect_to_identity": True,
            "session_duration": "24h",
            "app_launcher_visible": False,
            "policies": [{"id": policy_id, "precedence": 1}],
            "http_only_cookie_attribute": True,
            "same_site_cookie_attribute": "lax",
            "enable_binding_cookie": False,
            "options_preflight_bypass": False,
            "oauth_configuration": {
                "enabled": True,
                "dynamic_client_registration": {
                    "enabled": True,
                    # Claude Code completes OAuth on a localhost callback.
                    "allow_any_on_localhost": True,
                    "allow_any_on_loopback": True,
                    # claude.ai / Desktop / mobile connectors.
                    "allowed_uris": [
                        "https://claude.ai/api/mcp/auth_callback",
                        "https://claude.com/api/mcp/auth_callback",
                    ],
                },
                "grant": {
                    "access_token_lifetime": "15m",
                    "session_duration": "24h",
                },
            },
        }
    )
    if found:
        app = cf("PUT", f"{acct}/{found[0]['id']}", body)["result"]
        log(f"Access app updated ({app['id']})")
    else:
        app = cf("POST", acct, body)["result"]
        log(f"Access app created ({app['id']})")
    log(f"Access app AUD tag: {app['aud']}  (set CF_ACCESS_AUD on the NAS)")
    return app


# --- Edge: IP access rule, WAF custom rules, rate limit ----------------------


def ensure_ip_allow() -> None:
    # IP Access Rules only accept /16 and /24 IPv4 ranges, so the /21 goes in
    # as its eight /24 blocks.
    rules = f"/zones/{ZONE_ID}/firewall/access_rules/rules"
    for net in ipaddress.ip_network(ANTHROPIC_RANGE).subnets(new_prefix=24):
        found = cf("GET", f"{rules}?configuration.value={net}")["result"]
        body = {
            "mode": "whitelist",
            "configuration": {"target": "ip_range", "value": str(net)},
            "notes": f"Anthropic outbound {ANTHROPIC_RANGE} (Claude connectors) - rally-mcp",
        }
        if found:
            cf("PATCH", f"{rules}/{found[0]['id']}", body)
        else:
            cf("POST", rules, body)
    log(f"IP Access Rules allow {ANTHROPIC_RANGE} (as 8 x /24)")


def _put_phase_rules(phase: str, ours: list[dict]) -> None:
    """Replace only our rules (ref prefix) in a zone phase entrypoint."""
    path = f"/zones/{ZONE_ID}/rulesets/phases/{phase}/entrypoint"
    current = cf("GET", path)
    existing = (current.get("result") or {}).get("rules", []) if current else []
    keep = [
        {k: v for k, v in r.items() if k not in ("id", "version", "last_updated")}
        for r in existing
        if not r.get("ref", "").startswith(RULE_REF_PREFIX)
    ]
    cf("PUT", path, {"rules": ours + keep})


def ensure_waf(dev_ips: list[str]) -> None:
    host = f'http.host eq "{HOSTNAME}"'
    allowed_paths = " or ".join(
        [f'http.request.uri.path eq "{MCP_PATH}"']
        + [f'starts_with(http.request.uri.path, "{p}")' for p in OAUTH_PATH_PREFIXES]
    )
    mcp_ips = " ".join([ANTHROPIC_RANGE, *dev_ips])
    rules = [
        {
            "ref": f"{RULE_REF_PREFIX}skip_challenges",
            "description": "rally-mcp: no browser integrity / security level challenges",
            "expression": f"({host})",
            "action": "skip",
            "action_parameters": {"products": ["bic", "securityLevel", "uaBlock"]},
            "logging": {"enabled": True},
        },
        {
            "ref": f"{RULE_REF_PREFIX}path_lockdown",
            "description": "rally-mcp: block everything but /mcp and OAuth endpoints",
            "expression": f"({host} and not ({allowed_paths}))",
            "action": "block",
        },
        {
            "ref": f"{RULE_REF_PREFIX}mcp_ip_allowlist",
            # Only the MCP endpoint: the OAuth login/authorize pages must stay
            # reachable from your own browser.
            "description": "rally-mcp: /mcp only from Anthropic (+ temporary dev IPs)",
            "expression": (
                f'({host} and http.request.uri.path eq "{MCP_PATH}" '
                f"and not ip.src in {{{mcp_ips}}})"
            ),
            "action": "block",
        },
    ]
    _put_phase_rules("http_request_firewall_custom", rules)
    log(f"WAF custom rules set (MCP IP allowlist: {mcp_ips})")

    ratelimit = [
        {
            "ref": f"{RULE_REF_PREFIX}ratelimit",
            # The token endpoint is on the team domain, which Cloudflare runs
            # and rate limits itself; only /mcp is in this zone.
            "description": "rally-mcp: rate limit the MCP endpoint",
            "expression": f'({host} and http.request.uri.path eq "{MCP_PATH}")',
            "action": "block",
            "ratelimit": {
                "characteristics": ["cf.colo.id", "ip.src"],
                "period": 10,
                "requests_per_period": 60,
                "mitigation_timeout": 10,
            },
        }
    ]
    _put_phase_rules("http_ratelimit", ratelimit)
    log("rate limit set: 60 requests / 10s per IP on /mcp")


def ensure_bots_off() -> None:
    cf(
        "PUT",
        f"/zones/{ZONE_ID}/bot_management",
        {
            "fight_mode": False,
            "ai_bots_protection": "disabled",
            "crawler_protection": "disabled",
            "enable_js": False,
        },
    )
    log("Bot Fight Mode, AI bot blocking and crawler protection: off")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--github-client-id")
    parser.add_argument(
        "--dev-ip",
        action="append",
        default=[],
        help="temporary IP allowed to reach /mcp directly (repeatable); "
        "re-run without it to remove",
    )
    parser.add_argument("--skip-access", action="store_true")
    args = parser.parse_args()
    load_config()

    tunnel_id = ensure_tunnel()
    ensure_dns(tunnel_id)
    ensure_bots_off()
    ensure_ip_allow()
    ensure_waf(args.dev_ip)
    if args.skip_access:
        return
    idp_id = ensure_github_idp(args.github_client_id or GITHUB_CLIENT_ID)
    if not idp_id:
        sys.exit("Stopping before Access: no GitHub identity provider yet.")
    policy_id = ensure_policy(idp_id)
    ensure_app(idp_id, policy_id)


if __name__ == "__main__":
    main()
