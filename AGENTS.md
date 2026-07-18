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

### 6. Working with Docker

For Docker operations, use non-blocking variants:

```bash
# Start container
up

# Check logs (non-blocking)
logs-tail

# Not logs (that follows and blocks)
```

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

```bash
# Ensure dependencies are installed
install-deps

# Run linting
lint

# Test summary generation
test-generate
```

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
│   ├── models.py         # Database models (FamilyMember, Calendar, Setting, AISettingsHistory, LLMSettingsHistory, StemConceptHistory, DashboardSnapshot, Todo, RecurringTodo, DinnerPlan)
│   ├── schemas.py        # Pydantic schemas
│   ├── cli.py            # CLI commands (seed, etc.)
│   ├── recurrence.py     # Recurring todo processing (template → instance generation, next-date calculation)
│   ├── generator/
│   │   ├── __init__.py
│   │   ├── generate.py   # Summary generation logic with calendar, todos, and dinner plans
│   │   └── __main__.py   # CLI entry point
│   ├── utils/
│   │   ├── __init__.py
│   │   └── timezone.py   # Timezone helpers (now_utc, today_utc, today_local, ensure_utc)
│   └── routers/
│       ├── __init__.py
│       ├── dashboard.py     # Dashboard routes
│       ├── todos.py         # Todo CRUD API
│       ├── recurring_todos.py # Recurring todo template CRUD API
│       ├── dinner_planner.py # Dinner plan CRUD API
│       ├── family.py        # Family member CRUD API
│       └── settings.py      # Settings and calendar management API
├── static/
│   └── styles.css           # Application stylesheet
├── templates/
│   ├── dashboard.html       # Generated dashboard template
│   ├── todo.html            # Todo management page
│   ├── todo_completed.html  # Read-only previously-completed tasks page
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
  - Connection verification on save: LLM, Weather, and Calendar settings show a verification modal with spinner, checkmark on success (auto-closes), or error message with Close button on failure
- ✅ AI settings snapshotting with version history and rollback
  - `agent_voice` and `family_context` each have their own Save button and Version History link on the settings page
  - Every explicit save inserts a versioned snapshot into `ai_settings_history` (`field_name` discriminator); the active snapshot per field is referenced by the `current_agent_voice_history_id` / `current_family_context_history_id` settings keys
  - Version History modal lists snapshots newest first with a `Current` badge and in-place expandable value previews; **Change Version** rolls the field back (bumps `last_used_at`, repoints the setting, no new row) and updates the field without a page reload
  - Fields roll back independently; all snapshots are retained indefinitely
- ✅ LLM settings snapshotting with version history and rollback
  - The LLM section's `Provider` and `Model` are versioned as a **single coupled snapshot** (`llm_config`) — saving the LLM form records one `llm_settings_history` row whose JSON `value` captures the provider and its active model together; the active snapshot is referenced by the `current_llm_config_history_id` settings key
  - The LLM section has one `Save` button and one `Version History` link; the shared Version History modal shows each snapshot's `Provider` / `Model` pair, and **Change Version** restores both together (select flips, provider fields toggle, model input updates — no page reload, no new row)
  - The plain `llm_provider` / `llm_local_model` / `llm_anthropic_model` settings keys remain the source of truth read by the generator; save and rollback keep them in sync
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
- ✅ STEM Concept of the Day - Optional family learning feature (toggle in Settings → Learning)
  - When `stem_concept_enabled` is "true", the generator adds a `stem_concept` object to the summary JSON (title, field, explanation, and age-appropriate `activities`)
  - The LLM tailors ideas to the ages described in FAMILY CONTEXT and keeps each idea super easy to fold into the day's existing plans
  - Rendered as a dedicated dashboard card; when disabled, the field is omitted from the schema and nothing renders
  - The LLM-as-judge eval exempts `stem_concept` from groundedness/completeness (it is intentionally generative)
  - Used concepts are recorded in the `stem_concept_history` table (deduplicated by title). Past titles are injected into the generation prompt as a "do not repeat" list so topics don't recur
- ✅ Dinner planner - Full CRUD API and UI
  - Multiple plans per date (e.g. half the family at a restaurant, half eating at home)
  - Optional attendees: select which family members are eating (defaults to everyone)
  - Optional cook assignment: who's preparing the meal
  - Next 7 days display with smart date formatting
  - LLM generator annotates plans with attendee/cook names for smarter reminders
  - Luxury UI matching Rally aesthetic
- ✅ Seed command for development data
- ✅ Generate command for real API data
- ✅ Scheduled generation at 4:00 AM in configured timezone (in Docker)
  - Reads timezone from DB settings or config.toml (default: UTC)
  - Uses date-based tracking to prevent duplicate runs
  - Robust against server timezone settings
