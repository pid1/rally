# Offsite backup

**Status:** recommended setup, not automated by Rally itself. Everything here is host-side configuration; no application code is involved.

Rally keeps a family's calendar, tasks, meals, and preparedness inventory in a single SQLite file. This document is how to get that file somewhere safe, on a schedule, encrypted, without adding anything to the deployment.

The worked example targets **Unraid**, since that is where Rally is deployed today, and **Cloudflare R2**, which is free at the sizes Rally produces. The shape of the job — snapshot, encrypt, upload, prune — transfers to any host.

## Why this is worth doing

Two properties of the database decide the whole design.

**It holds live credentials.** Rally stores secrets in the database, in plaintext:

| Where | What |
|---|---|
| `settings.llm_anthropic_api_key`, `settings.llm_local_api_key` | LLM API keys |
| `settings.pushover_app_token` | Pushover application token |
| `family_members.pushover_user_key` | Per-member Pushover user keys |
| `calendars.username`, `calendars.password` | Google and Apple **app-specific passwords** |

Those last ones are standing grants against real accounts. So the backup has to be encrypted *before* it leaves the host. A provider that offers "encryption at rest" is holding a key to your family's calendar accounts, which is not the same thing at all.

**It is small.** A seeded database is on the order of 100 KB, and real family use keeps it in single-digit megabytes for years. This rules out most of what a search for "SQLite backup" will recommend — those tools solve the opposite problem. At this size the storage is free and the transfer is instant, so the design can optimize entirely for *simplicity and recoverability*.

## What gets backed up

Everything in the `/data` volume:

- `rally.db` — the database, as a consistent snapshot (see below)
- `config.toml` — API keys, coordinates, timezone
- `context.txt`, `agent_voice.txt` — family context and AI voice profile

Only the database is guaranteed to be there. An instance configured entirely through the Settings UI keeps all of that in the `settings` table and has no `config.toml` at all, which is why the script backs up the whole volume by exclusion rather than naming files — whatever exists gets captured.

The `/output` volume is **not** backed up. It holds generated dashboard HTML, which is rebuilt from the database on the next generation run.

## Taking a consistent snapshot

Never back up `rally.db` by copying it while Rally is running. A plain `cp` can capture a file mid-write, and the result is a database that looks fine until the day you need it.

Use `VACUUM INTO`, which takes its snapshot through SQLite's own locking:

```sql
VACUUM INTO '/data/backup/rally-snap.db'
```

Rally keeps serving throughout — for a database this size the read lock is held for milliseconds — and the output is a consistent, compacted copy. Two details worth knowing:

- **The target must not already exist.** `VACUUM INTO` fails with `output file already exists` rather than overwriting, so the script deletes the previous snapshot first.
- **It runs in the container, not on the host.** Unraid 7.x does ship `/usr/bin/sqlite3`, so this is not strictly necessary there — but running the snapshot through Rally's own container via `docker exec` uses the same SQLite build that wrote the database, and keeps the job working on hosts where the binary is absent or older than `VACUUM INTO` (SQLite 3.27, 2019).

Measured on a live 3.1 MB production database: 0.127 s to produce a 2.3 MB compacted snapshot, with the app still serving.

## Storage: Cloudflare R2

R2 is the recommended target:

- **Free at Rally's size** — the free tier covers far more than this database will ever need.
- **No egress fees** — restoring costs nothing, which matters because a backup you are reluctant to test is not a backup.
- **Genuinely S3-compatible** — works with restic, rclone, and everything else, with no adapter.

Backblaze B2 is an equally good substitute. Avoid CDN-first storage products whose S3 compatibility is incidental; they are built to serve assets, not to be written to by backup tooling.

### Creating the bucket

1. In the Cloudflare dashboard, go to **R2** and create a bucket, e.g. `rally-backup`.
2. Under **Manage R2 API Tokens**, create a token with **Object Read & Write**, scoped to that one bucket.
3. Note the **Access Key ID**, **Secret Access Key**, and your **Account ID**.

The endpoint is `https://<account-id>.r2.cloudflarestorage.com`, and restic addresses the bucket as `s3:https://<account-id>.r2.cloudflarestorage.com/rally-backup`.

## Encryption and retention: restic

