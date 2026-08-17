# Offsite backup

**Status:** recommended setup, not automated by Rally itself. Everything here is
host-side configuration; no application code is involved.

Rally keeps a family's calendar, tasks, meals, and preparedness inventory in a
single SQLite file. This document is how to get that file somewhere safe, on a
schedule, encrypted, without adding anything to the deployment.

The worked example targets **Unraid**, since that is where Rally is deployed
today, and **Cloudflare R2**, which is free at the sizes Rally produces. The
shape of the job — snapshot, encrypt, upload, prune — transfers to any host.

## Why this is worth doing

Two properties of the database decide the whole design.

**It holds live credentials.** Rally stores secrets in the database, in
plaintext:

| Where | What |
|---|---|
| `settings.llm_anthropic_api_key`, `settings.llm_local_api_key` | LLM API keys |
| `settings.pushover_app_token` | Pushover application token |
| `family_members.pushover_user_key` | Per-member Pushover user keys |
| `calendars.username`, `calendars.password` | Google and Apple **app-specific passwords** |

Those last ones are standing grants against real accounts. So the backup has to
be encrypted *before* it leaves the host. A provider that offers "encryption at
rest" is holding a key to your family's calendar accounts, which is not the same
thing at all.

**It is small.** A seeded database is on the order of 100 KB, and real family use
keeps it in single-digit megabytes for years. This rules out most of what a
search for "SQLite backup" will recommend — those tools solve the opposite
problem. At this size the storage is free and the transfer is instant, so the
design can optimize entirely for *simplicity and recoverability*.

## What gets backed up

Everything in the `/data` volume:

- `rally.db` — the database, as a consistent snapshot (see below)
- `config.toml` — API keys, coordinates, timezone
- `context.txt`, `agent_voice.txt` — family context and AI voice profile

Only the database is guaranteed to be there. An instance configured entirely
through the Settings UI keeps all of that in the `settings` table and has no
`config.toml` at all, which is why the script backs up the whole volume by
exclusion rather than naming files — whatever exists gets captured.

The `/output` volume is **not** backed up. It holds generated dashboard HTML,
which is rebuilt from the database on the next generation run.

## Taking a consistent snapshot

Never back up `rally.db` by copying it while Rally is running. A plain `cp` can
capture a file mid-write, and the result is a database that looks fine until the
day you need it.

Use `VACUUM INTO`, which takes its snapshot through SQLite's own locking:

```sql
VACUUM INTO '/data/backup/rally-snap.db'
```

Rally keeps serving throughout — for a database this size the read lock is held
for milliseconds — and the output is a consistent, compacted copy. Two details
worth knowing:

- **The target must not already exist.** `VACUUM INTO` fails with
  `output file already exists` rather than overwriting, so the script deletes
  the previous snapshot first.
- **It runs in the container, not on the host.** Unraid 7.x does ship
  `/usr/bin/sqlite3`, so this is not strictly necessary there — but running the
  snapshot through Rally's own container via `docker exec` uses the same SQLite
  build that wrote the database, and keeps the job working on hosts where the
  binary is absent or older than `VACUUM INTO` (SQLite 3.27, 2019).

Measured on a live 3.1 MB production database: 0.127 s to produce a 2.3 MB
compacted snapshot, with the app still serving.

## Storage: Cloudflare R2

R2 is the recommended target:

- **Free at Rally's size** — the free tier covers far more than this database
  will ever need.
- **No egress fees** — restoring costs nothing, which matters because a backup
  you are reluctant to test is not a backup.
- **Genuinely S3-compatible** — works with restic, rclone, and everything else,
  with no adapter.

Backblaze B2 is an equally good substitute. Avoid CDN-first storage products
whose S3 compatibility is incidental; they are built to serve assets, not to be
written to by backup tooling.

### Creating the bucket

1. In the Cloudflare dashboard, go to **R2** and create a bucket, e.g. `rally-backup`.
2. Under **Manage R2 API Tokens**, create a token with **Object Read & Write**,
   scoped to that one bucket.
3. Note the **Access Key ID**, **Secret Access Key**, and your **Account ID**.

The endpoint is `https://<account-id>.r2.cloudflarestorage.com`, and restic
addresses the bucket as
`s3:https://<account-id>.r2.cloudflarestorage.com/rally-backup`.

## Encryption and retention: restic

