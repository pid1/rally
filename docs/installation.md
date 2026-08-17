# Installation

Rally is a single container with a single volume. It runs on a NAS, a home server, or a spare machine on your LAN.

## Before you start

You need:

- **Docker.**
- **A timezone**, in IANA form, such as `America/Chicago` or `Europe/London`.
- **An AI provider** for the daily briefing: an Anthropic API key, or any OpenAI-compatible endpoint. A local model works; GLM 4.7 Flash is a good fit.
- **A National Weather Service forecast URL** for where you live. It is free and needs no key. The Weather section of the Settings page explains how to find yours.

Optional, and all addable later:

- **Calendar access.** ICS feed URLs, or Google/Apple CalDAV with app-specific passwords. Rally's own calendars need none of this.
- **A Pushover application token**, if you want event reminders and change notices. Each person also needs their own Pushover user key.

## Running it

Every push to `main` publishes an image to the GitHub Container Registry, so there is nothing to build:

```bash
docker run -d \
  -p 8000:8000 \
  -v $(pwd)/data:/data \
  -v $(pwd)/output:/output \
  --name rally \
  --restart unless-stopped \
  ghcr.io/pid1/rally:latest
```

To build it yourself instead:

```bash
docker build -t rally .
docker run -d -p 8000:8000 -v $(pwd)/data:/data --name rally --restart unless-stopped rally
```

Then open `http://<host>:8000/settings` and work down the page. Rally verifies each connection as you save it, so a wrong key or URL fails in front of you rather than at 4 AM.

The container runs database migrations on startup, regenerates the daily summary at 4 AM in your configured timezone, and checks once a minute for reminders that are due.

## Upgrading

Pull the new image and restart the container:

```bash
docker pull ghcr.io/pid1/rally:latest
docker stop rally && docker rm rally
docker run -d -p 8000:8000 -v $(pwd)/data:/data --name rally --restart unless-stopped ghcr.io/pid1/rally:latest
```

Migrations are idempotent and run automatically on startup, so an upgrade needs no manual database step. The container refuses to start if a migration fails. That is deliberate, because a half-migrated database is worse than a stopped one.

## What lives where

| Path | Contents |
|---|---|
| `/data/rally.db` | Everything: events, tasks, lists, settings, snapshots |
| `/output` | Generated summary artifacts |

The database holds your API keys and any CalDAV app-specific passwords **in plaintext**. Treat the volume accordingly, and back it up. [backup.md](backup.md) covers a scheduled, client-side-encrypted offsite backup, worked through end to end for Unraid and Cloudflare R2.

## Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `RALLY_ENV` | `development` | Set to `production` in Docker |
| `RALLY_DB_PATH` | Auto-detected from `RALLY_ENV` | Override the database location |

Everything else is configured in the Settings UI and stored in the database. See [configuration.md](configuration.md).

## Remote access

Rally has no built-in authentication. Keep it on your LAN, or reach it over a private network such as Tailscale. If you expose it beyond that, put it behind a reverse proxy that terminates HTTPS and handles authentication.
