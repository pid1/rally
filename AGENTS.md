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
│   ├── models.py         # Database models (FamilyMember, Calendar, Setting, AISettingsHistory, LLMSettingsHistory, StemConceptHistory, DashboardSnapshot, Todo, RecurringTodo, ShoppingStore, ShoppingItem, ShoppingItemHistory, DinnerPlan)
│   ├── schemas.py        # Pydantic schemas
│   ├── cli.py            # CLI commands (seed, etc.)
│   ├── recurrence.py     # Recurring todo processing (template → instance generation, next-date calculation)
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
│       ├── todos.py         # Todo CRUD API
│       ├── shopping.py      # Shopping list, store, and autocomplete-suggestion API
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
  - `shopping_list_in_summary_enabled` ("true"/"false", default "false") folds open shopping items into the daily summary (Shopping List section)
  - `shopping_last_purge_date` (local YYYY-MM-DD) is internal bookkeeping written by the shopping retention purge — never surfaced in the UI
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
- `/shopping` - Shopping list page: an `Add Item` header button opening a dual-mode modal with history-backed autocomplete, store grouping, store filter chips derived from the items on the list, and a `Manage stores` button in the Store toolbar group
- `/shopping/purchased` - Read-only page of items purchased before today (local time), grouped by store; reachable only via the `View purchased items` link on `/shopping`, not from the nav bar
- `/dinner-planner` - Dinner planning page with date picker and plan management
- `/settings` - Settings, family member, calendar, and followed-team management page

### API Routes
- `/api/dashboard/regenerate` - Force dashboard regeneration and save new snapshot
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
All pages include a navigation bar allowing users to switch between Dashboard, Todos, Shopping, Dinner Planner, and Settings. The nav markup is duplicated across all six page templates, so a nav change must be applied to each.

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
