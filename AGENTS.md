# AI Agent Instructions

This document provides guidance for AI coding assistants (Claude, Cursor, Copilot, etc.) working on this codebase.

## About Rally

Rally is a family command center that helps families come together, coordinate their days, and make the most of every opportunity. The tone should be empowering and encouraging—helping families work hard, support each other, and excel at what they do.

### Tone & Language Principles

When generating summaries or writing user-facing content, Rally should:

- **Be encouraging and empowering** - Frame challenges as opportunities
- **Celebrate hard work** - Acknowledge effort and productivity
- **Support coordination** - Help the family work as a team
- **Be proactive** - Suggest ways to make the day successful
- **Show optimism** - Even difficult schedules are framed positively
- **Recognize citizenship** - Acknowledge responsibilities and commitments

**Good examples:**
- "You've got a full day ahead—let's make it count!" 
- "Great opportunity for focused work between 9am-2pm"
- "You're well-positioned to tackle the plumber call and grocery run"

**Avoid:**
- Passive or defeatist language
- Overwhelming the user with problems
- Making schedules sound burdensome
- Being overly formal or corporate

## Development Environment

This project uses [devenv](https://devenv.sh) for reproducible development environments.

### Quick Setup

```bash
devenv shell
setup        # runs: install-deps, db-init
dev          # starts Rally at http://localhost:8000
```

### Commands

All commands should be run inside `devenv shell`.

#### Setup & Development

| Command | Description | Blocking |
|---------|-------------|----------|
| `setup` | Initialize repo (runs: install-deps, db-init) | No |
| `dev` | Start Rally dev server (port 8000) | Yes |
| `dev-start` | Start dev server in background | No |
| `dev-stop` | Stop background dev server | No |
| `dev-status` | Check status of background processes | No |
| `dev-logs` | View last 50 lines of dev logs | No |

#### Quality & Testing

| Command | Description | Blocking |
|---------|-------------|----------|
| `lint` | Run ruff linter | No |
| `lint-fix` | Run ruff with auto-fix | No |
| `format` | Run ruff formatter | No |
| `check` | Run lint + format check | No |
| `test-generate` | Test summary generation | No |

#### Database

| Command | Description | Blocking |
|---------|-------------|----------|
| `db-init` | Initialize SQLite database | No |
| `seed` | Seed database with sample data | No |
| `resetdb` | Delete and reinitialize database | No |
| `generate` | Generate real dashboard snapshot using APIs | No |

#### Docker

| Command | Description | Blocking |
|---------|-------------|----------|
| `build` | Build Docker image | No |
| `up` | Start Docker container | No |
| `down` | Stop Docker container | No |
| `logs` | View Docker logs (follows) | Yes |
| `logs-tail` | View last 50 lines | No |
| `restart` | Restart Docker container | No |

#### Dependencies

| Command | Description | Blocking |
|---------|-------------|----------|
| `install-deps` | Install dependencies with uv | No |

## For AI Agents

**CRITICAL**: When working in this repository, follow these rules:

### 1. Dependency Management

This project uses **uv** for all Python dependency management and execution. We do NOT use `pip install -e .` or traditional pip workflows.

❌ **Don't do this:**
```bash
pip install -e .
pip install package-name
python -m module
python script.py
```

✅ **Do this instead:**
```bash
# All Python execution goes through uv
uv run python -m rally.cli
uv run python script.py
uv run ruff check .

# Or better yet, use devenv scripts (see below)
seed
lint
test-generate
```

### 2. Always Use devenv Scripts

❌ **Don't do this:**
```bash
uv run ruff check src/
uv run python -m rally.generator
uv run uvicorn rally.main:app --reload
```

✅ **Do this instead:**
```bash
lint
test-generate
dev
```

**Why:** devenv scripts are the single source of truth. They handle uv invocation correctly and ensure consistency across all environments and developers.

### 3. Use Non-Interactive Commands for Automation

When you need to start services programmatically (in scripts, tests, or automation):

❌ **Don't use interactive commands:**
```bash
dev        # This blocks! Agent will hang
logs       # This follows! Agent will hang
```

✅ **Use background commands:**
```bash
dev-start     # Returns immediately
dev-status    # Check if running
dev-logs      # View output (non-blocking)
dev-stop      # Stop when done
```

### 4. Check Process Status Before Starting

Before starting dev servers:

```bash
dev-status    # Check what's already running
```

If something is already running, you can:
- Use it as-is
- Stop it first: `dev-stop`
- View its logs: `dev-logs`

### 5. View Logs for Errors

After starting background processes:

```bash
dev-start
sleep 2       # Give it time to start
dev-logs      # Check for errors
```

### 6. Never Destructively Mutate the Local Dev Database

The dev database (`rally.db`) holds the developer's local data. It is **gitignored**,
and SQLite keeps **no history** — an `UPDATE`/`DELETE` that commits overwrites the
old value irreversibly. There is no `git checkout` to fall back on. So verification
that drives the **running app against write endpoints** (create/update/delete, or
UI actions that call them) will permanently change local data.

❌ **Don't** exercise write paths against `rally.db` for exploratory/manual testing.

✅ **Prefer the automated suite** — each test gets an isolated in-memory database
(see `tests/conftest.py`), so it never touches `rally.db`:

```bash
uv run pytest
```

✅ **If you must drive the running app against write paths**, isolate the data first:

```bash
resetdb && seed     # start from a known, reproducible sample state
```

and, before mutating any specific row, read and stash its full contents so you can
put it back. If you do mutate `rally.db` incidentally, restore it: the sample data
is defined in `src/rally/cli.py` (the `seed` command), or run `resetdb && seed` to
rebuild the whole thing. Tell the user what you changed and how you restored it.

### 7. Working with Docker

For Docker operations, use non-blocking variants:

```bash
# Start container
up

# Check logs (non-blocking)
logs-tail

# Not logs (that follows and blocks)
```

### 8. Pull Request Format

Open PRs with `gh pr create` and follow the template at
`.github/pull_request_template.md`. **`gh pr create` does not auto-apply the
template**, so fill the sections in yourself and pass them with `--body-file`.

Every PR body must have these sections (in this order):

- **Summary** — one short paragraph on what changes and why.
- **Changes** — a bulleted list of the concrete changes, grouped by area
  (backend / frontend / config / tests) when helpful; identifiers and paths in
  `backticks`.
- **Notes for reviewers** — *optional*; non-obvious decisions, trade-offs, or
  follow-ups deliberately deferred. Omit the section when there is nothing to add.
- **Testing** — what was run and verified (tests, coverage delta, manual checks),
  stated honestly.
- **Closes #\<issue\>** — the issue this resolves, so it auto-closes on merge.

Workflow conventions:

- Write a concise, imperative title that summarizes the change.
- Open as a **draft** (`gh pr create --draft`) and mark it ready
  (`gh pr ready <n>`) only once CI is green.
- Apply the appropriate label (`enhancement`, `bug`, `documentation`, …), assign
  the author, and set the milestone when the work belongs to one.
- Run the test suite before opening (see the testing note below); PR CI runs
  `pytest`, `ruff check`, and `ruff format --check` and must pass.

> **Running the suite:** tests run under the project's Python 3.14 env, so use
> `uv run pytest` (or `.devenv/state/venv/bin/pytest`) — a bare `pytest` may be
> shadowed by another interpreter on your `PATH` and fail to import.

## Example Workflows

### Setting Up Development Environment

```bash
# Enter devenv shell
devenv shell

# Run full setup (installs deps, initializes DB)
setup

# Seed database with sample data
seed

# Start development server
dev
```

### Running Tests

Before pushing, run everything CI runs, or the PR check will fail. PR CI runs
`pytest`, `ruff check`, and `ruff format --check` (see Pull Request Format).
The `check` command covers both the linter **and** the formatter check — plain
`lint` only runs `ruff check` and will miss formatting problems.

```bash
# Ensure dependencies are installed
install-deps

# Lint + format check together (mirrors CI's `ruff check` + `ruff format --check`)
check

# Run the full test suite (see the env note under Pull Request Format)
uv run pytest

# Test summary generation
test-generate
```

The design-system regression suite needs a browser and is **not** part of the
default run — `tests/visual/` skips itself when Playwright is absent, and CI
runs it as a separate job. To run it locally:

```bash
uv sync --group visual
uv run playwright install chromium
uv run pytest tests/visual -v
```

Note that `uv sync --group visual` drops the editable `rally` install. Tests
still work (pytest puts `src/` on the path), but a bare `uvicorn rally.main:app`
will then need `PYTHONPATH=src`. Re-run plain `uv sync` to restore it.

### Starting Development Server

**Interactive (for humans):**
```bash
dev
# Press Ctrl+C to stop
```

**Background (for agents/scripts):**
```bash
dev-start
# Do other work...
dev-logs     # Check output
dev-stop     # Clean up when done
```

### Making Code Changes

```bash
# Make changes to src/rally/

# Check formatting and linting
check

# Or auto-fix issues
lint-fix
format

# Test the changes
test-generate
```

### Deploying with Docker

```bash
# Build image
build

# Start container
up

# Check logs
logs-tail

# Stop container
down
```

## Database Migrations

Rally uses a simple, file-based migration system. All migrations live in the `migrations/` directory and are **idempotent** (safe to run multiple times).

### How Migrations Work

1. **On Container Startup**: `entrypoint.sh` runs `migrations/run_migrations.py` automatically
2. **Idempotent**: Each migration checks if changes are already applied before executing
3. **Ordered**: Migrations run in the order they're listed in `run_migrations.py`
4. **Fail-Safe**: If any migration fails, the container won't start

### Migration Files

- `migrations/migrate_XXX_description.py` - Individual migration scripts
- `migrations/run_migrations.py` - Migration runner (executes all migrations in order)

### Existing Migrations

- `001_add_due_date` - Add `due_date` column to `todos` table
- `002_add_family_members` - Add `family_members` and `calendars` tables, `assigned_to` on `todos`
- `003_add_settings` - Add key-value `settings` table
- `004_add_recurring_todos` - Add `recurring_todos` table and `recurring_todo_id` on `todos`
- `005_add_dinner_plan_assignees` - Add `attendee_ids` and `cook_id` to `dinner_plans`
- `006_add_reminder_window` - Add `remind_days_before` to `todos` and `recurring_todos`
- `007_add_last_generated_date` - Add `last_generated_date` to `recurring_todos` (tracks most recently generated instance to prevent duplicates)
- `008_add_caldav_support` - Add CalDAV fields (`cal_type`, `username`, `password`) to `calendars`
- `009_add_custom_recurrence` - Add `custom_rule` to `recurring_todos`
- `010_add_meal_type` - Add `meal_type` to `dinner_plans`
- `011_add_meal_reviews` - Add `rating` and `review` to `dinner_plans`
- `012_add_ai_settings_history` - Add `ai_settings_history` table; seed it from existing `agent_voice` / `family_context` settings rows, point `current_agent_voice_history_id` / `current_family_context_history_id` settings keys at the seed rows, and remove the original settings rows
- `013_add_completed_at` - Add `completed_at` to `todos`
- `014_configurable_nws_weather` - Replace OpenWeather settings with configurable NWS forecast URL
- `015_add_llm_settings_history` - Add `llm_settings_history` table; seed a coupled provider+model snapshot from the existing `llm_provider` / model settings rows and point the `current_llm_config_history_id` settings key at it (original settings rows are preserved — they remain the source of truth for the generator)
- `016_add_stem_concept_history` - Add `stem_concept_history` table (records used STEM "concept of the day" topics so the generator avoids repeating a specific topic within 60 days)
- `017_add_shopping_lists` - Add `shopping_stores`, `shopping_items`, and `shopping_item_history` tables, plus the case-insensitive unique index on store names and the unique index on `shopping_item_history.name_key`
- `018_add_sports_watchlist` - Add `followed_teams` and `sports_event_notices` tables, plus the unique index on `sports_event_notices.event_key` (records which notable upcoming events have already been announced, so one is mentioned once rather than every morning for two weeks)
- `020_add_native_calendaring` - Add the `events`, `event_attendees`, `event_overrides` and `event_notifications` tables plus their indexes (the unique index on `event_notifications` *is* the reminder send-once guarantee), add `pushover_user_key` / `pushover_device` to `family_members`, and seed one `cal_type='native'` calendar per existing family member. Purely additive
- `021_add_preparedness` - Add the `prep_locations`, `prep_items` and `prep_refresh_notices` tables plus their indexes (the unique index on `prep_refresh_notices.notice_key` *is* the refresh announce-once guarantee), and seed the `prep_notify_enabled` / `prep_notify_time` / `prep_default_remind_days` settings rows. Purely additive
- `022_add_home_location` - Seed an empty `home_location` settings row. Purely additive; `home_location()` treats a missing row and an empty one identically, so this exists to make the field visible on the settings page from the first load rather than to change behaviour
- `023_add_prep_reviews` - Add the `prep_reviews` table (stored LLM reviews of the preparedness inventory). Purely additive
- `024_add_calendar_cache` - Add the `calendar_cache` table plus its unique index on `calendar_id` (one cache row per calendar; the index is what makes the sync's get-or-create safe), and seed `calendar_sync_interval_minutes` at 15. Purely additive; the table starts empty and the first sync fills it
- `025_add_caldav_sync_tokens` - Add `calendar_cache.sync_tokens` (one RFC 6578 sync token per server-side CalDAV calendar). Purely additive; NULL means "no baseline yet" and the next sync captures one
- `019_add_llm_max_tokens` - Backfill `max_tokens`/`max_tokens_mode` (`4000`/`"custom"`) into every `llm_settings_history` row's JSON value that lacks them (unparseable rows are skipped, not rewritten), and seed the `llm_anthropic_max_tokens`, `llm_local_max_tokens`, and `llm_anthropic_max_tokens_mode` settings keys when absent. The backfilled value matches prior behavior exactly, so this migration changes nothing observable by itself

### Running Migrations

**Automatic (Docker):**
Migrations run automatically when the container starts via `entrypoint.sh`

**Manual (Development):**
```bash
# Run all migrations
python3 migrations/run_migrations.py

# Run specific migration
python3 migrations/migrate_add_due_date.py

# Test idempotency (should succeed twice)
python3 migrations/run_migrations.py && python3 migrations/run_migrations.py
```

### Creating New Migrations

1. Create `migrations/migrate_XXX_description.py` using the template below
2. Add to `migrations/run_migrations.py` migrations list
3. Test locally with `python3 migrations/migrate_XXX_description.py`
4. Deploy (runs automatically on container startup — `migrations/` is copied into the Docker image)

**Key principle:** Every migration must be idempotent - safe to run multiple times.

### Migration Template

```python
#!/usr/bin/env python3
"""Migration: Brief description of what this does.

Safe to run multiple times (idempotent).
"""
import os
import sqlite3
from pathlib import Path

def migrate():
    """Run the migration. Return True on success, False on failure."""
    db_path = os.environ.get("RALLY_DB_PATH")

    if not db_path:
        prod_path = Path("/data/rally.db")
        dev_path = Path(__file__).parent.parent / "rally.db"
        db_path = str(prod_path) if prod_path.exists() else str(dev_path)

    db_path = Path(db_path)

    if not db_path.exists():
        print(f"✓ Database not found at {db_path}")
        print("  No migration needed - database will be created with correct schema.")
        return True

    print(f"Checking database at {db_path}...")

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    try:
        # CHECK: Is this migration already applied?
        cursor.execute("PRAGMA table_info(your_table)")
        columns = [col[1] for col in cursor.fetchall()]

        if 'your_new_column' in columns:
            print("✓ Migration: your_table.your_new_column already exists (idempotent)")
            return True

        # EXECUTE: Apply the migration
        print("  Applying migration...")
        cursor.execute("ALTER TABLE your_table ADD COLUMN your_new_column VARCHAR(10)")
        conn.commit()
        print("✓ Migration complete: your_table.your_new_column added")
        return True

    except sqlite3.Error as e:
        print(f"✗ Migration failed: {e}")
        return False
    finally:
        conn.close()

if __name__ == "__main__":
    import sys
    success = migrate()
    sys.exit(0 if success else 1)
```

### Migration Best Practices

**Do:**
- Make migrations idempotent — check before changing
- Return `True`/`False` to indicate success or failure
- Use `PRAGMA table_info` to check if columns exist
- Handle missing database — it's fine if DB doesn't exist yet
- Print clear messages — use ✓ for success, ✗ for errors
- Test locally first — run multiple times to verify idempotency

**Don't:**
- Drop data — migrations should be additive
- Use external files — keep migration logic self-contained
- Skip idempotency checks — always check before executing

### SQLite Migration Patterns

**Add Column:**
```python
cursor.execute("PRAGMA table_info(table_name)")
columns = [col[1] for col in cursor.fetchall()]
if 'new_column' not in columns:
    cursor.execute("ALTER TABLE table_name ADD COLUMN new_column TYPE")
```

**Create Table:**
```python
cursor.execute("""
    CREATE TABLE IF NOT EXISTS table_name (
        id INTEGER PRIMARY KEY,
        field VARCHAR(100)
    )
""")
```

**Add Index:**
```python
cursor.execute("""
    CREATE INDEX IF NOT EXISTS idx_name
    ON table_name(column)
""")
```

## Project Structure

```
rally/
├── src/rally/
│   ├── __init__.py
│   ├── main.py           # FastAPI application
│   ├── database.py       # SQLAlchemy database setup
│   ├── models.py         # Database models (FamilyMember, Calendar, Event, EventAttendee, EventOverride, EventNotification, Setting, AISettingsHistory, LLMSettingsHistory, StemConceptHistory, DashboardSnapshot, Todo, RecurringTodo, ShoppingStore, ShoppingItem, ShoppingItemHistory, DinnerPlan)
│   ├── schemas.py        # Pydantic schemas
│   ├── cli.py            # CLI commands (seed, etc.)
│   ├── recurrence.py     # Recurring todo processing (template → instance generation, next-date calculation)
│   ├── notifications.py  # Pushover transport, recipient resolution, due-reminder scan
│   ├── preparedness.py   # Refresh schedule arithmetic and the daily refresh digest
│   ├── golist.py         # Go list grouping plus the md/csv/pdf renderers
│   ├── prep_review.py    # LLM review of the inventory: prompt, grounding rules, normalising
│   ├── calendars/        # One normalized event shape for every calendar source
│   │   └── cache.py      # Cached external occurrences + the concurrent background sync
│   │   ├── occurrence.py # The Occurrence dataclass + timezone/DST helpers
│   │   ├── declined.py   # Declined/cancelled detection (one copy, was two)
│   │   ├── ics.py        # iCalendar component → Occurrence, shared by ICS and CalDAV
│   │   ├── native.py     # Rally-owned events: RRULE expansion and overrides
│   │   ├── inputs.py     # Submitted local times → stored columns
│   │   ├── merge.py      # Cross-calendar dedupe, attendance union, ordering
│   │   └── sources.py    # Fetch every configured calendar into one merged list
│   ├── generator/
│   │   ├── __init__.py
│   │   ├── generate.py   # Summary generation logic with calendar, todos, and dinner plans
│   │   └── __main__.py   # CLI entry point
│   ├── utils/
│   │   ├── __init__.py
│   │   ├── settings.py   # Settings-backed helpers (today_start_utc, local_timezone_name) — kept out of timezone.py to avoid a models.py import cycle
│   │   └── timezone.py   # Timezone helpers (now_utc, today_utc, today_local, ensure_utc)
│   └── routers/
│       ├── __init__.py
│       ├── dashboard.py     # Dashboard routes
│       ├── events.py        # Calendar event CRUD, occurrence expansion, notify
│       ├── todos.py         # Todo CRUD API
│       ├── shopping.py      # Shopping list, store, and autocomplete-suggestion API
│       ├── recurring_todos.py # Recurring todo template CRUD API
│       ├── dinner_planner.py # Dinner plan CRUD API
│       ├── family.py        # Family member CRUD API
│       └── settings.py      # Settings and calendar management API
├── static/
│   ├── styles.css           # Application stylesheet (see the Design System section)
│   ├── modal.js             # Shared modal chassis: scroll fade, show/hide
│   └── meal_edit_modal.js   # Shared meal add/edit modal behaviour
├── templates/
│   ├── dashboard.html       # Generated dashboard template
│   ├── calendar.html        # Month and agenda calendar views
│   ├── todo.html            # Todo management page
│   ├── todo_completed.html  # Read-only previously-completed tasks page
│   ├── shopping.html        # Shopping list page
│   ├── dinner_planner.html  # Dinner planner page
│   └── settings.html        # Settings, family member, and calendar management page
├── config.toml.example   # Example configuration file
├── context.txt.example   # Example family context
├── agent_voice.txt.example # Example AI agent voice/tone profile
├── migrations/            # Database migration scripts
│   ├── migrate_add_due_date.py        # Migration 001: add due_date to todos
│   ├── migrate_add_family_members.py  # Migration 002: add family_members, calendars, assigned_to
│   ├── migrate_add_settings.py        # Migration 003: add settings table
│   ├── migrate_add_recurring_todos.py # Migration 004: add recurring_todos table, recurring_todo_id on todos
│   ├── migrate_add_dinner_plan_assignees.py # Migration 005: add attendee_ids, cook_id to dinner_plans
│   ├── migrate_add_reminder_window.py # Migration 006: add remind_days_before to todos and recurring_todos
│   ├── migrate_add_last_generated_date.py # Migration 007: add last_generated_date to recurring_todos
│   ├── migrate_add_caldav_support.py  # Migration 008: add CalDAV fields to calendars
│   ├── migrate_add_custom_recurrence.py # Migration 009: add custom_rule to recurring_todos
│   ├── migrate_add_meal_type.py       # Migration 010: add meal_type to dinner_plans
│   ├── migrate_011_add_meal_reviews.py # Migration 011: add rating and review to dinner_plans
│   ├── migrate_012_add_ai_settings_history.py # Migration 012: add ai_settings_history table
│   ├── migrate_015_add_llm_settings_history.py # Migration 015: add llm_settings_history table
│   ├── migrate_017_add_shopping_lists.py # Migration 017: add shopping list tables
│   ├── migrate_018_add_sports_watchlist.py # Migration 018: add followed_teams, sports_event_notices tables
│   ├── migrate_019_add_llm_max_tokens.py # Migration 019: backfill per-provider LLM max tokens settings
│   ├── migrate_020_add_native_calendaring.py # Migration 020: event tables, Pushover columns, native calendars
│   ├── migrate_021_add_preparedness.py # Migration 021: preparedness stock, locations, refresh notices
│   └── run_migrations.py              # Migration runner (executes all migrations in order)
├── data/                 # Mounted in container (not in git)
│   ├── config.toml       # API keys, URLs, coordinates (optional if using Settings UI)
│   ├── context.txt       # Family context for LLM
│   ├── agent_voice.txt   # Agent voice profile
│   └── rally.db          # SQLite database
├── devenv.yaml           # devenv configuration
├── devenv.nix            # Development scripts
├── pyproject.toml        # Python dependencies (Python 3.14)
├── uv.lock               # Locked dependency versions
├── Dockerfile            # Production container
├── entrypoint.sh         # Docker entrypoint (migrations + scheduled generation + web server)
├── LICENSE
└── README.md
```

## Current Implementation Status

**Implemented:**
- ✅ FastAPI web application with routes
- ✅ Summary generation (`rally.generator`) with ICS parsing and recurring event support
  - LLM system prompt includes task filtering guideline (guideline 10): the LLM only references tasks explicitly listed in the TODOS section of its prompt
  - Todo and dinner plan date comparisons use the user's configured local timezone
- ✅ Configuration via Settings UI (stored in DB) with config.toml fallback
- ✅ **Native calendaring** (`/calendar`) — Rally owns events, and shows them
  - One normalized `Occurrence` shape (`src/rally/calendars/`) produced by the native, ICS and CalDAV adapters and merged in one place. `generate.fetch_calendars()` is now a thin caller
  - Fixed four defects the old dict-based read path made unavoidable: events sorted lexicographically by a 12-hour clock string (so 9 AM sorted after 1 PM), all-day events rendered as midnight appointments (a `date` also has `strftime`), a `(date, title)` dedupe key that dropped the second same-named event of a day, and a 7-day window measured in UTC dates
  - `events` / `event_attendees` / `event_overrides` tables. Times are stored **twice on purpose**: `start_utc`/`end_utc` are exclusive instants that order a day correctly, `start_date`/`end_date` are inclusive local dates that render correctly. Deriving either from the other at read time is where the all-day off-by-one lives
  - `tzid` is captured per event, so changing the family's timezone never re-times history
  - Recurrence is **RFC 5545 RRULE**, expanded through `recurring_ical_events` — the same expander the ICS path uses, so a 7:00 PM weekly event stays 7:00 PM across a DST transition and there is only one place for that bug to live. The UI offers the familiar Rally choices and compiles them to RRULE; nobody types one
  - Nonexistent local times (2:30 AM on spring-forward) shift forward by the gap; ambiguous ones (1:30 AM on fall-back) take the first instant. Both are policy, applied in `resolve_local` and tested
  - Per-occurrence edits via `event_overrides`, keyed on the **original** occurrence date rather than an index — an index shifts the moment an earlier occurrence is cancelled
  - Expansion is capped at 1,000 occurrences per event per query; hitting the cap logs and truncates rather than raising
  - Native calendars are rows in `calendars` with `cal_type='native'` and no URL, so per-member ownership, the Settings CRUD screen and the generator's join all apply unchanged. Every family member gets one; the router creates one on demand if none exists
- ✅ **Pushover reminders to an event's attendees** (Settings → Notifications)
  - `pushover_app_token` identifies the install; `family_members.pushover_user_key` identifies a person. A member without a key is never notified, which is the default rather than an error
  - Recipients are the event's **attendees**, never "everyone" — notifying four phones for one child's appointment is how a notification feature gets muted
  - Two paths: a reminder lead time (`events.notify_minutes_before`) and an explicit `Notify attendees`. Both go through one `send_pushover` and one recipient resolver
  - Lead time is subtracted from the **resolved occurrence**, not the series start; anything else is an hour wrong for half the year
  - `event_notifications` mirrors `sports_event_notices`: its unique index on `(event_id, occurrence_date, family_member_id, kind)` *is* the send-once guarantee. A **failed** send is recorded but does not consume the slot, so a brief outage cannot permanently eat a reminder
  - A window missed by more than `REMINDER_GRACE_MINUTES` (15) is **dropped, not replayed** — a push at 4:05 for a 2:30 reminder misinforms rather than reminds
  - `check_due_reminders` runs from a minute loop in `entrypoint.sh` *and* opportunistically from `GET /api/events`, gated to once a minute. The container loop only exists under Docker, so without the second hook a `dev` instance would never send one — same reasoning as the shopping retention purge
  - Failures are logged and recorded, never raised: a push cannot fail an API request or a summary
- ✅ Calendar integration (Google Calendar, iCloud) - filters to next 7 days, deduplicates, handles declined events
- ✅ Weather integration (configurable National Weather Service forecast URL — DWML feed)
- ✅ Configurable LLM provider - Anthropic Claude or any OpenAI-compatible API
- ✅ Idempotent database migrations - Run automatically on container startup
- ✅ SQLite database with FamilyMember, Calendar, Setting, DashboardSnapshot, Todo, RecurringTodo, and DinnerPlan models
- ✅ Dashboard caching via DashboardSnapshot table (no auto-generation on page load)
- ✅ Dashboard route (`/dashboard`) - renders from cached snapshot only
- ✅ Navigation between Dashboard, Todos, Dinner Planner, and Settings
- ✅ Family members - Full CRUD API and UI
  - Color-coded identities for each family member
  - Used for calendar ownership and todo assignment
- ✅ Calendar management - Full CRUD API and UI
  - Add ICS calendar feeds linked to family members
  - Optional owner email for accurate declined-event detection
- ✅ Settings management - Key-value store with web UI
  - Configure LLM provider, API keys, timezone
  - DB settings take precedence over config.toml
  - `stem_concept_enabled` ("true"/"false") toggles the STEM Concept of the Day feature (Learning section)
  - `shopping_list_in_summary_enabled` ("true"/"false", default "false") folds open shopping items into the daily summary (Shopping List section)
  - `shopping_last_purge_date` (local YYYY-MM-DD) is internal bookkeeping written by the shopping retention purge — never surfaced in the UI
  - `home_location` (free text, e.g. "Highland Village, TX") is the family's home, sent to the LLM as its own `HOME:` block alongside `FAMILY CONTEXT`. First-party rather than prose inside the context so other views can read it structurally. An unset value omits the whole block — a labelled section with nothing after it invites the model to invent one
  - `calendar_sync_interval_minutes` (default "15") is how stale a cached external calendar may get before the background sync refreshes it
  - `prep_overdue_in_summary_enabled` ("true"/"false", default **"true"**) folds preparedness stock that is past its refresh date into the daily summary. Defaults on, unlike the shopping and sports toggles: those add a standing block that costs tokens every day, whereas this one is normally empty and omits itself entirely, so it only costs anything on the days it matters
  - `prep_review_enabled` ("true"/"false", default **"false"**) adds the `Review` button to `/preparedness`. Off by default because it is a real LLM call and is only useful once a reasonable amount of stock has been entered
  - `prep_notify_enabled` ("true"/"false", default "true"), `prep_notify_time` (local HH:MM, default "08:00") and `prep_default_remind_days` (default "14") drive the preparedness refresh digest. `prep_last_digest_date` is internal bookkeeping written by the once-per-local-day gate — never surfaced in the UI, exactly like `shopping_last_purge_date`
  - `sports_watchlist_enabled` ("true"/"false", default "false") folds tonight's games and notable upcoming events for followed teams into the daily summary (Sports section)
  - `llm_anthropic_max_tokens` / `llm_local_max_tokens` (default `"4000"` each) are the per-provider token budgets `_call_llm` sends — each provider owns its own key, so switching `Provider` never carries one provider's budget onto the other. `llm_anthropic_max_tokens_mode` (`"model_max"` or `"custom"`, default `"custom"`) is Anthropic-only; in `"model_max"` mode the value is resolved from the Anthropic Models API at save time (not on every generation run) and stored, not re-resolved later — rollback restores the stored number verbatim rather than re-resolving it
  - Connection verification on save: LLM, Weather, Calendar, and Followed Team settings show a verification modal with spinner, checkmark on success (auto-closes), or error message with Close button on failure
- ✅ AI settings snapshotting with version history and rollback
  - `agent_voice` and `family_context` each have their own Save button and Version History link on the settings page
  - Every explicit save inserts a versioned snapshot into `ai_settings_history` (`field_name` discriminator); the active snapshot per field is referenced by the `current_agent_voice_history_id` / `current_family_context_history_id` settings keys
  - Version History modal lists snapshots newest first with a `Current` badge and in-place expandable value previews; **Change Version** rolls the field back (bumps `last_used_at`, repoints the setting, no new row) and updates the field without a page reload
  - Fields roll back independently; all snapshots are retained indefinitely
- ✅ LLM settings snapshotting with version history and rollback
  - The LLM section's `Provider`, `Model`, and per-provider `Max Tokens` (plus, for Anthropic, the budget `Mode`) are versioned as a **single coupled snapshot** (`llm_config`) — saving the LLM form records one `llm_settings_history` row whose JSON `value` captures all of them together; the active snapshot is referenced by the `current_llm_config_history_id` settings key
  - The LLM section has one `Save` button and one `Version History` link; the shared Version History modal shows each snapshot's `Provider` / `Model` / `Max Tokens` (plus `(model maximum)` when that snapshot used auto mode), and **Change Version** restores all of it together (select flips, provider fields toggle, model and max-tokens inputs update, budget radio flips — no page reload, no new row). Rollback restores the snapshot's stored `max_tokens` verbatim; it never re-resolves against the provider
  - The plain `llm_provider` / `llm_local_model` / `llm_anthropic_model` / `llm_anthropic_max_tokens` / `llm_local_max_tokens` / `llm_anthropic_max_tokens_mode` settings keys remain the source of truth read by the generator; save and rollback keep them in sync
  - **Budget** (Anthropic only): `Model maximum` resolves the model's real output cap via `client.models.retrieve(model)` at save time and stores the returned integer — the 4 AM job never makes this call itself. An unresolvable model name (typo, missing API key) rejects the save with the error shown in the verify modal, and writes no snapshot. `Custom` accepts any positive integer with no upper bound — model ceilings differ by provider and rise over time, so Rally does not hardcode one. The local provider has no `Budget` control; it is always a plain `Custom` value
- ✅ Todo management - Full CRUD API and UI
  - Create, read, update, delete todos
  - Optional due dates with native HTML5 date picker
  - Assign todos to family members
  - Configurable reminder window (`remind_days_before`) — controls when a todo appears in LLM briefings relative to its due date. Uses local timezone (not UTC) for date comparisons.
  - AI formats due dates with day-of-week (e.g., "[Due Friday, Feb 20]")
  - Overdue styling for past-due items
  - Completion tracking — a completed todo stays on `/todo` until the end of the local day it was completed
  - Integrated into LLM generator for schedule optimization
  - Luxury UI with inline editing
- ✅ Completed tasks history (`/todo/completed`)
  - Read-only archive of todos completed before today: no add, edit, delete, or completion checkbox
  - Mirrors the `/todo` layout (same heading, toolbar, and list styles) minus the Recurring Tasks section
  - Two extra sort options — `Completion Date (Most Recent)` (default) and `Completion Date (Oldest)` — alongside the `/todo` sorts
  - Assignee filter chips behave as on `/todo`; each row shows its completion date beneath the due date
  - Paginated 50 at a time via a `Load more` button; changing sort or filter resets to the first page
  - The two pages **partition** all todos — the local-midnight boundary comes from the shared `today_start_utc()` helper in `routers/todos.py`, so every todo appears on exactly one of them
- ✅ Recurring todos - Full CRUD API and UI
  - Define recurring templates (daily, weekly, monthly)
  - Configurable recurrence day (day-of-week for weekly, day-of-month for monthly)
  - Optional due date and reminder window per template
  - Assign to family members
  - Auto-generates concrete todo instances when due and no open instance exists
  - Recurrence processing runs during dashboard generation
  - Activate/deactivate templates without deleting
- ✅ Shopping list (`/shopping`) - Store-grouped family shopping list, a peer of Tasks and the Meal Planner
  - `Add Item` is the header button, in the same position and styling as `Add Task` and `Add Meal`, and opens a dual-mode modal (add/edit) following `todo.html` exactly. `Save` closes it — burst entry was tried inline and as a stay-open modal, and both times cost more in consistency than they bought in keystrokes. The store select reads `Anywhere` on every open, ignoring the active chips, matching `openAddModal()` on `/todo`
  - Autocomplete is a custom dropdown (not a native `datalist`) reading `GET /api/shopping/suggestions` server-side, with a ~150 ms debounce and a request-sequence guard against out-of-order replies. ↑/↓ move, Enter accepts, Esc dismisses, `×` forgets a suggestion. Accepting fills the store **only when the user hasn't already chosen one**. `note` is deliberately not restored. The menu lives inside `.modal-body`, which is a scroll box, so it is capped at 240px with its own scroll rather than spilling down the page. Wired in add mode only — editing is a correction, not a lookup
  - Completed items stay on the list until **local midnight**, exactly like tasks, via the shared `today_start_utc()` helper in `utils/settings.py`. There is no countdown and no client-side expiry sweep — the page just refetches periodically
  - Purchased items live on their own page (`/shopping/purchased`), reached by a `.view-switch` link exactly as `/todo/completed` is. A checkbox that changes what the list means underneath you is a mode; the archive is different data with a different lifetime. Backed by `GET /api/shopping/purchased` — store chips filter client-side, search filters server-side
  - Store filter chips describe **what is on the list**, not what stores exist: a store earns a chip when it has an item in the current fetch, or when it is currently selected. That second clause prevents a filter that cannot be seen or undone. There is no `All` chip — no selection is the unfiltered state, matching the assignee chips on `/todo`
  - `Manage stores` sits in the Store toolbar group beside the chips it manages, styled `.filter-clear`. Opening it from the page rather than from the item modal designs the stacked-overlay problem out instead of mitigating it
  - **Two separate memories, deliberately.** `shopping_items` is a 30-day rolling record whose completed rows are *deleted*; `shopping_item_history` is permanent and deduplicated with a use counter. The purge is safe precisely because autocomplete reads history, not items — trimming one never damages the other
  - The purge runs opportunistically from the items listing (the `process_recurring_todos` precedent), gated on the `shopping_last_purge_date` settings row so it executes at most once per local day. The 4 AM container job would be the obvious home but lives in `entrypoint.sh` and only runs under Docker, so a `dev`-served instance would never purge
  - Open items optionally feed the AI daily summary via `shopping_list_in_summary_enabled` (Settings → Shopping List, default off). Completed items never reach the LLM
- ✅ STEM Concept of the Day - Optional family learning feature (toggle in Settings → Learning)
  - When `stem_concept_enabled` is "true", the generator adds a `stem_concept` object to the summary JSON (title, field, explanation, and age-appropriate `activities`)
  - The LLM tailors ideas to the ages described in FAMILY CONTEXT and keeps each idea super easy to fold into the day's existing plans
  - Rendered as a dedicated dashboard card; when disabled, the field is omitted from the schema and nothing renders
  - The LLM-as-judge eval exempts `stem_concept` from groundedness/completeness (it is intentionally generative)
  - Used concepts are recorded in the `stem_concept_history` table (one row per `(title, used_on)`). Concepts used within the last 60 days (`STEM_REPEAT_WINDOW_DAYS`) are injected into the generation prompt as a "do not reuse" list, so a specific topic won't repeat within that window; a specific topic older than 60 days drops off the list and may recur. Different sub-topics within the same broader area are always allowed
- ✅ Sports watchlist - Optional 14-day TV and radio listings for followed teams (toggle in Settings → Sports)
  - Two blocks: **Tonight** lists every event today, notable or not (the direct replacement for checking a listings site); **Coming up** lists only notable events on days 2-14, each announced **once** via the `sports_event_notices` table
  - Television and radio are separate fields end to end and never concatenated — broadcast lists mix radio callsigns in with TV channels, and a naive join renders a Rangers game as "Peacock, ERADM"
  - **Two providers on purpose.** Baseball uses MLB statsapi, the only source that carries radio (measured: 100% of Rangers games vs 2 entries across ESPN's six sources). Everything else uses ESPN
  - Notability is **per sport**, because a 17-game season and a 162-game season disagree about what "ordinary" means. Every NFL and racing event qualifies; NHL and MLB require an opener, postseason, national TV, a league special day, a first division meeting, or a standings-driven reason. Preseason never qualifies
  - Standings (`espn.fetch_standings` / `mlb.fetch_standings`) back the record-driven rules. Requested at `level=3` so division membership arrives with the records — the schedule payload carries no team grouping at all. **Reasons may cite a record or a streak, never a game result**; scores remain a non-goal
  - ESPN gotchas the adapter guards, each of which otherwise produces a silent wrong answer: a bare team-schedule call returns only the season type the calendar is in (so all three are requested and merged), `?dates=` is ignored on team endpoints (so the window is filtered locally), `market: National` is meaningless in the NFL, and **regional entries are dropped entirely because `market` does not identify whose feed it is** — measured across a full Stars season, all 58 regional TV rows are tagged `Home` and every one is the opponent's network
  - All calls are issued concurrently under one short overall budget and are best-effort: a provider outage degrades to a missing section, never a failed summary
- ✅ Dinner planner - Full CRUD API and UI
  - Multiple plans per date (e.g. half the family at a restaurant, half eating at home)
  - Optional attendees: select which family members are eating (defaults to everyone)
  - Optional cook assignment: who's preparing the meal
  - Next 7 days display with smart date formatting
  - LLM generator annotates plans with attendee/cook names for smarter reminders
  - Luxury UI matching Rally aesthetic
- ✅ Preparedness (`/preparedness`, `/go-list`) - Emergency stock with refresh schedules, a daily Pushover digest, and a printable go list
  - Items carry a name, free-text quantity, location and notes. Quantity is deliberately *not* parsed: with no par levels or low-stock alerts in scope, an integer would be structure bought for features that are not being built and paid for on every entry
  - Refresh is one of three modes — none, a fixed date, or every N months. Month arithmetic clamps (31 Aug + 6 months is 28 Feb), reusing `recurrence.py`'s helper
  - `Refreshed` re-anchors an interval on the **actual** refresh date, because for physical stock the clock starts when you swap it. A spent one-shot date becomes unscheduled rather than inventing a date Rally cannot know
  - One **digest** per day covering everything due, to every family member with a Pushover key — the household, not an event's attendees, because the water drums belong to the house. Each item is announced once per refresh date via `prep_refresh_notices`; a failed send records nothing so the next pass retries
  - The digest rides the existing minute loop (`python -m rally.notifications`) and the opportunistic API hook, rather than growing a second scheduler
  - The go list groups every item by location in walking order, prints cleanly, and exports as Markdown, CSV or PDF
- ✅ External calendar cache - `/calendar` reads from the database, never the network
  - The page was fetching every remote feed synchronously on every request: **11.5s measured in production** across three sources, serially, with one 8.9MB ICS feed accounting for 6.3s. Reads now come from `calendar_cache` and are **0.008s** — same 81 occurrences, verified against the live feeds
  - **Native events are deliberately not cached.** They are a local query (0.13s), they are the events most likely to have just been edited, and serving them stale would make Rally feel broken where it owns the data
  - Syncs run **concurrently** (`ThreadPoolExecutor`, 4 workers): a pass costs the slowest feed rather than the sum
  - **CalDAV syncs are incremental via RFC 6578 sync tokens.** Handing the server back last pass's token asks *what changed* without downloading anything — iCloud answers a delta call in ~0.12s. Measured on the CalDAV leg alone: **4.01s → 1.39s (2.9x)**. A server without sync-collection returns `None` from `sync_probe` and the full fetch happens exactly as before, so Google's endpoint is unaffected either way. `disable_fallback=True` is deliberate: the library will otherwise emulate sync-collection with a full listing, which reports every object as changed and costs more than the fetch it is meant to avoid
  - Syncs are **incremental** for ICS too. A conditional request (`If-None-Match` / `If-Modified-Since`) turns an unchanged feed into a 304; failing that, a semantic fingerprint skips the re-expansion. Neither production feed sends a validator, so the fingerprint is what fires here — and it must be *semantic*, because Google rewrites `DTSTAMP` on every response **and** returns the VEVENTs in a different order each time, so a raw body hash reported "changed" every single fetch. The fingerprint unfolds continuation lines, drops `DTSTAMP`, sorts, and hashes; an incremental pass drops from 6.5s to 3.4s
  - A failing feed **keeps serving its last good occurrences** and names itself, rather than blanking the calendar. `/calendar` shows how stale the cache is and offers a `Refresh` button
  - The generator still fetches **live** (`use_cache` defaults to False): a briefing built from a cache that had been failing silently for a day would be wrong, and it is the one caller with nobody watching
- ✅ Overdue preparedness stock in the daily summary (toggle in Settings → Preparedness, default on)
  - `load_overdue_prep_items()` lists only genuinely **overdue** items, never "due soon". The Pushover digest already announces the approach once per refresh date; the briefing is the standing nag for the ones nobody dealt with, and repeating every upcoming refresh would be noise
  - The section and its guideline are both omitted when nothing is overdue — an empty labelled section invites the model to comment on it anyway
  - The guideline states the section is the complete list of overdue items, so the model cannot pad it, and tells it to keep the mention to one line of housekeeping rather than the theme of the day
  - **Read-only with respect to `prep_refresh_notices`.** A summary run must never disturb the digest's announce-once guarantee; there is a test asserting the notice count is unchanged
- ✅ Preparedness AI review (`prep_review.py`, toggle in Settings → Preparedness) - Asks the configured LLM what the kit is missing
  - Sees the **entire** inventory, family members, the active family context (which is where ages come from — there is no age column) and the home location from #147
  - **Groundedness is the whole design.** Asking what is *absent* is the prompt shape most likely to produce invention, so the model is told the inventory is the only evidence of what the family owns, warned not to flag a category the list already covers under different words, and required to put anything it does not know — unstated ages, unset home — into an `assumptions` field rather than guessing. Absent inputs are passed as an explicit `(not recorded)` so there is no silent hole to fill
  - Responses are normalised before storage: unknown priorities coerce to `medium`, gaps without an item are dropped, and the list is capped — a review is read by someone deciding what to buy, so a half-parsed field is worse than a missing one
  - Snapshotted into `prep_reviews` and read back on view, following `DashboardSnapshot`. The response carries `stale` so a review of 38 items is visibly stale once you hold 44
- ✅ Seed command for development data
- ✅ Generate command for real API data
- ✅ Scheduled generation at 4:00 AM in configured timezone (in Docker)
  - Reads timezone from DB settings or config.toml (default: UTC)
  - Uses date-based tracking to prevent duplicate runs
  - Robust against server timezone settings
- ✅ Environment mode detection (dev/production)
- ✅ Elegant grayscale design with serif typography
- ✅ Static CSS stylesheet (`static/styles.css`), organised in token/base/primitive/component layers
- ✅ Design system: tokens, layout primitives, one button family, one modal chassis (`docs/visual-design-system.md`, `/styleguide`)
- ✅ Design-system regression tests: static stylesheet lint plus a Playwright suite (`tests/test_stylesheet.py`, `tests/visual/`)
- ✅ uv-based dependency management

## Design System

`static/styles.css` is organised in layers — tokens, base, layout primitives,
components, page-specific, responsive — in that order. A rule's position tells
you its blast radius. Full rationale and the audit that produced it:
`docs/visual-design-system.md`. Live reference: `/styleguide`.

When touching the UI:

- **Use tokens, never literals.** `var(--space-4)`, `var(--text-sm)`,
  `var(--ink-muted)`. A raw px or hex in a component is a bug unless it is a
  1px hairline. `tests/test_stylesheet.py` fails the build otherwise.
- **Text is `--ink`, `--ink-muted` or `--ink-subtle`.** `--rule` and
  `--rule-subtle` are hairlines and fail WCAG AA as text.
- **Buttons are `.btn` plus `--secondary`, `--quiet`, `--sm`.** Do not add a
  new button class; Save must look the same everywhere it appears.
- **Never write `outline: none`.** One `:focus-visible` rule in the base layer
  covers everything.
- **Page structure is `.page.stack` > `.page-header` + `.toolbar` + content.**
  Spacing between blocks comes from `.stack`, not from component margins.
- **Toolbars own their reset slot.** `Clear Filters` goes in `.toolbar-reset`
  as the toolbar's last child, never inside a `.toolbar-group`.
- **Modals are `.modal-content > h3 + .modal-scroll > .modal-body`**, and the
  page loads `/static/modal.js`.
- **Hit areas are `var(--target-min)`**, which is 44px on coarse pointers and
  narrow viewports. The calendar month grid is the case where this bites: seven
  columns at 390px leave 42px per day, so the grid is **hidden below 768px**
  and `/calendar` forces the agenda view rather than shipping a target nobody
  can hit.

Run `uv run pytest tests/test_stylesheet.py` for the static checks, and the
visual suite (above) before shipping a layout change.

## Application Routes

### Page Routes
- `/` - Redirects to `/dashboard`
- `/dashboard` - Serves the generated daily summary from cached snapshot (shows error if missing)
- `/calendar` - Day, Week, Month and Agenda views of every calendar source. **The week starts Sunday.** Prev/Today/Next move by the selected slice — a day, a week, a month, or the 30-day agenda window. Every view is reachable at every width: the month grid was previously hidden below 768px on a measurement of 42px per column, but that was taken at 320px; re-measured, a 390px viewport gives 51px per column and 360px gives 47px, both clear of the 44px rule. On a phone the grid drops event text and shows a date plus a dot per member, with the whole cell as one tap target, and it scrolls inside its own container below 340px. A phone lands on **Day** — desktop still lands on Month. `Add Event` opens a dual-mode modal carrying attendees, recurrence and a reminder lead time; editing an occurrence of a series prompts for scope (this / this and following / all) with three buttons rather than a select. External events render read-only. **Below 768px the month grid does not render at all** and the page forces the agenda view: seven columns leave 42px per day, which cannot carry a 44px tap target
- `/todo` - Todo management page with full CRUD interface
- `/todo/completed` - Read-only page of todos completed before today (local time); reachable only via the `View completed tasks` link on `/todo`, not from the nav bar
- `/shopping` - Shopping list page: an `Add Item` header button opening a dual-mode modal with history-backed autocomplete, store grouping, store filter chips derived from the items on the list, and a `Manage stores` button in the Store toolbar group
- `/shopping/purchased` - Read-only page of items purchased before today (local time), grouped by store; reachable only via the `View purchased items` link on `/shopping`, not from the nav bar
- `/dinner-planner` - Dinner planning page with date picker and plan management
- `/settings` - Settings, family member, calendar, and followed-team management page
- `/styleguide` - Design system reference: every component and state rendered from the real stylesheet. Unlinked from the nav, but it ships — a styleguide that exists only in development stops matching production
- `/preparedness` - Preparedness stock, grouped by location. Location and status chips, search, and an `Add Item` modal carrying the refresh schedule. Each scheduled row has a `Refreshed` button — the one action performed while standing in the garage holding the thing
- `/go-list` - The printable packing list: every item grouped by location, in walking order, with the unassigned group last. Print stylesheet plus Markdown / CSV / PDF export

### API Routes
- `/api/dashboard/regenerate` - Force dashboard regeneration and save new snapshot
- `/api/events` - Calendar events. **Two shapes travel through here and they are deliberately different**: an *event* is the stored rule (what the edit form reads), an *occurrence* is one dated instance of it (what every view renders)
  - `GET /api/events?start=&end=&member=&source=` - Expanded **occurrences** from every source, merged and ordered. Local dates; window capped at 366 days; repeatable `member` filters by attendee with OR semantics; `source` is `all` (default), `native`, or `external`. Also runs the once-per-minute due-reminder check
  - `POST /api/events` - Create. Times are **local wall times plus `tzid`**, never UTC instants — the browser does no timezone maths. An all-day `end` is the inclusive last day. `rrule` is validated by parsing it
  - `GET /api/events/{id}` - The stored series row plus its overrides
  - `GET /api/events/{id}/occurrences?start=&end=` - Occurrences of one series, so the UI can show what a change affects
  - `PUT /api/events/{id}?scope=this|following|all&occurrence_date=` - `this` writes an `event_overrides` row keyed on the **original** occurrence date; `following` truncates the series with `UNTIL` and creates a new event carrying the tail (moving the overrides at or after the split with it); `all` updates the row and **keeps existing overrides** — a moved occurrence stays moved. `occurrence_date` is required for the first two
  - `DELETE /api/events/{id}?scope=…&occurrence_date=` - Cancel one occurrence, truncate the tail, or delete the event and cascade its attendees, overrides and notifications (SQLite does not enforce the references)
  - `POST /api/events/{id}/notify` - Push now to the event's attendees. Returns `{sent, skipped, failed}` **by name**: "it worked" and "both phones buzzed" are different claims, and an attendee with no Pushover key is reported as skipped rather than silently dropped
- `/api/todos` - Todo CRUD endpoints
  - `GET /api/todos` - List todos (incomplete, plus those completed since local midnight today)
  - `GET /api/todos/completed` - List todos completed **before** local midnight today — the exact complement of the above. Query params: `sort` (one of `completed-newest` (default), `completed-oldest`, `due-soonest`, `due-furthest`, `assignee`, `newest`, `oldest`), repeatable `assignee` (family member ID and/or `unassigned`; OR semantics, empty means all), `limit` (default 50, max 200), `offset`. Returns `{items, has_more}`. Sorting, filtering and paging are server-side; recurring processing is deliberately **not** run here.
  - `POST /api/todos` - Create new todo
  - `GET /api/todos/{id}` - Get specific todo
  - `PUT /api/todos/{id}` - Update todo
  - `DELETE /api/todos/{id}` - Delete todo
- `/api/shopping` - Shopping list endpoints
  - `GET /api/shopping/stores` - List stores, ordered by name ASC
  - `POST /api/shopping/stores` - Create a store. `409` on a case-insensitive name conflict
  - `PUT /api/shopping/stores/{id}` - Rename. `409` on conflict with a *different* store
  - `DELETE /api/shopping/stores/{id}` - Delete. **Reassigns the store's items to `store_id = NULL` first** — SQLite FKs aren't enforced, so an orphaned `store_id` would make those items vanish from every rendered group
  - `GET /api/shopping/items?include_hidden=false` - List items, ordered `completed ASC, created_at DESC`. Hides items completed before local midnight today unless `include_hidden=true`. Runs the once-per-local-day retention purge (see below)
  - `POST /api/shopping/items` - Create. `201`, or `200` with the existing row when an **open** item with the same trimmed, case-insensitive name already exists in the same store (a merely *completed* match creates a new item). Accepts `store` as a store **name** in place of `store_id` for scripted/voice clients; sending both is `422`, and an unrecognized name falls back to the catch-all rather than erroring or auto-creating a store. A `201` upserts `shopping_item_history`; a `200` does not
  - `PUT /api/shopping/items/{id}` - Partial update of `name`, `note`, `store_id`, `completed` (`note`/`store_id` use the `UNSET` sentinel). Completion stamping matches `PUT /api/todos/{id}` exactly. Does **not** touch history
  - `DELETE /api/shopping/items/{id}` - Delete an item; history is untouched
  - `GET /api/shopping/purchased?search=` - List items purchased **before** local midnight today — the exact complement of `GET /api/shopping/items`, including completed rows whose `completed_at` is `NULL` so nothing is invisible in both views. Ordered most-recent-first. Optional case-insensitive `search` across name and note. No sort/limit/offset: `PURCHASED_RETENTION_DAYS = 30` bounds the response. Runs the once-per-local-day retention purge
  - `GET /api/shopping/suggestions?q=&limit=8` - Autocomplete over `shopping_item_history`. Substring (wildcard) match with `%`/`_` escaped, ranked prefix-matches-first then by `times_added` DESC, `last_added_at` DESC, `name` ASC. Empty `q` returns the top entries by use count. `limit` defaults to 8 and is clamped to 25
  - `DELETE /api/shopping/suggestions/{id}` - Forget a suggestion (history is permanent, so a typo'd add would otherwise haunt autocomplete forever). Leaves `shopping_items` alone
- `/api/recurring-todos` - Recurring todo template CRUD endpoints
  - `GET /api/recurring-todos` - List all recurring todo templates
  - `POST /api/recurring-todos` - Create new recurring todo template
  - `GET /api/recurring-todos/{id}` - Get specific template
  - `PUT /api/recurring-todos/{id}` - Update template
  - `DELETE /api/recurring-todos/{id}` - Delete template
- `/api/dinner-plans` - Dinner plan CRUD endpoints
  - `GET /api/dinner-plans` - List all dinner plans
  - `POST /api/dinner-plans` - Create new dinner plan (multiple per date allowed)
  - `GET /api/dinner-plans/{id}` - Get specific plan
  - `GET /api/dinner-plans/date/{date}` - Get all plans for a date (YYYY-MM-DD)
  - `PUT /api/dinner-plans/{id}` - Update plan
  - `DELETE /api/dinner-plans/{id}` - Delete plan
- `/api/family` - Family member CRUD endpoints
  - `GET /api/family` - List all family members
  - `POST /api/family` - Create new family member
  - `GET /api/family/{id}` - Get specific family member
  - `PUT /api/family/{id}` - Update family member
  - `DELETE /api/family/{id}` - Delete family member
- `/api/followed-teams` - Sports watchlist subscriptions (teams and racing series)
  - `GET /api/followed-teams` - List every followed team, active or not, ordered by label
  - `POST /api/followed-teams` - Follow a team or racing series. `team_key` is `NULL` for a racing series, which is a league-level subscription with no team
  - `PUT /api/followed-teams/{id}` - Partial update; `team_key` and `radio_station` use the `UNSET` sentinel so `null` clears and omission leaves alone
  - `DELETE /api/followed-teams/{id}` - Unfollow. Announcement history in `sports_event_notices` is left alone
  - `POST /api/followed-teams/{id}/test` - Fetch the team's next 14 days and report what came back. An empty window returns `success: true` with an explanatory message, because a wrong `team_key` and an off-season team are indistinguishable from here
- `/api/settings` - Key-value settings endpoints
  - `GET /api/settings` - Get all settings
  - `PUT /api/settings` - Bulk upsert settings
- `/api/settings/ai` - Versioned AI settings endpoints (`agent_voice`, `family_context`)
  - `GET /api/settings/ai` - Get the currently active value and history ID for each field
  - `PUT /api/settings/ai/{field_name}` - Explicit save: inserts a new `ai_settings_history` snapshot (`created_at` = `last_used_at` = now, UTC) and points the field's `current_<field>_history_id` setting at it
  - `GET /api/settings/ai/{field_name}/history` - List all snapshots for a field, newest first (by `created_at` descending), plus the current history ID
  - `POST /api/settings/ai/{field_name}/rollback` - Make an existing snapshot active: bumps its `last_used_at` and repoints the setting — no new row inserted. Body: `{history_id}`
- `/api/settings/llm/config` - Versioned LLM configuration endpoints (coupled `provider` + `model` + `max_tokens` + `max_tokens_mode` snapshot)
  - `GET /api/settings/llm/config` - Get the currently active config and history ID. `max_tokens`/`max_tokens_mode` are `null` when no snapshot exists yet
  - `PUT /api/settings/llm/config` - Explicit save: inserts a new `llm_settings_history` snapshot, points `current_llm_config_history_id` at it, and syncs the plain `llm_provider` / model / max-tokens settings keys. Body: `{provider, model, max_tokens, max_tokens_mode}` (`max_tokens` defaults to `4000` and must be `> 0`; `max_tokens_mode` defaults to `"custom"`, forced to `"custom"` server-side for any provider other than `"anthropic"`). In `max_tokens_mode: "model_max"`, the submitted `max_tokens` is ignored and re-resolved from the Anthropic Models API — `400` with a `detail` message if the model can't be resolved (unrecognized name, missing/invalid API key), and no snapshot is written on that path
  - `GET /api/settings/llm/config/history` - List all snapshots, newest first, plus the current history ID
  - `POST /api/settings/llm/config/rollback` - Make an existing snapshot active: restores the whole config together (including the stored `max_tokens`, verbatim — never re-resolved), bumps `last_used_at`, repoints the setting, and syncs the plain settings keys — no new row inserted. Body: `{history_id}`
- `/api/settings/test-llm` - LLM connectivity test
  - `POST /api/settings/test-llm` - Test LLM provider connection (sends minimal 1-token request). Returns `{success, message}` or `{success, error}`. On Anthropic success, `message` appends the configured max-tokens value (e.g. `"Connected to claude-sonnet-4-6 (max tokens: 128000)"`) — the one place a freshly resolved "Model maximum" budget is confirmed to the operator, since the verify modal auto-closes on success and the field's helper text is the durable surface afterward
- `/api/settings/test-pushover` - Pushover connectivity test
  - `POST /api/settings/test-pushover` - Sends a real message to the first family member who has a user key. There is no token-only validation worth having: a well-formed token that belongs to another account looks identical to a correct one until a phone buzzes
- `/api/family/{id}/test-pushover` - Send a test push to one member's profile
- `/api/settings/test-weather` - Weather connectivity test
  - `POST /api/settings/test-weather` - Fetch the configured NWS forecast URL and confirm it returns DWML weather data (10-second timeout). Returns `{success, message}` or `{success, error}`.
- `/api/calendars` - Calendar feed CRUD endpoints
  - `GET /api/calendars` - List all calendar feeds
  - `POST /api/calendars` - Create new calendar feed
  - `GET /api/calendars/{id}` - Get specific calendar
  - `PUT /api/calendars/{id}` - Update calendar feed
  - `DELETE /api/calendars/{id}` - Delete calendar feed
  - `POST /api/calendars/{id}/test` - Test calendar feed connectivity. For ICS feeds, fetches the URL and validates calendar data. For CalDAV, connects and counts available calendars. For a **native** calendar there is nothing to connect to, so it reports how many events it holds — the button still means something in the same place. Returns `{success, message}` or `{success, error}`.

- `/api/preparedness` - Preparedness stock, locations, the go list and the refresh digest
  - `GET /api/preparedness/locations` - List locations, ordered `sort_order ASC, name ASC` — physical walking order, not alphabetical, because that is the order a go list is packed in
  - `POST|PUT|DELETE /api/preparedness/locations[/{id}]` - CRUD. `409` on a case-insensitive name conflict. **`DELETE` reassigns the location's items to `location_id = NULL` first** — SQLite FKs aren't enforced, so an orphan would vanish from every rendered group *and from the go list*, which is the failure that matters
  - `GET /api/preparedness/items` - List stock. Query: repeatable `location` (ids and/or `unassigned`), `status` (`ok|due|overdue`, derived from today so it is filtered in Python), `search` (name + notes), `sort` (`location` default, `name`, `refresh-soonest`, `newest`). Runs the daily refresh digest opportunistically, the same arrangement `list_events` uses
  - `POST /api/preparedness/items` - Create. On `interval` mode with no date, the first refresh is seeded as today + interval — a new item is assumed fresh today
  - `PUT /api/preparedness/items/{id}` - Partial update via the `UNSET` sentinel. The mode/field triangle is re-validated against the **merged** state, so a patch that only flips the mode still has to leave a coherent item behind
  - `POST /api/preparedness/items/{id}/refresh` - Mark refreshed. An `interval` item re-anchors on the *actual* refresh date; a spent `date` item becomes unscheduled rather than inventing a date Rally cannot know
  - `GET /api/preparedness/go-list` - Grouped JSON, honours `?location=`
  - `GET /api/preparedness/go-list/export?format=md|csv|pdf` - Download as an attachment
  - `POST /api/preparedness/digest/run?dry_run=true` - Run the digest now. Defaults to a dry run — the honest way to answer "is this working" without waiting until morning or burning the notice rows that suppress a real send
  - `GET /api/preparedness/digest/log` - Recent announcements, newest first
  - `POST /api/preparedness/review` - Run an LLM review of the inventory and store it. `400` with a message written for the person who pressed the button when the feature is off, the inventory is empty, no LLM is configured, or the model did not answer usefully
  - `GET /api/preparedness/review` - The last stored review, plus `stale` (the item count has changed since it ran). Reading **never** calls the model — a review costs real money and several seconds, so a page load must never spend either. `404` until one has been run

### Navigation
Top level is the four pages a family touches daily — **Dashboard, Tasks, Shopping, Calendar** — plus a single **Other** dropdown holding everything visited occasionally: Meal Planner, Previous Meals, Preparedness, Go List.

The split is by *frequency*, not by feature size. A meal plan is edited weekly and a go list is opened when something has gone wrong; neither earns a permanent slot next to Tasks. Collapsing the old Meal Planner dropdown into Other also keeps the top level at five items, so the three-column mobile nav from #144 still lands as two clean rows and nothing was pushed below the fold.

The outside-click handler is generic over `.nav-dropdown` rather than naming an id, so adding a second dropdown later needs no JS change.

All pages include a navigation bar allowing users to switch between Dashboard, Calendar, Todos, Shopping, Dinner Planner, and Settings. The nav markup is duplicated across every page template, so a nav change must be applied to each. On a phone the nav is a **three**-column grid: five items in two columns is three rows, which pushes the first row of content below the fold.

## Configuration

Rally supports two configuration approaches:

1. **Settings UI** (recommended) - Configure LLM provider, API keys, timezone, family members, and calendars through the `/settings` page. Settings are stored in the database.
2. **config.toml** (fallback) - File-based configuration for API keys, calendar URLs, and coordinates. DB settings take precedence when both exist.

Additional context files:
- `context.txt` - Family scheduling context (copy from `context.txt.example`)
- `agent_voice.txt` - AI agent tone/voice profile (copy from `agent_voice.txt.example`)

### Environment Modes

Rally detects environment via `RALLY_ENV` environment variable:

**Development (default):**
- Looks for config files in current directory
- Database at `./rally.db`

**Production:**
- Set via `ENV RALLY_ENV=production` in Dockerfile
- Looks for config in `/data/`
- Database at `/data/rally.db`

In Docker container, these should be mounted at `/data/`:
- `/data/config.toml` (optional if using Settings UI)
- `/data/context.txt`
- `/data/agent_voice.txt`

## Troubleshooting

### Port Already in Use

```bash
dev-status              # Check what's running
dev-stop                # Stop background processes
# Or kill manually:
lsof -ti:8000 | xargs kill
```

### Database Issues

```bash
rm rally.db            # Delete database (or data/rally.db in prod)
db-init                # Reinitialize
seed                   # Add sample data for development
```

The database is automatically created when the app starts. Migrations run automatically before initialization. Models include:
- `FamilyMember` - Family members with name, color, and timestamps
- `Calendar` - ICS calendar feeds linked to family members, with optional owner email
- `Setting` - Key-value settings store (LLM provider, API keys, timezone, etc.)
- `AISettingsHistory` - Versioned snapshots of `agent_voice` / `family_context` with field_name discriminator, value, created_at, and last_used_at; active snapshot per field referenced via `current_<field>_history_id` settings keys
- `LLMSettingsHistory` - Versioned snapshots of the coupled LLM provider + model configuration (JSON value `{"provider": ..., "model": ...}`, field_name always `llm_config`); active snapshot referenced via the `current_llm_config_history_id` settings key
- `StemConceptHistory` - Records used STEM "concept of the day" topics (title, field, used_on date) so the generator avoids repeating a specific topic within 60 days; one row per (title, used_on)
- `DashboardSnapshot` - Stores generated dashboard data with date, timestamp, JSON data, and active flag
- `Todo` - Task management with title, description, optional due_date (YYYY-MM-DD), assigned_to (family member), optional recurring_todo_id (link to recurring template), optional remind_days_before (reminder window), completion status, and timestamps
- `RecurringTodo` - Recurring todo templates with title, description, recurrence_type (daily/weekly/monthly), recurrence_day, assigned_to, has_due_date, remind_days_before, last_generated_date (tracks most recently generated instance's recurrence date), active flag, and timestamps
- `ShoppingStore` - User-defined store items are grouped under (Costco, Trader Joe's, …). Names are unique case-insensitively; there is no seeded "Anywhere" row — the catch-all is `store_id IS NULL`
- `ShoppingItem` - Shopping list item with name, optional note, optional store_id, completion status, completed_at, and timestamps. Uses the same `completed`/`completed_at` columns and semantics as `Todo`, so a completed item stays visible until local midnight; completed rows are deleted 30 days after completion
- `ShoppingItemHistory` - Permanent, deduplicated record of every name ever added (name_key = trimmed + casefolded), with the display casing, the most recently used store_id, a `times_added` counter and `last_added_at`. Powers autocomplete and deliberately survives the purchased-item purge
- `PrepLocation` - A place preparedness stock lives (Garage shelf, Truck, Bug-out bag). Names unique case-insensitively; the catch-all is `location_id IS NULL`, never a seeded row. `sort_order` is physical walking order — a go list is packed in the order you walk it, and alphabetical is the wrong order for that
- `PrepItem` - Preparedness stock with a free-text `quantity`, optional location and notes, and an optional refresh schedule (`refresh_mode` none/date/interval, `refresh_interval_months`, `next_refresh_date`, `remind_days_before`, `last_refreshed_on`). `next_refresh_date` is stored and indexed rather than derived — it is the only column the digest reads
- `PrepRefreshNotice` - Announce-once record keyed `f"{item_id}:{refresh_date}"`. Keying on the *pair* is what re-arms an item for free when its date moves; the unique index is the guarantee, not an optimisation
- `DinnerPlan` - Meal planning with date, plan text, attendee_ids (JSON array of family member IDs), cook_id (family member ID), and timestamps. Multiple plans per date are allowed.

### Dependency Issues

```bash
install-deps            # Reinstall dependencies
```

### Docker Issues

```bash
# Using devenv commands
down                    # Stop container
build                   # Rebuild image
up                      # Start again

# Or use Docker directly:
docker stop rally
docker rm rally
docker build -t rally .
docker run -d -p 8000:8000 -v $(pwd)/data:/data -v $(pwd)/output:/output --name rally rally
```

## Additional Resources

- [devenv Documentation](https://devenv.sh)
- [FastAPI Documentation](https://fastapi.tiangolo.com)
- [Ruff Documentation](https://docs.astral.sh/ruff/)
- [uv Documentation](https://docs.astral.sh/uv/)
- [SQLAlchemy Documentation](https://docs.sqlalchemy.org/)
