#!/bin/bash
# Keep rally-mcp on the newest image, for hosts where it runs from up.sh.
# Unraid's CA Auto Update keeps Rally current (an Unraid template), but rally-mcp
# isn't a template, so nothing else pulls it. Run it from cron every minute, the
# same cadence as Rally's, so the two move together after a push to main:
#
#   * * * * * /mnt/user/appdata/rally-mcp/auto-update.sh >/dev/null 2>&1
#
# On Unraid, put that line in /boot/config/plugins/dynamix/rally-mcp-update.cron
# and run `update_cron`, so it survives a reboot.
#
# Does nothing while the image is current or is a local build (no registry in
# its name), so a pinned branch build stays put.
set -euo pipefail
cd "$(dirname "$0")"
exec 9>/var/run/rally-mcp-update.lock
flock -n 9 || exit 0

IMAGE="$(sed -n 's/^RALLY_MCP_IMAGE=//p' .env | tail -1)"
IMAGE="${IMAGE:-ghcr.io/pid1/rally-mcp:latest}"
case "$IMAGE" in */*) ;; *) exit 0 ;; esac

docker pull -q "$IMAGE" >/dev/null 2>&1 || exit 0
want="$(docker image inspect --format '{{.Id}}' "$IMAGE")"
have="$(docker inspect --format '{{.Image}}' rally-mcp 2>/dev/null || true)"
[ "$want" = "$have" ] && exit 0

NOTIFY=/usr/local/emhttp/webGui/scripts/notify
logger -t rally-mcp-update "updating rally-mcp to $IMAGE ($want)"
if ./up.sh >/dev/null; then
    [ -x "$NOTIFY" ] && "$NOTIFY" -e "rally-mcp" -s "rally-mcp updated" -d "Now on ${want:7:12}" -i normal || true
else
    [ -x "$NOTIFY" ] && "$NOTIFY" -e "rally-mcp" -s "rally-mcp update FAILED" -d "up.sh failed for $IMAGE" -i alert || true
    exit 1
fi