[restic](https://restic.net) encrypts client-side by default, keeps versioned
snapshots, prunes on a retention policy, and verifies repository integrity — the
whole job in one tool, and nothing to install on the host, since it runs from
the official container image.

The versioning matters more than it might seem. A backup that simply *mirrors*
the database will faithfully mirror its damage: a bad migration or a corrupted
page gets copied offsite, overwriting the last good copy. Dated snapshots with a
retention policy protect against the failure you are actually likely to hit.

### Credentials

Store them in `/mnt/user/appdata/rally/backup.env`, **not** in the script:

```bash
export RESTIC_REPOSITORY="s3:https://<account-id>.r2.cloudflarestorage.com/rally-backup"
export RESTIC_PASSWORD="<a long random passphrase>"
export AWS_ACCESS_KEY_ID="<r2 access key id>"
export AWS_SECRET_ACCESS_KEY="<r2 secret access key>"
export AWS_DEFAULT_REGION="auto"
```

```bash
chmod 600 /mnt/user/appdata/rally/backup.env
```

This placement is deliberate and Unraid-specific. User Scripts are stored on the
Unraid flash drive, which is FAT32 and cannot represent Unix permissions — a
passphrase pasted into the script body would sit world-readable on a USB stick.
Appdata lives on a cache pool with a real filesystem, where `chmod 600` means
something.

**Record `RESTIC_PASSWORD` in your password manager.** It is not recoverable, and
without it the backups are permanently unreadable. Losing it is the single most
likely way this setup fails you.

### Initialize the repository

Once, before the first run:

```bash
set -a; . /mnt/user/appdata/rally/backup.env; set +a
docker run --rm \
  -e RESTIC_REPOSITORY -e RESTIC_PASSWORD \
  -e AWS_ACCESS_KEY_ID -e AWS_SECRET_ACCESS_KEY -e AWS_DEFAULT_REGION \
  restic/restic init
```

## Scheduling on Unraid

Use the **User Scripts** plugin from Community Applications, not `crontab`.
Unraid runs from RAM and rebuilds itself from the flash drive at boot, so a
`crontab -e` edit disappears silently at the next reboot. User Scripts persist,
and the schedule is managed in the Unraid UI alongside everything else.

Add a script named `Rally Backup`, paste the body below, then set its schedule to
**Scheduled Daily** in the User Scripts page and click **Apply**. The plugin
stores that choice in `/boot/config/plugins/user.scripts/schedule.json` and the
daily run is driven by `/etc/cron.daily/user.script.start.daily.sh`, which the
plugin installs — so nothing needs adding to `crontab` or `/etc/cron.d/root`.

```bash
#!/bin/bash
# Rally offsite backup: snapshot the database, push it to R2, prune old copies.
set -euo pipefail

APPDATA=/mnt/user/appdata/rally
CONTAINER=rally
SNAPSHOT="$APPDATA/backup/rally-snap.db"

# Credentials live in appdata, not here — see docs/backup.md.
# shellcheck source=/dev/null
source "$APPDATA/backup.env"

restic_run() {
    docker run --rm \
        -e RESTIC_REPOSITORY -e RESTIC_PASSWORD \
        -e AWS_ACCESS_KEY_ID -e AWS_SECRET_ACCESS_KEY -e AWS_DEFAULT_REGION \
        "$@"
}

mkdir -p "$APPDATA/backup"
rm -f "$SNAPSHOT"   # VACUUM INTO refuses to overwrite an existing target

# Consistent snapshot via the container's own Python, so the host needs no
# sqlite3 binary and Rally keeps serving while it runs.
docker exec "$CONTAINER" python -c \
    "import sqlite3; c = sqlite3.connect('/data/rally.db'); c.execute(\"VACUUM INTO '/data/backup/rally-snap.db'\"); c.close()"

# Back up the whole volume, minus: the live database (the snapshot stands in for
# it), any ad-hoc manual copies in backups/ (restic's own versioning supersedes
# them), and backup.env — the credentials that protect this repository must not
# live inside it. Anything else new landing in /data is picked up without
# editing this script.
restic_run -v "$APPDATA:/src:ro" restic/restic backup \
    --tag rally --host unraid \
    --exclude /src/rally.db \
    --exclude '/src/rally.db-journal' \
    --exclude /src/backups \
    --exclude /src/backup.env \
    /src

restic_run restic/restic forget --tag rally \
    --keep-daily 7 --keep-weekly 4 --keep-monthly 12 --prune

rm -f "$SNAPSHOT"
```

That retention really does keep a year of history for a few megabytes. Measured
against a live instance: the 2.2 MiB snapshot stored as **253 KiB** on the first
run, and a second run minutes later added **1016 bytes**. Daily snapshots of a
database that changes slowly cost almost nothing.

Two deletions in that script are deliberate, and both are about not leaving
credentials lying around:

- **The local snapshot is removed when the run finishes.** It is an unencrypted
  copy of the credentials table; there is no reason to keep one in appdata
  between runs.
- **`backup.env` is excluded from the backup.** Backing up the whole volume
  otherwise sweeps it in, which puts the restic passphrase and the R2 keys
  *inside the repository they protect*. It is encrypted there, so this is not an
  immediate exposure — but anyone who obtained the passphrase would also get keys
  that can delete the backups, and an R2 token is trivially re-created, so there
  is no recovery value to offset the risk.

## Restoring

With the container stopped:

```bash
set -a; . /mnt/user/appdata/rally/backup.env; set +a

docker stop rally

docker run --rm -v /mnt/user/appdata/rally/restore:/restore \
  -e RESTIC_REPOSITORY -e RESTIC_PASSWORD \
  -e AWS_ACCESS_KEY_ID -e AWS_SECRET_ACCESS_KEY -e AWS_DEFAULT_REGION \
  restic/restic restore latest --target /restore

cp /mnt/user/appdata/rally/restore/src/backup/rally-snap.db \
   /mnt/user/appdata/rally/rally.db

# Only if the instance uses one — a Settings-UI-configured install has no config.toml.
[ -f /mnt/user/appdata/rally/restore/src/config.toml ] && \
  cp /mnt/user/appdata/rally/restore/src/config.toml /mnt/user/appdata/rally/config.toml

docker start rally
```

Migrations are idempotent and run on every start, so a snapshot from an older
schema is upgraded automatically on the next boot.

## Verify it works

Do these once at setup, then revisit occasionally.

**Restore to a scratch directory and read the result.** A backup that has never
been restored is a hypothesis, not a backup:

```bash
docker run --rm -v /mnt/user/appdata/rally/verify:/verify \
  -e RESTIC_REPOSITORY -e RESTIC_PASSWORD \
  -e AWS_ACCESS_KEY_ID -e AWS_SECRET_ACCESS_KEY -e AWS_DEFAULT_REGION \
  restic/restic restore latest --target /verify

docker run --rm -v /mnt/user/appdata/rally/verify:/verify python:3.14-slim \
  python -c "import sqlite3; c = sqlite3.connect('/verify/src/backup/rally-snap.db'); \
    print(c.execute('PRAGMA integrity_check').fetchone()); \
    print(c.execute('SELECT count(*) FROM family_members').fetchone())"
```

Expect `('ok',)` and a plausible member count.

**Check the repository itself** every few months:

```bash
docker run --rm \
  -e RESTIC_REPOSITORY -e RESTIC_PASSWORD \
  -e AWS_ACCESS_KEY_ID -e AWS_SECRET_ACCESS_KEY -e AWS_DEFAULT_REGION \
  restic/restic check
```

**Confirm the schedule is actually firing.** User Scripts keeps per-script logs;
after the first day, check that a snapshot was written and that
`restic snapshots` lists it.

## Troubleshooting

**TLS handshake failures from host tools, but not from containers.** If a
host-installed S3 client (`rclone`, for instance) fails against the R2 endpoint
with `remote error: tls: handshake failure` while `curl` to the same URL
succeeds, suspect broken IPv6 egress rather than TLS. R2 endpoints resolve to
both A and AAAA records; a tool that prefers IPv6 on a host with no working IPv6
route fails in ways that read like a certificate problem. Confirm with:

```bash
curl -4 -sS -o /dev/null -w "v4: %{http_code}\n" https://<account-id>.r2.cloudflarestorage.com
curl -6 -sS -o /dev/null -w "v6: %{http_code}\n" https://<account-id>.r2.cloudflarestorage.com
```

A `400` is the expected healthy answer to an unauthenticated request. This does
not affect the backup job — Docker's default bridge network is IPv4-only, so
restic in a container is unaffected — but it will bite anyone debugging the
endpoint with host tools first.

## Alternatives considered

**Litestream** is the answer most searches give, and it is the wrong fit here.
It requires WAL journal mode, so the application's storage configuration would
change to suit the backup tool. It runs as an additional process alongside
Rally. It does not encrypt client-side, which is disqualifying given the
credentials above. And its entire value is a sub-second recovery point objective
— an enormous amount of machinery for a family dashboard, where losing a day
means re-entering a few tasks.

**A Docker Compose sidecar** works on Unraid only via the Docker Compose Manager
plugin, and would mean converting Rally from a normal Docker template into a
compose stack — managing it through a different UI than every other container on
the box. A plain restic container also runs once and exits, so scheduling it
needs either a cron-capable image or an external trigger. More moving parts than
a single scheduled script, for the same result.

**The Appdata Backup plugin** is the common Unraid choice and is fine as a
complement, but it achieves consistency by stopping the container, and it writes
to a local share — so it is downtime plus a second step to get anything offsite.

**Unraid parity is not a backup.** Parity covers the array, and appdata normally
lives on a cache pool; a single-device cache has no redundancy at all. Without
something like this, the database is likely the least protected data on the
server.
