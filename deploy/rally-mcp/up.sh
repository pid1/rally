#!/bin/bash
# Start rally-mcp and its tunnel with plain `docker`, for hosts without the
# Compose plugin (stock Unraid). Same containers, networks and settings as
# docker-compose.yml next to it; run it from this directory, next to .env.
#
#   ./up.sh          create the networks if needed, (re)create both containers
#   ./up.sh down     remove both containers (the networks stay for Rally)
set -euo pipefail
cd "$(dirname "$0")"

[ -f .env ] || { echo "No .env here: cp .env.example .env and fill it in" >&2; exit 1; }
set -a; . ./.env; set +a
: "${TUNNEL_TOKEN:?set TUNNEL_TOKEN in .env}"
: "${CF_ACCESS_AUD:?set CF_ACCESS_AUD in .env}"
: "${RALLY_MCP_SERVICE_TOKEN:?set RALLY_MCP_SERVICE_TOKEN in .env}"
: "${CF_ACCESS_TEAM_DOMAIN:?set CF_ACCESS_TEAM_DOMAIN in .env}"
: "${RALLY_MCP_PUBLIC_HOST:?set RALLY_MCP_PUBLIC_HOST in .env}"

RALLY_MCP_IMAGE="${RALLY_MCP_IMAGE:-ghcr.io/pid1/rally-mcp:latest}"
CLOUDFLARED_IMAGE="cloudflare/cloudflared:2026.10.1@sha256:a15a3b50b8dabd57ac2090e9484c0247dba9dfa1f0df4d298d4317f9d69eb2d5"
HARDENING=(--read-only --cap-drop ALL --security-opt no-new-privileges:true --restart unless-stopped)

docker rm -f rally-mcp-cloudflared rally-mcp >/dev/null 2>&1 || true
[ "${1:-}" = "down" ] && { echo "rally-mcp and its tunnel are down"; exit 0; }

docker network inspect rally_mcp_edge >/dev/null 2>&1 || docker network create rally_mcp_edge >/dev/null
docker network inspect rally_mcp_backend >/dev/null 2>&1 || docker network create --internal rally_mcp_backend >/dev/null

docker create --name rally-mcp "${HARDENING[@]}" \
    --network rally_mcp_edge \
    -e RALLY_URL=http://rally:8000 \
    -e CF_ACCESS_TEAM_DOMAIN -e RALLY_MCP_PUBLIC_HOST \
    -e CF_ACCESS_AUD -e RALLY_MCP_SERVICE_TOKEN \
    "$RALLY_MCP_IMAGE" >/dev/null
docker network connect rally_mcp_backend rally-mcp
docker start rally-mcp >/dev/null

docker run -d --name rally-mcp-cloudflared "${HARDENING[@]}" \
    --network rally_mcp_edge \
    -e TUNNEL_TOKEN \
    "$CLOUDFLARED_IMAGE" tunnel --no-autoupdate run >/dev/null

echo "rally-mcp ($RALLY_MCP_IMAGE) and its tunnel are up"
docker ps --filter name=rally-mcp --format '  {{.Names}}\t{{.Status}}'