[restic](https://restic.net) encrypts client-side by default, keeps versioned snapshots, prunes on a retention policy, and verifies repository integrity — the whole job in one tool, and nothing to install on the host, since it runs from the official container image.

The versioning matters more than it might seem. A backup that simply *mirrors* the database will faithfully mirror its damage: a bad migration or a corrupted page gets copied offsite, overwriting the last good copy. Dated snapshots with a retention policy protect against the failure you are actually likely to hit.

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

This placement is deliberate and Unraid-specific. User Scripts are stored on the Unraid flash drive, which is FAT32 and cannot represent Unix permissions — a passphrase pasted into the script body would sit world-readable on a USB stick. Appdata lives on a cache pool with a real filesystem, where `chmod 600` means something.

**Record `RESTIC_PASSWORD` in your password manager.** It is not recoverable, and without it the backups are permanently unreadable. Losing it is the single most likely way this setup fails you.

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

Use the **User Scripts** plugin from Community Applications, not `crontab`. Unraid runs from RAM and rebuilds itself from the flash drive at boot, so a `crontab -e` edit disappears silently at the next reboot. User Scripts persist, and the schedule is managed in the Unraid UI alongside everything else.

Add a script named `Rally Backup`, paste the body below, then set its schedule to **Scheduled Daily** in the User Scripts page and click **Apply**. The plugin stores that choice in `/boot/config/plugins/user.scripts/schedule.json` and the daily run is driven by `/etc/cron.daily/user.script.start.daily.sh`, which the plugin installs — so nothing needs adding to `crontab` or `/etc/cron.d/root`.

```bash
#!/bin/bash
# Rally offsite backup: snapshot the database, push it to R2, prove the copy is
# restorable, then prune. Setup and rationale: docs/backup.md in the rally repo.
#
# Fails closed. Any step that does not verify aborts the run with a non-zero
# exit and an Unraid notification, and old snapshots are NOT pruned — a run that
# cannot prove itself must never be the reason good backups are deleted.
set -euo pipefail

APPDATA=/mnt/user/appdata/rally
CONTAINER=rally
BACKUP_DIR="$APPDATA/backup"
SNAPSHOT="$BACKUP_DIR/rally-snap.db"
# Verification scratch lives outside the backed-up volume so a run that dies
# mid-way can never leave a restored copy to be swept into the next backup.
VERIFY_DIR=/tmp/rally-backup-verify
NOTIFY=/usr/local/emhttp/webGui/scripts/notify

cleanup() { rm -rf "$SNAPSHOT" "$VERIFY_DIR"; }

fail() {
    echo "RALLY BACKUP FAILED: $1" >&2
    if [ -x "$NOTIFY" ]; then
        "$NOTIFY" -e "Rally backup" -s "Rally backup FAILED" -d "$1" -i "alert" || true
    fi
    cleanup
    exit 1
}
trap 'fail "unexpected error at line $LINENO"' ERR
trap cleanup EXIT

[ -f "$APPDATA/backup.env" ] || fail "missing $APPDATA/backup.env"
# shellcheck source=/dev/null
source "$APPDATA/backup.env"

restic_run() {
    docker run --rm \
        -e RESTIC_REPOSITORY -e RESTIC_PASSWORD \
        -e AWS_ACCESS_KEY_ID -e AWS_SECRET_ACCESS_KEY -e AWS_DEFAULT_REGION \
        "$@"
}

# All SQLite work runs inside Rally's container, so this job depends on no host
# sqlite3 build. $BACKUP_DIR is under the container's /data mount.
sqlite_in_container() {
    docker exec "$CONTAINER" python -c "$1"
}

mkdir -p "$BACKUP_DIR" "$VERIFY_DIR"
rm -f "$SNAPSHOT"           # VACUUM INTO refuses to overwrite an existing target
rm -rf "${VERIFY_DIR:?}"/*

# --- 1. Consistent snapshot ------------------------------------------------
# Taken through SQLite's own locking, so Rally keeps serving and the copy is
# never mid-write.
sqlite_in_container \
    "import sqlite3; c = sqlite3.connect('/data/rally.db'); c.execute(\"VACUUM INTO '/data/backup/rally-snap.db'\"); c.close()" \
    || fail "VACUUM INTO failed"
[ -s "$SNAPSHOT" ] || fail "snapshot was not produced"

# --- 2. Validate before uploading ------------------------------------------
# No point storing a corrupt snapshot, and catching it here keeps the last known
# good backup in place rather than burying it under a bad one.
integrity=$(sqlite_in_container \
    "import sqlite3; print(sqlite3.connect('/data/backup/rally-snap.db').execute('PRAGMA integrity_check').fetchone()[0])" \
    | tr -d '\r\n')
[ "$integrity" = "ok" ] || fail "snapshot failed integrity_check ($integrity); nothing uploaded"

expected_sha=$(sha256sum "$SNAPSHOT" | awk '{print $1}')

# --- 3. Upload --------------------------------------------------------------
# Back up the whole volume, minus: the live database (the snapshot stands in for
# it), any manual copies in backups/ (restic's versioning supersedes them), and
# backup.env — the credentials protecting this repository must not live inside
# it. Anything else new landing in /data is picked up without editing this file.
restic_run -v "$APPDATA:/src:ro" restic/restic backup \
    --tag rally --host unraid \
    --exclude /src/rally.db \
    --exclude '/src/rally.db-journal' \
    --exclude /src/backups \
    --exclude /src/backup.env \
    /src || fail "restic backup failed"

# --- 4. Prove it is restorable ---------------------------------------------
# The point of a backup is a successful restore, so download what was just
# stored and check it byte for byte. Comparing against the local snapshot rather
# than the live database makes this immune to writes landing mid-run.
restic_run -v "$VERIFY_DIR:/restore" restic/restic restore latest --target /restore \
    || fail "verification restore failed — backup is NOT known to be recoverable"

restored=$(find "$VERIFY_DIR" -name 'rally-snap.db' -type f | head -1)
[ -n "$restored" ] || fail "verification restore produced no rally-snap.db"

actual_sha=$(sha256sum "$restored" | awk '{print $1}')
[ "$actual_sha" = "$expected_sha" ] \
    || fail "restored copy does not match what was uploaded (expected $expected_sha, got $actual_sha)"

# --- 5. Only now is it safe to prune ---------------------------------------
restic_run restic/restic forget --tag rally \
    --keep-daily 7 --keep-weekly 4 --keep-monthly 12 --prune \
    || fail "prune failed (backup itself succeeded and was verified)"

restic_run restic/restic check || fail "repository check reported errors"

cleanup
echo "Rally backup verified and complete: $(date)"
echo "  snapshot sha256: $expected_sha"
```

That retention really does keep a year of history for a few megabytes. Measured against a live instance: the 2.2 MiB snapshot stored as **253 KiB** on the first run, and a second run minutes later added **1016 bytes**. Daily snapshots of a database that changes slowly cost almost nothing — which is exactly what makes the verification step affordable.

### Why the script verifies rather than assuming

A job that only checks whether the upload returned an error is testing the wrong property. What you actually need to know is that a *restore* would work, and the only way to know that is to do one. Because the data is tiny and R2 charges nothing for egress, downloading the copy back and comparing it byte for byte costs a few seconds and nothing at all in money.

The ordering is the substance of the design:

- **Integrity is checked before upload**, so a corrupt snapshot is never stored on top of good backups.
- **The round trip is compared against the local snapshot**, not against the live database. The live database may take writes during the run; the snapshot cannot. Comparing to it makes the check exact instead of approximate.
- **Pruning happens only after verification passes.** A run that cannot prove itself must never be the reason older, good snapshots get deleted. This is the failure mode that turns a backup system into a liability.
- **Every failure path exits non-zero and raises an Unraid notification**, so the job shows as failed in User Scripts rather than failing silently for months.

Two deletions in the script are deliberate, and both are about not leaving credentials lying around:

- **The local snapshot is removed when the run finishes**, including on failure. It is an unencrypted copy of the credentials table; there is no reason to keep one in appdata between runs.
- **`backup.env` is excluded from the backup.** Backing up the whole volume otherwise sweeps it in, which puts the restic passphrase and the R2 keys *inside the repository they protect*. It is encrypted there, so this is not an immediate exposure — but anyone who obtained the passphrase would also get keys that can delete the backups, and an R2 token is trivially re-created, so there is no recovery value to offset the risk.

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

Migrations are idempotent and run on every start, so a snapshot from an older schema is upgraded automatically on the next boot.

## Verify it works

The nightly job already restores every backup it takes and compares it byte for byte, and runs `restic check` afterward. The checks below are the ones it cannot do for itself.

**Read every stored byte**, every few months. Plain `restic check` validates the repository's structure and indexes; `--read-data` re-downloads and re-verifies the actual contents:

```bash
docker run --rm \
  -e RESTIC_REPOSITORY -e RESTIC_PASSWORD \
  -e AWS_ACCESS_KEY_ID -e AWS_SECRET_ACCESS_KEY -e AWS_DEFAULT_REGION \
  restic/restic check --read-data
```

**Prove the restored database actually runs Rally**, once at setup and after any major upgrade. Byte-identical is not the same as bootable — this is the check that catches a schema the current image can no longer migrate. Run a *second* container against a copy, so production is never involved:

```bash
TESTDIR=/mnt/user/appdata/rally-restoretest
mkdir -p "$TESTDIR/r"

docker run --rm -e RESTIC_REPOSITORY -e RESTIC_PASSWORD \
  -e AWS_ACCESS_KEY_ID -e AWS_SECRET_ACCESS_KEY -e AWS_DEFAULT_REGION \
  -v "$TESTDIR/r:/restore" restic/restic restore latest --target /restore

cp "$TESTDIR/r/src/backup/rally-snap.db" "$TESTDIR/rally.db"
rm -rf "$TESTDIR/r"

# Clear the Pushover token in the COPY so a test instance cannot send
# notifications to the family. Never do this to the live database.
sqlite3 "$TESTDIR/rally.db" "DELETE FROM settings WHERE key='pushover_app_token';"

docker run -d --name rally-restoretest -p 8181:8000 \
  -v "$TESTDIR:/data" -v rally-restoretest-output:/output \
  "$(docker inspect rally --format '{{.Config.Image}}')"

sleep 15
docker logs rally-restoretest 2>&1 | grep -iE "migration|Database ready|✗|error"
curl -s -o /dev/null -w "dashboard: %{http_code}\n" -L http://127.0.0.1:8181/dashboard

docker rm -f rally-restoretest && docker volume rm rally-restoretest-output
rm -rf "$TESTDIR"
```

Expect every migration to report idempotent success, `Database ready`, and `dashboard: 200`.

**Confirm the schedule is actually firing.** User Scripts keeps a per-script log at `/tmp/user.scripts/tmpScripts/Rally Backup/log.txt`. After the first day it should end with `Rally backup verified and complete`, and `restic snapshots` should list a new entry. To test the scheduled path immediately without waiting for the timer, run the plugin's own hook:

```bash
/etc/cron.daily/user.script.start.daily.sh
```

## Troubleshooting

**TLS handshake failures from host tools, but not from containers.** If a host-installed S3 client (`rclone`, for instance) fails against the R2 endpoint with `remote error: tls: handshake failure` while `curl` to the same URL succeeds, suspect broken IPv6 egress rather than TLS. R2 endpoints resolve to both A and AAAA records; a tool that prefers IPv6 on a host with no working IPv6 route fails in ways that read like a certificate problem. Confirm with:

```bash
curl -4 -sS -o /dev/null -w "v4: %{http_code}\n" https://<account-id>.r2.cloudflarestorage.com
curl -6 -sS -o /dev/null -w "v6: %{http_code}\n" https://<account-id>.r2.cloudflarestorage.com
```

A `400` is the expected healthy answer to an unauthenticated request. This does not affect the backup job — Docker's default bridge network is IPv4-only, so restic in a container is unaffected — but it will bite anyone debugging the endpoint with host tools first.

## Alternatives considered

**Litestream** is the answer most searches give, and it is the wrong fit here. It requires WAL journal mode, so the application's storage configuration would change to suit the backup tool. It runs as an additional process alongside Rally. It does not encrypt client-side, which is disqualifying given the credentials above. And its entire value is a sub-second recovery point objective — an enormous amount of machinery for a family dashboard, where losing a day means re-entering a few tasks.

**A Docker Compose sidecar** works on Unraid only via the Docker Compose Manager plugin, and would mean converting Rally from a normal Docker template into a compose stack — managing it through a different UI than every other container on the box. A plain restic container also runs once and exits, so scheduling it needs either a cron-capable image or an external trigger. More moving parts than a single scheduled script, for the same result.

**The Appdata Backup plugin** is the common Unraid choice and is fine as a complement, but it achieves consistency by stopping the container, and it writes to a local share — so it is downtime plus a second step to get anything offsite.

**Unraid parity is not a backup.** Parity covers the array, and appdata normally lives on a cache pool; a single-device cache has no redundancy at all. Without something like this, the database is likely the least protected data on the server.
