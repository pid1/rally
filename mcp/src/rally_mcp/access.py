"""Cloudflare Access in front, checked again here.

Access (with Managed OAuth) is the authorization server: it runs the OAuth
flow with Claude, and only requests carrying a token it issued reach the
tunnel. rally-mcp does not take that on faith. Every MCP request must carry
the ``Cf-Access-Jwt-Assertion`` header Access adds, signed by the team's keys,
issued by the team domain and for this application's AUD — so a mistake in
the tunnel or Access config cannot expose Rally.

The server is stateless with respect to auth: nothing is stored, Claude
refreshes its own tokens, and revocation happens in Cloudflare Zero Trust.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

import jwt
from jwt import PyJWKClient

JWT_HEADER = "cf-access-jwt-assertion"


class AccessDenied(Exception):
    """The request did not carry a valid Access JWT for this application."""


@dataclass(frozen=True)
class Identity:
    email: str
    subject: str
    connection_id: str
    client: str
    claims: dict


def client_name(user_agent: str | None) -> str:
    """Which Claude surface is calling, from its user agent."""
    ua = (user_agent or "").lower()
    if "claude-user" in ua:
        return "claude.ai"
    if "claude-code" in ua or "claude code" in ua:
        return "Claude Code"
    if "claude" in ua and ("electron" in ua or "desktop" in ua):
        return "Claude Desktop"
    return (user_agent or "unknown")[:80]


def connection_id(claims: dict, client: str) -> str:
    """A stable id for one person's authorization from one client.

    Access does not document a grant or client id claim for Managed OAuth, so
    any it does send is preferred; otherwise one person on one Claude surface
    is one connection. Token refreshes (every 15 minutes) must not look like
    new connections, or every refresh would be an alert.
    """
    grant = claims.get("client_id") or claims.get("azp") or claims.get("oauth_client_id") or ""
    raw = f"{claims.get('sub', '')}|{grant}|{client}"
    return "conn_" + hashlib.sha256(raw.encode()).hexdigest()[:24]


class AccessVerifier:
    def __init__(self, *, certs_url: str, issuer: str, audience: str, jwks_client=None) -> None:
        self.issuer = issuer
        self.audience = audience
        self._jwks = jwks_client or PyJWKClient(certs_url, cache_keys=True, lifespan=3600)

    def verify(self, token: str | None, *, user_agent: str | None = None) -> Identity:
        if not token:
            raise AccessDenied("Missing Cf-Access-Jwt-Assertion header")
        try:
            key = self._jwks.get_signing_key_from_jwt(token)
            claims = jwt.decode(
                token,
                key.key,
                algorithms=["RS256"],
                audience=self.audience,
                issuer=self.issuer,
                options={"require": ["exp", "iat", "aud", "iss"]},
            )
        except jwt.PyJWTError as exc:
            raise AccessDenied(f"Access JWT rejected: {exc}") from exc
        email = (claims.get("email") or "").strip().lower()
        if not email:
            # A service token or a misconfigured app: no person to act for.
            raise AccessDenied("Access JWT carries no email identity")
        client = client_name(user_agent)
        return Identity(
            email=email,
            subject=str(claims.get("sub", "")),
            connection_id=connection_id(claims, client),
            client=client,
            claims=claims,
        )