- ✅ Environment mode detection (dev/production)
- ✅ Elegant grayscale design with serif typography
- ✅ Static CSS stylesheet (`static/styles.css`)
- ✅ uv-based dependency management

## Application Routes

### Page Routes
- `/` - Redirects to `/dashboard`
- `/dashboard` - Serves the generated daily summary from cached snapshot (shows error if missing)
- `/todo` - Todo management page with full CRUD interface
- `/todo/completed` - Read-only page of todos completed before today (local time); reachable only via the `View completed tasks` link on `/todo`, not from the nav bar
- `/dinner-planner` - Dinner planning page with date picker and plan management
- `/settings` - Settings, family member, and calendar management page

### API Routes
- `/api/dashboard/regenerate` - Force dashboard regeneration and save new snapshot
- `/api/todos` - Todo CRUD endpoints
  - `GET /api/todos` - List todos (incomplete, plus those completed since local midnight today)
  - `GET /api/todos/completed` - List todos completed **before** local midnight today — the exact complement of the above. Query params: `sort` (one of `completed-newest` (default), `completed-oldest`, `due-soonest`, `due-furthest`, `assignee`, `newest`, `oldest`), repeatable `assignee` (family member ID and/or `unassigned`; OR semantics, empty means all), `limit` (default 50, max 200), `offset`. Returns `{items, has_more}`. Sorting, filtering and paging are server-side; recurring processing is deliberately **not** run here.
  - `POST /api/todos` - Create new todo
  - `GET /api/todos/{id}` - Get specific todo
  - `PUT /api/todos/{id}` - Update todo
  - `DELETE /api/todos/{id}` - Delete todo
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
- `/api/settings` - Key-value settings endpoints
  - `GET /api/settings` - Get all settings
  - `PUT /api/settings` - Bulk upsert settings
- `/api/settings/ai` - Versioned AI settings endpoints (`agent_voice`, `family_context`)
  - `GET /api/settings/ai` - Get the currently active value and history ID for each field
  - `PUT /api/settings/ai/{field_name}` - Explicit save: inserts a new `ai_settings_history` snapshot (`created_at` = `last_used_at` = now, UTC) and points the field's `current_<field>_history_id` setting at it
  - `GET /api/settings/ai/{field_name}/history` - List all snapshots for a field, newest first (by `created_at` descending), plus the current history ID
  - `POST /api/settings/ai/{field_name}/rollback` - Make an existing snapshot active: bumps its `last_used_at` and repoints the setting — no new row inserted. Body: `{history_id}`
- `/api/settings/llm/config` - Versioned LLM configuration endpoints (coupled `provider` + `model` snapshot)
  - `GET /api/settings/llm/config` - Get the currently active provider + model and history ID
  - `PUT /api/settings/llm/config` - Explicit save: inserts a new `llm_settings_history` snapshot capturing the provider + model pair, points `current_llm_config_history_id` at it, and syncs the plain `llm_provider` / model settings keys. Body: `{provider, model}`
  - `GET /api/settings/llm/config/history` - List all snapshots, newest first, plus the current history ID
  - `POST /api/settings/llm/config/rollback` - Make an existing snapshot active: restores provider and model together, bumps `last_used_at`, repoints the setting, and syncs the plain settings keys — no new row inserted. Body: `{history_id}`
- `/api/settings/test-llm` - LLM connectivity test
  - `POST /api/settings/test-llm` - Test LLM provider connection (sends minimal 1-token request). Returns `{success, message}` or `{success, error}`.
- `/api/settings/test-weather` - Weather connectivity test
  - `POST /api/settings/test-weather` - Fetch the configured NWS forecast URL and confirm it returns DWML weather data (10-second timeout). Returns `{success, message}` or `{success, error}`.
- `/api/calendars` - Calendar feed CRUD endpoints
  - `GET /api/calendars` - List all calendar feeds
  - `POST /api/calendars` - Create new calendar feed
  - `GET /api/calendars/{id}` - Get specific calendar
  - `PUT /api/calendars/{id}` - Update calendar feed
  - `DELETE /api/calendars/{id}` - Delete calendar feed
  - `POST /api/calendars/{id}/test` - Test calendar feed connectivity. For ICS feeds, fetches the URL and validates calendar data. For CalDAV, connects and counts available calendars. Returns `{success, message}` or `{success, error}`.

### Navigation
All pages include a navigation bar allowing users to switch between Dashboard, Todos, Dinner Planner, and Settings.

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
