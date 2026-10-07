# AI Agent Instructions

This document provides guidance for AI coding assistants (Claude, Cursor, Copilot, etc.) working on this codebase.

It is the home for agent guidance specifically. Documentation written for people
lives in [docs/](docs/) and is linked from the README: [installation](docs/installation.md),
[configuration](docs/configuration.md), [development](docs/development.md),
[backups](docs/backup.md), [voice shortcuts](docs/voice-shortcuts.md) and the
[design system](docs/visual-design-system.md). When a change alters how Rally is
installed, configured or developed, update the relevant page there as well as this one.

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
| `demo` | Fresh seeded demo instance on port 8100, in its own `demo.db` | Yes |
| `screenshots` | Regenerate `docs/screenshots` from a seeded throwaway database | No |

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

✅ **Prefer the demo instance** for anything that has to run against a live server —
manual verification, screenshots, a recorded walkthrough. It seeds its own throwaway
database on its own port, so there is nothing to restore afterwards:

```bash
demo                # http://localhost:8100, backed by demo.db
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
- `022_add_home_location` - Seed an empty `home_location` settings row. Purely additive; `home_location()` treats a missing row and an empty one identically, so this exists to make the field visible on the settings page from the first load rather than to change behavior
- `023_add_prep_reviews` - Add the `prep_reviews` table (stored LLM reviews of the preparedness inventory). Purely additive
- `024_add_calendar_cache` - Add the `calendar_cache` table plus its unique index on `calendar_id` (one cache row per calendar; the index is what makes the sync's get-or-create safe), and seed `calendar_sync_interval_minutes` at 15. Purely additive; the table starts empty and the first sync fills it
- `025_add_caldav_sync_tokens` - Add `calendar_cache.sync_tokens` (one RFC 6578 sync token per server-side CalDAV calendar). Purely additive; NULL means "no baseline yet" and the next sync captures one
- `019_add_llm_max_tokens` - Backfill `max_tokens`/`max_tokens_mode` (`4000`/`"custom"`) into every `llm_settings_history` row's JSON value that lacks them (unparseable rows are skipped, not rewritten), and seed the `llm_anthropic_max_tokens`, `llm_local_max_tokens`, and `llm_anthropic_max_tokens_mode` settings keys when absent. The backfilled value matches prior behavior exactly, so this migration changes nothing observable by itself
- `026_add_shopping_sort_order` - Add `shopping_items.sort_order`, the per-store hand-arranged position behind drag-to-reorder. Backfilled per store group in the order the list already read (`completed ASC, created_at DESC, id ASC`), so no existing list visibly moves
- `027_add_member_notification_prefs` - Add the `member_notification_prefs` table plus its index and the unique index on `(family_member_id, kind)`. Purely additive and it writes **no rows**: an absent row means the kind's default, so upgrading changes nobody's behavior — shipping the feature is not the same as turning it on
- `028_add_recurring_todo_start_date` - Add `recurring_todos.start_date`, the day a series' first instance is due. Purely additive and writes no rows: `NULL` means "start from today", which is what every existing template already does
- `029_member_color_palette` - Move every `family_members.color` onto the closed palette (`rally.member_colors`). No schema change; the column already existed as unvalidated hex that no screen could set, so in practice every member sat on the old `#333333` default and the calendar drew four members as four identical near-black dots. Colors are handed out by `id ASC`, cycling, which is the same rule `POST /api/family` uses — whatever a hand-set color meant is deliberately not preserved, because reading intent out of an arbitrary hex is guesswork a five-entry palette cannot honor anyway. A member **already** on a palette color is left alone, which is both what makes it idempotent and what stops a container restart from overwriting a color somebody chose after the first run. The palette is duplicated in the migration rather than imported, per the self-contained rule above
- `030_calendar_sync_backoff` - Three fixes for one symptom: production reported "External calendars updated 272 hours ago" while every live feed had been fetched two minutes earlier. Deletes `calendar_cache` rows whose calendar no longer exists (`calendar_id` is a plain integer, not a foreign key, and the delete endpoint never removed the row, so it froze at the moment of the deletion — that frozen row is the 272 hours; the live feeds were fine, only the report was wrong); adds `calendar_cache.retry_after` for the rate-limit backoff; and moves `calendar_sync_interval_minutes` from 15 to 5, but **only** when it is still exactly the "15" migration 024 seeded, since no screen exposes the key and any other value was set by hand

- `031_add_device_preferences` - Add the `devices` and `member_preferences` tables plus their indexes, including the unique index on `(family_member_id, device_id, pref_key)`. Per-family-member *behavioral* settings, answered once per device — what a screen does when one person opens it, as opposed to migration 027, which decides who hears about what. The first setting is the calendar's landing view. `devices.id` is TEXT because the browser mints the token itself and has to keep using the same one, which a server-assigned id cannot do without a round trip before the first paint. Purely additive and it writes **no rows**: an absent row means the setting's default, which is always `auto` — Rally's own rule, the behavior that predates the table — so upgrading moves nobody's screen
- `032_add_event_override_calendar` - Add `event_overrides.calendar_id`. Which calendar an event sits on decides its color, its owner's name and — when nobody was named explicitly — its attendee list, which is what the member filter matches on. That was a property of the *series* alone, so "put this one Tuesday on Sam's calendar" had nowhere to live. NULL means **inherit from the series**, the rule every other nullable column in the table already follows, which is why this writes **no rows**: an existing override keeps NULL and resolves to exactly the calendar it renders on today. No foreign key, matching `event_attendees` and `member_notification_prefs` — a stray id is handled where it is read instead, and a deleted calendar degrades to "on the series' calendar" rather than to an occurrence with no color and no filter that matches it
- `034_add_packing_lists` - Add the `packing_lists`, `packing_list_bags`, `packing_list_items`, `packing_list_item_history`, `packing_list_schedules`, `packing_list_days`, `packing_list_day_items` and `packing_list_day_checks` tables plus their indexes. The unique indexes carry rules the API relies on: one template per name and one bag per name (both case-insensitive), one schedule per template, one copy of a template per day, one check per item per day, one reading of a template item per day (a partial index, since a day's own items have no `item_id`), and one history row per item name. `packing_lists.pack_days_before` is `CHECK >= 0`. Purely additive and writes **no rows**. **Returns early once migration 035 has run** (when `packing_list_templates` exists): it runs on every start, and would otherwise recreate the old tables empty and then fail on an index over a renamed column, which stops the container
- `035_packing_list_templates` - Names templates as templates and lets a day exist without one, in **one transaction** (SQLite DDL is transactional, so a failure part-way leaves the database as it was). Renames `packing_lists` → `packing_list_templates`, `packing_list_items` → `packing_list_template_items`, `packing_list_schedules` → `packing_list_template_schedules`; renames `packing_list_id` → `packing_list_template_id` and, on day items and day checks, `item_id` → `template_item_id` (`RENAME COLUMN`, SQLite 3.25+). **Rebuilds `packing_list_days`**, because SQLite cannot drop the `NOT NULL` 034 put on its template id, adding `name`, `description` (a templateless day's own) and `item_order` (JSON, a day's hand-arranged order). Drops every index named for an old table or column and recreates it under the models' name. Then **recounts `packing_list_item_history.times_added`** under its new meaning — past days a name was on a list, each past day read as `resolve_day` reads it — creating no row, and sets `packing_history_counted_through` to yesterday; that marker is how a second run knows the recount is done. Renames move no data
- `036_add_bag_owners_and_nesting` - Add `packing_list_bags.owner_id` and `packing_list_bags.parent_bag_id` (the household's default owner and the bag it goes in), and the `packing_list_template_bags`, `packing_list_day_bags` and `packing_list_day_bag_checks` tables plus their indexes. Replaces the bag name index: `ix_packing_list_bags_name_nocase` is dropped for `ix_packing_list_bags_name_owner_nocase` over `(name COLLATE NOCASE, IFNULL(owner_id, 0))`, since a bag is now unique by name and owner (every existing bag is ownerless and already uniquely named, so it cannot clash). The unique indexes carry the API's rules: one reading of a bag per template, one per day, and one grab check per bag per day. Purely additive and writes **no rows**: every bag has no owner and goes in nothing, and no item moves. What does change on screen is that each bag already in use appears as a ▣ bag row at the top of the `Everyone` group, since a bag with no owner is Everyone's

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

        if "your_new_column" in columns:
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
if "new_column" not in columns:
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
│   ├── models.py         # Database models (FamilyMember, Calendar, Event, EventAttendee, EventOverride, EventNotification, Setting, AISettingsHistory, LLMSettingsHistory, StemConceptHistory, DashboardSnapshot, Todo, RecurringTodo, ShoppingStore, ShoppingItem, ShoppingItemHistory, MemberNotificationPref, Device, MemberPreference, MealPlan, Note, PackingListTemplate, PackingListBag, PackingListTemplateItem, PackingListItemHistory, PackingListTemplateSchedule, PackingListDay, PackingListDayItem, PackingListDayCheck, PackingListTemplateBag, PackingListDayBag, PackingListDayBagCheck)
│   ├── schemas.py        # Pydantic schemas
│   ├── cli.py            # CLI commands (seed, etc.)
│   ├── recurrence.py     # Recurring todo processing (template → instance generation, next-date calculation)
│   ├── notifications.py  # Pushover transport, recipient resolution, due-reminder scan, add/change/remove notices
│   ├── notification_prefs.py # The KINDS catalog and the one place that decides who hears what
│   ├── member_prefs.py   # Per-member, per-device behavioral settings: the catalog, the device registry, and how a value resolves
│   ├── member_colors.py  # The closed family-member color palette and its constraints
│   ├── todo_notifications.py # The push that goes to a task's assignee when it lands on their list
│   ├── shopping_notifications.py # Batched "added to the shopping list" pushes, behind a settle window
│   ├── preparedness.py   # Refresh schedule arithmetic and the daily refresh digest
│   ├── packing_lists.py     # Packing list item order, a day's overlay and its own order (`resolve_day`), a list's bags as it reads them (`resolve_day_bags`, `resolve_template_bags`) and stale grab checks (`prune_bag_checks`), pack dates, bags, item history and the daily count of packed days (`count_packed_days`), the delete cascades and a deleted template's days made templateless (`delete_template`), schedule processing and the summary's PACKING LISTS text
│   ├── golist.py         # Go list grouping plus the md/csv/pdf renderers
│   ├── markdown.py       # The one markdown renderer: a note's bold/italic/lists, nothing else
│   ├── rich_text.py      # An event's Notes as paragraphs, line breaks and links: a plain-text path and an allowlist HTML converter
│   ├── templating.py     # The one Jinja environment every page renders through
│   ├── prep_review.py    # LLM review of the inventory: prompt, grounding rules, normalizing
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
│   │   ├── generate.py   # Summary generation logic with calendar, todos, and meal plans
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
│       ├── meal_planner.py  # Meal plan CRUD API, plus the paged previous-meals archive
│       ├── notes.py        # Daily Note CRUD API plus the searchable previous-notes page
│       ├── packing_lists.py   # Packing list templates and their items (/api/packing-list-templates), bags (/api/packing-list-bags), item suggestions (/api/packing-list-items), days with their checks, own changes and order (/api/packing-list-days) and template schedules (/api/packing-list-template-schedules)
│       ├── family.py        # Family member CRUD API
│       ├── devices.py       # Device registry and the per-device behavioral settings API
│       └── settings.py      # Settings and calendar management API
├── static/
│   ├── styles.css           # Application stylesheet (see the Design System section)
│   ├── modal.js             # Shared modal chassis: scroll fade, show/hide, and the page scroll lock behind an open modal
│   ├── sidebar.js           # The site menu: opening and closing the right-hand sidebar
│   ├── drag_reorder.js      # Pointer-events drag-to-reorder for grouped lists, optionally fenced to a `scope`
│   ├── list_group.js        # listGroupHtml(): the markup for one titled group of rows (.list-group), with optional lead rows (.list-group-lead)
│   ├── packing_lists.js        # What the packing list pages share: date, pack-day and progress wording, grouping by owner or bag, a day box and its entries, a JSON fetch helper
│   ├── autocomplete.js      # attachAutocomplete(): a text field's suggestion menu (Shopping item names; packing list item names and bags)
│   ├── recurrence_form.js   # The shared repeat controls (Daily/Weekly/Monthly/Custom): rule in, rule out, read-back line
│   ├── device_member.js     # This browser's device token, who it belongs to, and its stored answers
│   ├── archive_list.js      # The archive pages' shared search, results count and Load more
│   └── meal_edit_modal.js   # Shared meal add/edit modal behavior
├── templates/
│   ├── base.html            # The shared layout every page extends: <head>, header, menu button, sidebar
│   ├── dashboard.html       # Generated dashboard template
│   ├── calendar.html        # Month and agenda calendar views
│   ├── todo.html            # Todo management page
│   ├── todo_completed.html  # Read-only previously-completed tasks page
│   ├── shopping.html        # Shopping list page
│   ├── meal_planner.html    # Meal planner page
│   ├── meal_planner_previous.html # Previous meals: ratings, reviews, search and paging
│   ├── notes.html           # Notes page: one Daily Note per day, today onward
│   ├── notes_previous.html  # Read-only, searchable archive of past Daily Notes
│   ├── _note_edit_modal.html # Shared note add/edit modal
│   ├── packing_lists.html      # Packing Lists: day boxes coming up, the packing list templates with their items and schedules, and the Schedule, Edit Packing List, item and Manage Bags modals
│   ├── packing_lists_previous.html # Read-only, searchable archive of past days' packing lists
│   ├── _recurrence_fields.html # The repeat controls shared by Recurring Tasks and packing list schedules
│   ├── _start_from_fields.html # Start from (Blank list / Template + the template select), shared by Add Packing List and Add Packing List Template
│   └── settings.html        # Settings, family member, and calendar management page
├── config.toml.example   # Example configuration file
├── context.txt.example   # Example family context
├── agent_voice.txt.example # Example AI agent voice/tone profile
├── scripts/
│   └── capture_screenshots.py # Regenerates docs/screenshots from a seeded throwaway DB
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
│   ├── migrate_026_add_shopping_sort_order.py # Migration 026: add shopping_items.sort_order
│   ├── migrate_028_add_recurring_todo_start_date.py # Migration 028: add recurring_todos.start_date
│   ├── migrate_029_member_color_palette.py # Migration 029: move members onto the closed color palette
│   ├── migrate_018_add_sports_watchlist.py # Migration 018: add followed_teams, sports_event_notices tables
│   ├── migrate_019_add_llm_max_tokens.py # Migration 019: backfill per-provider LLM max tokens settings
│   ├── migrate_020_add_native_calendaring.py # Migration 020: event tables, Pushover columns, native calendars
│   ├── migrate_021_add_preparedness.py # Migration 021: preparedness stock, locations, refresh notices
│   ├── migrate_027_add_member_notification_prefs.py # Migration 027: per-member notification preferences
│   ├── migrate_031_add_device_preferences.py # Migration 031: device registry and per-device behavioral settings
│   ├── migrate_033_add_notes.py # Migration 033: add notes table (one Daily Note per day)
│   ├── migrate_034_add_packing_lists.py # Migration 034: packing list templates, bags, items, item history, schedules, days, a day's own changes and checks
│   ├── migrate_035_packing_list_templates.py # Migration 035: template tables and columns renamed, templateless days, a day's order, item history recounted
│   ├── migrate_036_add_bag_owners_and_nesting.py # Migration 036: a bag's owner and the bag it goes in, template and day readings of bags, grab checks
│   └── run_migrations.py              # Migration runner (executes all migrations in order)
├── tests/                # Pytest suite (in-memory DB per test)
│   └── visual/           # Design-system regression suite; drives real Chromium
├── docs/                 # Human documentation, linked from README.md
│   ├── installation.md   # Requirements, Docker deployment, upgrades, env vars
│   ├── configuration.md  # Settings UI, LLM, weather, calendars, notifications
│   ├── development.md    # Local setup, commands, tests, migrations, layout, the demo instance
│   ├── voice-shortcuts.md # Siri / Apple Shortcuts for the shopping list
│   ├── backup.md         # Offsite encrypted backup (Unraid + Cloudflare R2)
│   ├── visual-design-system.md # Design system audit, tokens, and enforcement
│   └── screenshots/      # README screenshots, regenerated by the `screenshots` script
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
  - Todo and meal plan date comparisons use the user's configured local timezone
- ✅ Configuration via Settings UI (stored in DB) with config.toml fallback
- ✅ **Notes** (`/notes`) — one Daily Note per day, shown on the dashboard
  - The dashboard card is the **one card not taken from the snapshot**: `get_dashboard` reads today's note live on every request, so a note written at breakfast appears on the next load instead of waiting for the 4 AM generation. Everything else on that page is cached
  - `{{note_section}}` renders or is omitted, the same shape `{{briefing_section}}` and `{{stem_section}}` use — no note, no card, and no placeholder text
  - The renderer is `MarkdownIt("zero", {"html": False, "breaks": True}).enable(["emphasis", "list", "newline"])` and every part of that is load-bearing. `"zero"` because `enable()` only ever *adds* rules: built on `"commonmark"` the same enable list still renders headings, links, images, code, blockquotes and rules. `breaks` + `newline` together because standard markdown collapses a single newline, and a family pressing Enter expects a line break — neither works without the other
  - Two independent controls on markup, not one: the API **rejects** a note containing a tag (nothing is ever stripped or silently altered), and the renderer escapes tags anyway. The rejection is tag-shaped rather than the `<` character, so `wear layers if temp < 40` and `the 5<6 rule` still save
  - `body_html` is inserted into the DOM as markup rather than through a page's `escapeHtml()`. It is one of only two values in Rally that render rather than escape (the other is an event's `description_html`, below), which is why the renderer config has a test per property
  - The day boundary is `utils.settings.today_local_str()` — the single helper for "today's local date as a `YYYY-MM-DD` string", used by Notes, the Meal Planner, the dashboard's note lookup and the shopping purge marker. The Meal Planner and the shopping purge each carried their own copy before this; consolidating them is why a date column can no longer mean two different days in two places. Use `today_local()` directly only when you need a `date` for arithmetic rather than a string to compare
  - Phone links are deliberately **not** applied to notes — see issue #230. `escapeHtmlWithPhoneLinks()` cannot simply be called on either the markdown or the rendered HTML
- ✅ **Packing Lists** (`/packing-lists`) — reusable packing lists, put on a day and checked off there (#249, #252, #254)
  - **Names say "template" when the thing belongs to a template** (#252): `packing_list_templates`, `packing_list_template_items`, `packing_list_template_schedules`, `packing_list_template_id`, a day item's `template_item_id`, `/api/packing-list-templates`, `/api/packing-list-template-schedules`, `source: "template"`, and on the page `#templates-container`, `data-template-row`, `openTemplateModal()`. Things that belong to a day or to the household keep theirs: `packing_list_days`, `packing_list_day_items`, `packing_list_day_checks`, `packing_list_bags`, `packing_list_item_history`, `/api/packing-list-days`, `/api/packing-list-bags`, `/api/packing-list-items`, and the pages `/packing-lists` and `/packing-lists/previous`, which show both kinds
  - A `PackingListTemplate` is the **template**: a name, an optional description and `pack_days_before` (a whole number, `0` = pack the day of), plus its items. An item has a name, an optional note, an optional **owner** (a family member; none reads as **Everyone**) and an optional **bag** (none reads as **No bag**), and one `sort_order` for the whole template
  - **Bags are one household list** (`packing_list_bags`), shared by every template, because the pool bag is the same pool bag on every list. A bag comes into being by being typed on an item and is renamed or deleted from **Manage bags**. **A bag is unique by its name (ignoring case) and its household owner**, so Emma and Jake can each have a `Backpack`; the index is over `IFNULL(owner_id, 0)` because SQLite treats NULLs as distinct and "no owner" must count as one owner. So a typed name can mean several bags: `bag_named(db, name, owner_id)` takes the item owner's bag of that name, else the ownerless one, else makes a new **ownerless** bag. The item form sends a bag it was opened with or that was picked from the menu **by id** while the field still reads that name (`chosenBag`), and only a typed name by name, so re-saving an item never quietly moves it to a same-named bag. Wherever two bags share a name the page adds the owner — `Backpack (Emma)` — through `bagLabelIn()`, and a bag that is unique reads as typed. By owner is the exception, through `bagFromGroup()`: a bag named from inside somebody's group (an item's bag, or the bag a bag goes in) carries its owner exactly when that owner, as the list reads it, is not the group's person — `in Suitcase (Dad)` under Emma, `Toiletries bag (Emma)` beside Jake's toothbrush — and reads as typed when it is theirs, so a person can see which of their things travel in a bag somebody else grabs. Deleting one moves what was in it to No bag — on templates, on days' own items and in history — rather than taking anything with it
  - **A bag has an owner and can go in another bag** (#262), to any depth: the household's defaults are `packing_list_bags.owner_id` (none reads as **Everyone**) and `parent_bag_id`, set from **Manage Bags**, which lists bags as they nest (each bag directly under the one it goes in, A–Z among bags in the same one; a missing or looping parent reads as the top level) and whose rows stay one line until `Edit` unfolds them in place (`Name`, `Owner`, `Goes in`, `Save`, `Delete`). A template or a day can read a bag differently — a **reading** (`packing_list_template_bags`, `packing_list_day_bags`), both fields copied whole, the rule a day's reading of an item follows. A day reads its own reading, else its template's, else the household's; a template reads its own, else the household's. Deleting a bag sends the bags inside it to the top level (on the bag and in every reading) and takes its readings and grab checks with it; deleting a member makes their bags Everyone's (`clear_member`)
  - **Which bags are on a list is derived, never stored**: every bag an item on it (as `resolve_day` reads it) is in, and every bag those go in. A bag with nothing in it is on no list. `resolve_day_bags` / `resolve_template_bags` return them **outermost first**, then the bags inside each, A–Z among bags in the same one. A bag is never put inside itself: the API refuses it (`422`) at the level being edited, and since readings at two levels can still combine into a loop no single write made, the walk up a chain stops at a repeat and a looped bag reads as going in nothing
  - **A day's bags are grabbed** (`packing_list_day_bag_checks`, the row is the check), counted on their own line under the items' (`1 of 3 bags grabbed`, `.packing-list-bag-progress`), and a day dims as done only when every item is packed **and** every bag grabbed. Check All / Uncheck All include bags; the Items filter keeps a bag row by whether it was grabbed. **A grab check does not outlive the bag's place on a day**: `_day_responses` deletes the check of a bag no longer on a day from today on (`prune_bag_checks`), so a bag that comes back comes back unchecked; a past day keeps its checks
  - **By owner, each person's group opens with their bags** (`listGroupHtml({ leadHtml })` → `.list-group-lead`, outside the `.list-container` so the drag never sees them), each a row with ▣ (`.title-indicator`, `A bag to grab`), its parent on the muted line (`in Suitcase`, or `in Suitcase (Dad)` when somebody else carries it), a `Grabbed <bag>` checkbox on a day and none on a template, no grip and no Edit. A person who owns a bag and nothing in it still gets a group. Group counts read `2 of 5 packed · 1 of 2 bags` on a day and `5 items · 2 bags` on a template. **By bag** shows no bag rows: its groups follow the list's nesting, a parent before the bags inside it, and a nested bag's header reads `Toiletries bag · in Suitcase`
  - **Change bags** (`btn--quiet`, in the actions under a list's items — beside `Remove from day` on a day entry, after `Add Item` on a template row — only when it has bags) opens a modal of that list's bags, each with `Owner` and `Goes in` (which leaves out the bag and the bags inside it), `Reset` when the list has its own reading, and `Remove from day` / `Remove from template`. Like every Rally form it has **Save and Cancel**, and nothing is written until Save: edits wait in `bagEdits`, Reset shows the level above's values in the form, and a bag marked for removal greys out with `Removed when you save.` and `Undo`. Save asks before any removal, then writes removals, then resets, then edits — so an edit to a bag that went in a removed one wins. **Removing a bag never removes an item**: after a confirmation naming how many items move, its items go to No bag — on a day through that day's readings, so they read `(changed)`; on a template on the template, reaching its days — and the bags that went in it go in nothing on that list. A template's reading reaches every day it is on unless the day has its own. Copies (`copy_from_template_id`) bring the source's readings along, and deleting a template copies its readings onto each of its days that has none (`_make_templateless`). Item history is untouched by all of it, and the daily summary does not mention bags
  - **The page groups every packing list one of two ways**, chosen with the `View` chips: **By owner** (family members in name order, then Everyone) or **By bag** (bags A–Z, then No bag). It is a lens, not a filter — nothing is hidden — and it is not remembered: every visit opens By owner. Only groups with something in them are drawn, and **every group is headed, even when it is the only one**. Each item carries the other dimension on a muted line under its name (its bag By owner, its owner By bag, `No bag` / `Everyone` when it has none), so every row reads the same way
  - **Every group folds** (#256), in Coming Up, on template rows and in the archive, so a family's list can be packed one person or one bag at a time: the whole header — name, rule and count — is the toggle (`listGroupHtml({ collapsible: true })`), and the count stays in it while folded. A packing list opens with **every group folded**, a lone group included. While you stay on the page each group stays as you left it — through `View less` / `View more`, the minute's refetch, Check All, a save and a reorder — kept by group key in `openDayGroups` / `openTemplateGroups` (`openDayGroups` on the archive), one set per container because a day and a template can share an id. Nothing opens a group but its header: an item added or moved into a folded group leaves it folded, and a drop on one goes to its end. **Switching View folds every group again** and leaves open packing lists open. Nothing is remembered across a reload
  - Both views are cuts of the **one** template order. Dragging a row onto another group is the owner or bag change: `POST /api/packing-list-templates/{id}/items/reorder` takes `{view, key, item_ids}`, gives every listed item that group's owner (`view: owner`) or bag (`view: bag`), and deals them back into the slots they already held between them, so items in other groups keep their places. A drag cannot leave its packing list (`drag_reorder.js`'s `scope`). New items go to the bottom: a packing list is entered and read top to bottom
  - **Item names are remembered** (`packing_list_item_history`), keyed by trimmed, casefolded name, with the owner and bag last typed. **Typing creates a row** — adding an item (to a template or to one day) or renaming one to a new name; a case-only rename is the same name — at `times_added` **0**, so it is suggested at once; nothing else ever creates one, so a suggestion forgotten with × stays forgotten until somebody types it again. History survives the item's deletion
  - **`times_added` is how many past days a name was on a packing list** (#252) — what was packed, not what was typed. `packing_lists.count_packed_days()` adds each day once its date is over, as `resolve_day` reads it (own additions in, removed items out, renamed items under the day's name, checked or not, two items of one name counting twice), to rows that exist only, and changes nothing else on the row. It is gated on the `packing_history_counted_through` settings row (the last date counted), so each day counts exactly once and a quiet week catches up in one pass; it runs beside `process_schedules()`, from `GET /api/packing-list-days` and from the generator's `load_packing_lists()`. Because only a day that is over counts, and a past day cannot be deleted, **nothing is ever undone**: taking a list off a day, moving a day, an owner or bag change, a check, a reorder and a template's deletion leave every count alone. Suggestions rank prefix matches first, then by this count, then by `last_added_at` (last typed) The item form's name field autocompletes from it through `attachAutocomplete` (add mode only), filling owner and bag only when nobody has chosen them; × forgets a suggestion. The bag field autocompletes from the bag list
  - **A `PackingListDay` puts a template on a date.** It reads the template **live**, so an edit to the template reaches every day it is on with nothing to sync, and a check on one day (`PackingListDayCheck`) cannot reach the template or any other day. On top of that, a day keeps **its own changes** in `packing_list_day_items`: items added to that day only, that day's edit of a template item, and that day's removal of one. An edit copies the item whole (name, note, owner, bag), so from then on a template change to that item no longer reaches that day. `packing_lists.resolve_day()` is the one place the overlay is laid over the template — the page, the counts and the summary all read a day through it. Deleting a template item drops every day's reading of it
  - A day marks what it changed: `(added)` / `(changed)` on the row (`.item-mark`), and an `N items changed` note beside the entry's Edit button (`changed_count`, counting edits, removals and additions)
  - **A day's own order** (#252): every Coming Up row has a grip (keyboard ↑/↓ too), and a day's items reorder **for that day only**. The order is stored on the day as `packing_list_days.item_order`, a JSON list of `"template:<id>"` / `"day:<id>"` keys (`NULL` is the default: the template's order, then the day's own) — not on its readings, because a reading copies the item whole and counts as a change. `POST /api/packing-list-days/{id}/items/reorder` takes `{view, key, items: [{source, id}]}`, the template reorder's contract: within a group it is order only (nothing marked changed); into another group it gives the item that owner or bag on this day only, a template item through its reading (so it reads `(changed)`), a day's own item directly. Template items and the day's own share one order. Anything not in the list (added later) reads after it, and the list is read leniently — a key for a gone or removed item is skipped, a duplicate counts once. **Reordering the template wins**: it clears `item_order` on that template's days from today on. `drag_reorder.js`'s `itemId(row)` is what lets a row's id carry its source, and group keys carry the day's id (`<day id>:<section key>`), the way template rows carry theirs
  - **`Add Packing List`** (#254, the header's `.page-header-actions`, where `Add Meal` is) is **the one way to put a packing list on a single day**; a repeating schedule is the Schedule modal's. Its modal (`#day-add-modal-overlay`) has `Start from`, two radios: `Blank list` (the default) or `Template` (hidden with no templates). `Template` shows a `Template` select below it (every template A–Z, starting at `Choose a template…`, required) and, under that, two radios: `Make a copy I can change` (the default) and `Keep in sync with the template`. That gives three forms of `POST /api/packing-list-days`:
    - **Blank list** — a **one-off**: a templateless day with its own name, label and lead time and nothing on it yet. Add Item opens for it after Save.
    - **Make a copy** — a one-off holding a copy of the template's items (`copy_from_template_id`): each item whole, in template order, unchecked, as the day's own, with no link back, and no history written. Name fills with the template's until somebody types one.
    - **Keep in sync** — the template itself on that day, the templated day the Schedule modal's old *One day* made: ⧉, a read-only Name (the template's), and the `409 {message, id}` rule, which closes the modal and opens the day it is already on.
    - Picking a template fills the lead time with its own. Kept in sync, leaving it there means the day follows the template (no `pack_days_before` sent); a copy and a blank list always send it, since a one-off has no template to follow. The `#day-pack-hint` line (moved here from the Schedule modal) reads the date and that field
    - After Save the `Due now` and `Items` filters come off and the new entry is revealed with `View more` open
    - **A one-off has no description**: a day's entry never shows one (`dayCardHtml()` renders none) and the summary does not use one, so it would only ever be seen inside Edit. The label is its note. Names are free — a one-off may share a name with a template or another one-off, on any date
  - **A templateless day** (#252) has `packing_list_template_id IS NULL` and its own `name`, `description` and `pack_days_before`; every item on it is one of its own. Deleting a template makes each of its days one (`packing_lists.delete_template`), past and upcoming, **before** the template goes: each template item, as that day had it, becomes a day item with its check, removed ones stay gone, its own items stay, its reading order becomes `sort_order`, its lead time becomes its own, its label stays, and `schedule_id` and `item_order` are cleared. So a template's deletion never erases what was packed or what is coming up. The responses carry `name` / `description` for both kinds (the template's, or the day's own). It reads as any day does, with what only makes sense with a template switched off: no ⧉, no `(added)` / `(changed)` marks, `changed_count` always 0, `Delete` in place of `Remove from day`, a scope note without the template sentence, `pack_days_before: null` a `422`, and no date clash (a `None` template id would compare `IS NULL` and call every other templateless day a clash). Template-item endpoints `404` on it. Its name is its own to change: Edit shows a `Name` field (`#day-edit-name-input`) in place of the read-only name, and `PUT` accepts `name` (a `422` on a templated day). A description it copied from a deleted template stays read-only
  - **⧉ marks a day that comes from a template** (`.title-indicator`, tooltip `From a packing list template`), after the label and before ↻, so a day reads `Swim at Nana's — Emma ⧉ ↻`
  - Days include past ones, which show the template as it is now (with their own changes). Freezing a past trip's list is deliberately out of scope
  - **When to pack** is `pack_days_before` days ahead: the template's number, which a day can override (`packing_list_days.pack_days_before`, `NULL` follows the template). `pack_date()` is the arithmetic; the entry reads `Pack the day of`, `Pack Wednesday` or `Pack Wednesday, Sep 30`
  - **Coming Up** is the Meal Planner's shape: one `.day-box.day-box--divided` per date, the day's packing lists stacked inside it as `.day-box-entry`s with a hairline between them, and the date stated once as the box's footer (`Today`, `Tomorrow`, or the long date). Each entry has its name — label ⧉ ↻, pack line, progress, the changed note and **Edit**, and a collapsed **View more** (`.disclosure`) holding the items, grouped by the current view, each with its own Edit and grip, then `Check All`, `Uncheck All`, `Add Item` and `Remove from day` (`Delete` on a templateless day). Check All and Uncheck All ask first, and do nothing when nothing would change. Which entries are open survives the refetch
  - **Edit Packing List** (one day's entry) shows the day's name and description read-only (its template's, or a templateless day's own) and edits that day's date, label and lead time. A label set there is the day's own (`label_edited`) and a schedule's relabel passes over it
  - **Packing: Due now** is a toolbar filter showing the days whose packing day has come (packing date ≤ today) and that have not passed, packed or not. `Clear Filters` resets it, and `Add Packing List` turns it off so the new day is not hidden
  - **Items: Not packed / Packed** (#258) narrows Coming Up to the items in that state; at most one chip is on, and clicking it again turns it off (the Rating chips' rule). It is `showItem` on `dayCardHtml()` / `dayBoxesHtml()`: groups are still cut from the whole list, so a group's count and the entry's progress line read the same with rows hidden, and a group with no row left is not drawn. A packing list with nothing matching is hidden, the way `Due now` hides a day, and both filters must pass. Folding is left alone — a group that drops out comes back open or folded as it was. A row that stops matching (ticked under `Not packed`, unticked under `Packed`) leaves at once without a re-render (`removeFilteredRow`), and focus moves to the next row's checkbox, else the next open group's, else the next drawn group's header, else the next packing list's View more. Adding an item leaves the filter on. `Clear Filters` and `Add Packing List` turn it off; it is not remembered and is not on the archive
  - **Packing List Templates** lists every template as a `.template-item` row (the dashed outline Recurring Tasks use): its name — schedule label ↻ (`Paused` / `Ended` when it is), the cadence, then **Schedule**, **Pause/Resume** (only when it repeats) and **Edit** (details only: name, description, lead time, delete). **Add Packing List Template** can **start from a copy of another template** (#260): its modal opens with `Start from` (`Blank list` by default, or `Template` with a required `Template` select, A–Z, starting at `Choose a template…`), the `_start_from_fields.html` partial `Add Packing List` also uses, with no copy-or-sync choice since that is a day's. It is for a new template only (Edit hides it) and is hidden when there is no template to copy; picking one fills nothing in, because the name, description and lead time are the new template's own, and each opening starts at `Blank list`. After Save the row opens as any new one does. The row's own View more holds its items, grouped by the view, with drag to reorder, an Edit per item and `Add Item`. `Save & Add Another` keeps the owner and bag a list is being entered with. **Enter in the item name or the bag is Save & Add Another** in add mode (#258) and Save in edit mode (`enterSavesItem`); Enter on a highlighted suggestion only accepts it (the listener runs after both `attachAutocomplete`s and checks `defaultPrevented`), and a name of only spaces is refused in the browser with the server's wording, `A name can't be empty.`, on the name field (focus moves there from the bag), cleared on the next keystroke and on every open. Notes is the item form's last field
  - **The Schedule modal** is one template's repeating schedule (`Schedule: <name>`, no picker); a single day is `Add Packing List`'s (#254). A `Repeats` checkbox and a folded **View schedule** holding the repeat controls, start and end dates, the read-back and a label for each day; `Update schedule`, which becomes `Remove schedule`, behind a confirmation, when Repeats is unchecked on a template that repeats. When it does not repeat and Repeats is off there is nothing to save, so only Cancel shows — never no button at all
  - **Schedules** (`PackingListTemplateSchedule`, at most **one per template**): the cadences Recurring Tasks offer — Daily (optionally weekdays only), Weekly, Monthly and Custom — with optional start and end dates (both inclusive), a label for each day, and Pause/Resume on the template's row only. The controls are `_recurrence_fields.html` + `RecurrenceForm`, the same partial and script `/todo` uses, and the read-back comes from `POST /api/recurring-todos/preview`, trimmed to the end date in the browser. The schedule's columns are named as on `RecurringTodo`, so `rally.recurrence` reads it as-is
  - `packing_lists.process_schedules()` creates ordinary `PackingListDay` rows (with `schedule_id`) for every occurrence from today through `SUMMARY_LOOKAHEAD_DAYS`, so every scheduled day the summary could mention exists before it is written. It runs from `GET /api/packing-list-days` and from the generator's `load_packing_lists()`, the `process_recurring_todos` arrangement. A date the template is already on by hand is kept, not duplicated
  - **`last_generated_date` is a high-water mark**: a date at or before it is never generated again, which is what keeps a removed day from coming back. A paused schedule resumes along its own cadence (every 2 weeks stays on its weeks) and skips the dates it missed rather than putting them in the archive
  - **A schedule has no hold over the days it made**, with one exception. Pausing it, changing its rule or dates, or deleting it leaves made days where they are; deleting it turns them into days added by hand (`schedule_id` cleared). A **new label** relabels the days it made from today on, except a day whose label was edited by hand. Deleting the template takes its schedule with it
  - **A day before today is the archive** (`/packing-lists/previous`): the same boxes, read-only — every item row `.is-read-only`, no controls — with the View chips, search over the template's name, a templateless day's own name and the day's label (an outer join, so a templateless day is in the archive too), and Load more, through `archive_list.js`. Read-only is enforced by the API as well: any write to a past day is a `403`, the Notes rule. The boundary is `today_local_str()`, so the two lists partition every day. There is no page for one template or one day: `/packing-lists/{id}` and `/packing-lists/days/{id}` are `404`s
  - SQLite does not enforce the references, so every delete cascades by hand: a template takes its items and its schedule, and makes its days templateless rather than taking them (`packing_lists.delete_template`); a day takes its checks and own changes; an item takes its checks and days' readings of it; a bag is cleared everywhere it is used; a family member's items become Everyone's (`packing_lists.clear_member`, called from `DELETE /api/family/{id}`). Bags and history belong to the household and outlive any template
  - A template goes on today or later (`422` otherwise) and once per day (`409` with `{message, id}`). An owner must be a family member that exists and a bag id a bag that exists (`422`)
  - On a day a checked row stays where it is, dimmed: packing goes in list order and a row that jumps to the bottom loses people's place. The progress line is an `aria-live` status; a tick re-renders only the counts so focus stays on the box. The page refetches every minute for a second person packing on another device, but never mid-save, mid-drag (`isDragging()`, the Shopping guard), while hidden, or while focus is inside Coming Up
  - **In the daily summary** (`packing_lists_in_summary_enabled`, default on): `packing_lists.summary_text()` lists every day's packing list — templated or not — from today through `SUMMARY_LOOKAHEAD_DAYS` (7) that still has something unchecked, read through `resolve_day` (so in the day's own order), with only the unchecked items, **grouped by owner** (family members in name order, then Everyone, every group headed) and each item's bag as `(in <bag>)`. Its status is `TODAY` on the day, `PACK TODAY` from the packing day until then (so a list packed days ahead keeps coming up while something is left on it), otherwise `UPCOMING (in N days)`. An item whose trimmed, casefolded name is open on the shopping list is marked `(already on the shopping list)` — the whole name, the same key shopping history dedupes on, because a fuzzy match that wrongly said "sunscreen is handled" is worse than none. The `PACKING LISTS` guideline asks for a packing reminder on the packing day, restocking and hard-to-get flags early enough to act, an `UPCOMING` packing list mentioned only for those flags, and nothing that is not in the section. Section and guideline are omitted when empty, and the text is eval ground truth
- ✅ **Native calendaring** (`/calendar`) — Rally owns events, and shows them
  - One normalized `Occurrence` shape (`src/rally/calendars/`) produced by the native, ICS and CalDAV adapters and merged in one place. `generate.fetch_calendars()` is now a thin caller
  - Fixed four defects the old dict-based read path made unavoidable: events sorted lexicographically by a 12-hour clock string (so 9 AM sorted after 1 PM), all-day events rendered as midnight appointments (a `date` also has `strftime`), a `(date, title)` dedupe key that dropped the second same-named event of a day, and a 7-day window measured in UTC dates
  - `events` / `event_attendees` / `event_overrides` tables. Times are stored **twice on purpose**: `start_utc`/`end_utc` are exclusive instants that order a day correctly, `start_date`/`end_date` are inclusive local dates that render correctly. Deriving either from the other at read time is where the all-day off-by-one lives
  - `tzid` is captured per event, so changing the family's timezone never re-times history
  - Recurrence is **RFC 5545 RRULE**, expanded through `recurring_ical_events` — the same expander the ICS path uses, so a 7:00 PM weekly event stays 7:00 PM across a DST transition and there is only one place for that bug to live. The UI offers the familiar Rally choices and compiles them to RRULE; nobody types one
  - Beyond the five fixed choices the form offers **`Custom…`** — an interval over days/weeks/months/**years**, several weekdays at once, and a monthly rule by date or by position (`the first Sunday`, `the last Friday`). `Every weekday` compiles to `FREQ=DAILY;BYDAY=MO,TU,WE,TH,FR` and **pins the interval to 1**, because RRULE has no way to say "every third weekday"; the field is disabled rather than silently ignored
  - The day-of-month select carries **`Last day`** (`BYMONTHDAY=-1`) as its own entry, which is not a synonym for `31`. RRULE **skips** a month that has no 31st rather than clamping to its end — the opposite of `recurrence.py`, which clamps for tasks. The divergence is deliberate: RRULE's semantics are what every external calendar consuming a Rally event applies, and clamping only inside Rally would make one rule mean two things. The form warns under the select when 29–31 is chosen
  - **`Ends`** bounds any repeating event — `Never`, `On date` (`UNTIL`), or `After N occurrences` (`COUNT`) — not only a custom one, because a bound is orthogonal to a cadence. `On date` writes end-of-day so the chosen day is included, matching `_until_value()`, which already got the all-day/timed value-type distinction right
  - The form **only sends `rrule` when the recurrence actually changed**: it compiles the controls and compares against the stored rule, omitting the key entirely when they match so `EventUpdate`'s `UNSET` leaves it alone. A rule richer than even the custom controls (`BYSETPOS`, `BYMONTH`) selects a hidden `Custom schedule` entry and is preserved read-only rather than narrowed — the defect #206 fixed, which editing a location was enough to trigger
  - Nonexistent local times (2:30 AM on spring-forward) shift forward by the gap; ambiguous ones (1:30 AM on fall-back) take the first instant. Both are policy, applied in `resolve_local` and tested
  - Per-occurrence edits via `event_overrides`, keyed on the **original** occurrence date rather than an index — an index shifts the moment an earlier occurrence is cancelled
  - Expansion is capped at 1,000 occurrences per event per query; hitting the cap logs and truncates rather than raising
  - Native calendars are rows in `calendars` with `cal_type='native'` and no URL, so per-member ownership, the Settings CRUD screen and the generator's join all apply unchanged. Every family member gets one; the router creates one on demand if none exists
  - **An event says whose calendar it goes on.** The Add/Edit Event modal's `Whose calendar` select lists every native calendar, grouped by owner under an `<optgroup>` and ordered by the same `/api/family` name order the filter chips and legend use. It opens **unselected** on Add and blocks Save until answered — landing on somebody's calendar by position is what this replaced — except when the family has exactly one native calendar, which is not a choice. Two guards enforce it, not one: `Save` submits the form so `required` covers it, but the three recurring-scope buttons call `saveEvent()` straight from a click handler and never submit, so the check lives in `saveEvent()` as well
  - **Picking a calendar checks its owner as an attendee, and never unchecks anyone.** Ownership and attendance stay separate questions — "on Maya's calendar, Dad driving" has to be sayable — but the owner is almost always involved. Moving an event checks the new owner and leaves the previous one, because quietly dropping a recipient is how somebody stops being told about their own event
  - **All three edit scopes can move an event**, and `event_overrides.calendar_id` is what makes `Only this event` a real option rather than a greyed-out one. `_apply_override` re-resolves `calendar_label`, `member`, `member_color` and `attendees` together for a moved occurrence — resolve one without the others and it displays a new owner while still answering to the old one's filter, since `attendees` falls back to the owner when nobody was named. `CalendarOwner` carries the four; `_restamp_owner_fields` in `cache.py` keeps the same four in step for external feeds, for the same reason
  - **Every write path that accepts `calendar_id` goes through `_require_native_calendar()`.** There were three and only one was guarded. An event pointed at an external or missing calendar does not merely render oddly — `sources.py` expands only `cal_type='native'` calendars, so the row survives, still loads in the edit form, and appears on no grid and under no filter, behind a successful save
- ✅ **Event Notes formatting** (`rally.rich_text`) — the `Notes` row of the event detail view reads as paragraphs, line breaks and links instead of one escaped blob
  - `OccurrenceResponse.description_html` travels beside the raw `description`, the same pairing `NoteResponse` uses for `body` / `body_html`. It is computed in the response schema, on the way out, so a description already in `calendar_cache` is formatted without a resync, and there is no migration. The edit form still loads the raw `description`
  - **`source` decides the path, not a guess about the content.** `native` is always **plain text**: a blank line is a paragraph, a single newline is `<br>`, bare `http(s)` URLs are links, and a tag somebody typed into Rally's own field is shown as typed. Every other source (`ics`, `caldav_google`, `caldav_apple`) takes the **HTML** path when the text holds something tag-shaped (`<` + optional `/` + a letter — the same rule as `schemas._MARKUP_RE`, so `temp < 40` is text) and the plain path otherwise
  - The HTML path is an **allowlist rebuilt from `html.parser` events**, not a sanitizer that strips what it dislikes: the output contains only `p`, `br`, `ul`, `ol`, `li`, `strong`, `em` and `a`, every one written by the converter itself. All attributes are dropped except a link's `href`, which must be `http`, `https`, `mailto` or `tel` (checked after stripping the whitespace and control characters a browser ignores inside a URL); anything else is its label with no anchor. `script`, `style` and `title` vanish with their contents, other tags vanish and keep their text, and entities are decoded then escaped again. Output is balanced by construction, which `tests/test_rich_text.py` checks against 200 random tag soups. No new dependency
  - Links are `.inline-link` and open in a new tab (`target="_blank" rel="noopener noreferrer"`). Bare URLs in text that is not already inside a link are linked on the HTML path too, and text inside an unusable link is never linked a second time
  - **Phone numbers stay links.** `escapeHtmlWithPhoneLinks()` takes raw text and cannot run on markup, so `static/phone_links.js` gained `linkPhoneNumbers(element)`, which walks the text nodes of an element already in the page. Both entry points share `phoneMatches()`, so what counts as a number is decided in one place (`tests/visual/test_phone_links.py` runs the same tables through both). A number inside an existing link is left alone, and one split across two elements is not found
  - Only the detail view shows a description. The **daily-summary prompt still receives the raw string** (`generate.py`); cleaning it changes model input and the LLM-as-judge eval, so it is a separate change
- ✅ **Pushover reminders to an event's attendees** (Settings → Notifications)
  - `pushover_app_token` identifies the install; `family_members.pushover_user_key` identifies a person. A member without a key is never notified, which is the default rather than an error
  - Recipients are the event's **attendees**, never "everyone" — notifying four phones for one child's appointment is how a notification feature gets muted
  - Three paths: a reminder lead time (`events.notify_minutes_before`), an explicit `Notify attendees`, and an automatic notice when an event is **added, changed or removed**. All go through one `_deliver` — the only place a push is attempted, so reporting, the send-once row and "a failure is data, not an exception" each exist once
  - A change notice is **one body sent to everybody**, not personalized. The push title is `Calendar Addition|Modification|Deletion: <event title>` (see `CHANGE_LABELS`) and the body is `When:` / `Where:` / `Attendees:`, each line omitted when there is nothing to say. `When` is the occurrence's own local date plus the **time range** in the install's configured zone, named — `2026-08-14 · 5:30 to 6:30 PM CDT`. The meridiem is stated once when both ends share it and twice when they do not (`11:30 AM to 1:00 PM`); a zero-length event collapses to one time; a timed occurrence crossing midnight dates both ends, because `2026-08-14 – 2026-08-16 · 5:30 PM to 9:00 AM` does not say which end is which. `Attendees` lists **every** attendee in the order they were added, including those with no Pushover key, because the line describes the event rather than the send
  - A recurring series adds `This event repeats …` — a sentence, not a fourth `Label: value` line, because it qualifies the whole notice. `describe_recurrence()` (in `calendars/describe.py`, since it serves the modal and the detail view as well as this notice) reads the RRULE back in the vocabulary the event form offers — `weekly on Friday`, `every 2 weeks on Tuesday and Thursday`, `monthly on the first Sunday`, `monthly on the last day`, `every weekday`, plus `, until Nov 14, 2026` or `, 12 times` for a bounded series. Anything richer degrades to `on a custom schedule` rather than a confidently wrong phrase. `every weekday` exists because describing that rule as `daily` was wrong rather than merely vague
  - Which occurrence a notice describes follows the scope: `scope=this` names that occurrence, everything else names the next one that has not finished yet, falling back to the most recent for a series entirely in the past
  - Notices are **planned then sent** (`plan_change_notice` → `send_change_notice`). Deletion is why: a delete destroys the occurrence, the attendee list and sometimes the event row, so the text and recipients are resolved *before* the write and delivered *after* it — announcing beforehand would risk naming a deletion that then failed. `notify_event_change` is the one-call form for additions and edits
  - A whole-event delete plans with `record=False`: its `event_notifications` rows are cascaded away with the event, so writing another would orphan a row against a deleted id
  - Neither half can raise, and both run **after** the commit: changing the calendar is what the user asked for, and a Pushover outage must not fail the write
  - Lead time is subtracted from the **resolved occurrence**, not the series start; anything else is an hour wrong for half the year
  - `event_notifications` mirrors `sports_event_notices`: its unique index on `(event_id, occurrence_date, family_member_id, kind)` *is* the send-once guarantee. A **failed** send is recorded but does not consume the slot, so a brief outage cannot permanently eat a reminder
  - A window missed by more than `REMINDER_GRACE_MINUTES` (15) is **dropped, not replayed** — a push at 4:05 for a 2:30 reminder misinforms rather than reminds
  - `check_due_reminders` runs from a minute loop in `entrypoint.sh` *and* opportunistically from `GET /api/events`, gated to once a minute. The container loop only exists under Docker, so without the second hook a `dev` instance would never send one — same reasoning as the shopping retention purge
  - Failures are logged and recorded, never raised: a push cannot fail an API request or a summary
- ✅ **Pushover on task assignment** (Settings → Tasks)
  - One recipient: the task's `assigned_to` member, and nobody else. `assigned_to IS NULL` means "Everyone", which is precisely the audience an event notification refuses to buzz, so an unassigned task announces nothing
  - Fired from the write paths only — `POST /api/todos` and `PUT /api/todos/{id}` — after the commit, and it cannot raise. Same discipline as the calendar change notices: the task is what the caller asked for and a Pushover outage must not fail creating it
  - A hand-over, not an edit: `notify_assignment(db, todo, previous_assignee=…)` sends only when `assigned_to` changed to somebody new. Renaming a task somebody has had for a week is silent, and so is clearing the assignee or completing the task
  - An **already-complete** task is never announced, including when a done task is reassigned — that is bookkeeping, not work
  - Title is `New Task: <title>`; the body is the due date then the description, each line omitted when there is nothing to say. Wording is the shortest unambiguous form — `Due today`, `Due tomorrow`, `Due Saturday` inside a week, `Due Sep 30` beyond it (with the year when it is not this one), and `Overdue since Aug 14` for a date already past, because handing somebody a late task must not read as an ordinary one. A task with neither falls back to `It's on your list.`: Pushover rejects an empty message
  - **No send-once row.** Unlike a reminder, this fires from a single write rather than a repeating scan, so there is nothing to deduplicate — and a task genuinely bounced between two people is two hand-overs, both worth announcing
  - Recurring instances are **not** announced. `process_recurring_todos()` runs opportunistically inside `GET /api/todos`, so a push there would fire from a read and would buzz the owner of a daily chore every morning about a standing arrangement
- ✅ Calendar integration (Google Calendar, iCloud) - filters to next 7 days, deduplicates, handles declined events
- ✅ Weather integration (configurable National Weather Service forecast URL — DWML feed)
- ✅ Configurable LLM provider - Anthropic Claude or any OpenAI-compatible API
- ✅ Idempotent database migrations - Run automatically on container startup
- ✅ SQLite database with FamilyMember, Calendar, Setting, DashboardSnapshot, Todo, RecurringTodo, and MealPlan models
- ✅ Dashboard caching via DashboardSnapshot table (no auto-generation on page load)
- ✅ Dashboard route (`/dashboard`) - renders from cached snapshot only
- ✅ Navigation via a right-hand sidebar — docked open on wide screens, behind a hamburger menu otherwise — on a shared Jinja base layout (`templates/base.html`); see **Navigation**
- ✅ Family members - Full CRUD API and UI
  - Color-coded identities for each family member, from a **closed palette** of five (`src/rally/member_colors.py`, `--member-*` in the stylesheet)
    - Rally is grayscale and e-ink first, so a member's color is the only color-carrying channel in the app. The palette is a fixed set rather than free hex because one arbitrary value can defeat the guarantee the set exists for: that any two members are distinguishable on any display Rally runs on
    - Three constraints, in priority order — **monochrome e-ink separability** (adjacent entries >=1.24x apart in relative luminance, so five members stay five distinct grays with color removed entirely), **WCAG 1.4.11 non-text contrast** (3:1 on both `--surface` and `--surface-sunken`; a dot is a UI component, not text), and **color e-ink gamut** (hues >=53 degrees apart, near primaries a Spectra/Kaleido panel reproduces). Five is what those constraints allow, not a preference: six compress the spacing to 1.20x and eight to 1.15x
    - Validated on the way **in** (`FamilyMemberCreate` / `FamilyMemberUpdate` reject anything else with a 422) and never on the way out. `FamilyMemberResponse` reports whatever is stored, because a response schema that rejected a legacy row would take down `/api/family` — including the Settings page that is the only way to repair it
    - `POST /api/family` assigns the first unused entry when the caller says nothing about color, so a family never has to think about it to get distinct dots. Beyond five members the palette cycles
    - Set from Settings as a row of five swatches; there is no free-form color input, and `tests/test_member_colors.py` fails if the stylesheet and the module ever disagree about a value
  - Used for calendar ownership and todo assignment
- ✅ **Per-member, per-device behavioral settings** (`src/rally/member_prefs.py`, Settings → **Personal Defaults**) — what a screen *does* for one person, the sibling of `notification_prefs`, which decides who *hears* what
  - A **catalog**, not a column per idea: a setting is an entry in `member_prefs.CATALOG` with its choices and its default, and the second one costs nothing but that entry. The first is `calendar_default_view`
  - **The key is the pair — a person on a device.** The first cut split the answer two ways, phone and computer, keyed on 768px. That is a guess about a device dressed up as a fact about one: the kitchen wall tablet and the desk laptop are both "a computer" by width and want opposite things, and two people's phones are one phone to a media query. The device is what somebody actually configures, so the device is what the answer hangs on
  - **`auto` is every setting's default and every setting offers it.** It names Rally's own rule rather than a value — for the calendar, the width rule that page has always had — so a device nobody has configured behaves exactly as it did before this table existed. It is a real option in the dropdown, which is what makes "let Rally pick" something a person can say rather than only something they get by staying silent. A concrete default would move an unconfigured screen; `member_prefs` raises at import if one is ever introduced
  - **Only the calendar knows what `auto` means.** `const NARROW` lives in `templates/calendar.html` and nowhere else, and resolving `auto` there means doing nothing — `mode` and `range` already hold what the width gave them. `tests/test_pages.py` pins that it is consulted exactly once
  - The stored value for the calendar **is** the pair the toolbar draws (`<view>:<range>`, e.g. `agenda:week`), so there is no lookup table on the server and none in the browser either. `calendar:rolling30` and `agenda:rolling3` are deliberately not offered: a grid cannot draw a rolling thirty days and `Next 3 days` is a time grid, so each would be a landing view the page then silently changes
  - **Devices register themselves** (`static/device_member.js`): a token minted with `crypto.randomUUID` on first use and kept in `localStorage`, plus the member binding, which is the other half of the key. Neither ever leaves the browser except as the id in a URL. There is no enrollment step — the household is already behind one front door. A browser with unusable storage gets a fresh token per load, so it simply never accumulates settings
  - **`devices` exists so the feature is visible and reversible.** A browser that clears its site data comes back as a new device and leaves its answers behind; without a list showing what is remembered, and a `Forget` that drops a device and its rows together, the table grows entries nothing can reach and nobody can see. The label is a coarse UA guess ("iPhone", "Mac") that exists to be corrected — a column of raw tokens answers no question anybody has
  - An **absent row means the default**, the same discipline `member_notification_prefs` follows. A row is written even when the value is `auto`, because it records that somebody chose — which is what lets `auto` change meaning later without moving a family that picked it on purpose
- ✅ Calendar management - Full CRUD API and UI
  - Add ICS calendar feeds linked to family members
  - Optional owner email for accurate declined-event detection
- ✅ Settings management - Key-value store with web UI
  - Configure LLM provider, API keys, timezone
  - DB settings take precedence over config.toml
  - `stem_concept_enabled` ("true"/"false") toggles the STEM Concept of the Day feature (Learning section)
  - `shopping_list_in_summary_enabled` ("true"/"false", default "false") folds open shopping items into the daily summary (Shopping List section)
  - `shopping_last_purge_date` (local YYYY-MM-DD) is internal bookkeeping written by the shopping retention purge — never surfaced in the UI
  - `packing_history_counted_through` (local YYYY-MM-DD) is internal bookkeeping: the last date `count_packed_days` has counted into packing list item history. Set by migration 035 or the first pass, never surfaced in the UI
  - `home_location` (free text, e.g. "Highland Village, TX") is the family's home, sent to the LLM as its own `HOME:` block alongside `FAMILY CONTEXT`. First-party rather than prose inside the context so other views can read it structurally. An unset value omits the whole block — a labeled section with nothing after it invites the model to invent one
  - `calendar_sync_interval_minutes` (default "5") is how stale a cached external calendar may get before the background sync refreshes it. A calendar being rate-limited is exempt while its `calendar_cache.retry_after` is in the future — that column, not this key, schedules its next attempt
  - `packing_lists_in_summary_enabled` ("true"/"false", default **"true"**) folds packing lists on a day in the next week that still have something unpacked into the daily summary (Packing Lists section). Defaults on for the same reason as `prep_overdue_in_summary_enabled`: the section omits itself unless something is coming up
  - `prep_overdue_in_summary_enabled` ("true"/"false", default **"true"**) folds preparedness stock that is past its refresh date into the daily summary. Defaults on, unlike the shopping and sports toggles: those add a standing block that costs tokens every day, whereas this one is normally empty and omits itself entirely, so it only costs anything on the days it matters
  - `prep_review_enabled` ("true"/"false", default **"false"**) adds the `Review` button to `/preparedness`. Off by default because it is a real LLM call and is only useful once a reasonable amount of stock has been entered
  - `prep_notify_enabled` ("true"/"false", default "true"), `prep_notify_time` (local HH:MM, default "08:00") and `prep_default_remind_days` (default "14") drive the preparedness refresh digest. `prep_last_digest_date` is internal bookkeeping written by the once-per-local-day gate — never surfaced in the UI, exactly like `shopping_last_purge_date`
  - `todo_notify_enabled` ("true"/"false", default "true") pushes a task to its assignee when it is created or handed to somebody new (Tasks section). Defaults on: a family that has entered Pushover keys wants the pushes, and the row only exists once somebody turns them off
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
  - **Daily can skip weekends** (`Schedule on weekdays only` under Daily), stored as `custom_rule = {"weekdays_only": true}` beside `recurrence_type = "daily"` so no column is added. It is a different rule from Custom's checkbox of the same name, which moves a weekend date to the following Monday; the two are deliberately one label, and each page words the Custom one's hover text for what it schedules (`weekdays_only_title`). Read back as `Every weekday`, `Every weekend day` or `Every 2 weeks on weekdays`
  - Configurable recurrence day (day-of-week for weekly, day-of-month for monthly)
  - Optional due date and reminder window per template
  - Assign to family members
  - Auto-generates concrete todo instances when due and no open instance exists
  - **Optional start date** — the day the first instance is due, with the cadence counted from there ("replace the smoke detector battery every 12 months, starting 1 January 2027", set up in 2026 in one pass). One substitution does all of it: `get_first_recurrence_date()` resolves from `max(today, start_date)` rather than from today, and `_first_custom()` already means "the first date matching this rule on or after the day I hand you". For daily and Custom "every N days" the start date is the **anchor** — the first task is on it and the interval counts from it; for the rules that name a position on the calendar (a weekday, a day of the month, the first Sunday) it is a **floor**, because that named position is the point of the rule. `get_next_recurrence_date()`, `_next_custom()` and `_resolve_reference_date()` are untouched: once the first instance exists, `last_generated_date` is the anchor and the start date has done its job
  - `process_recurring_todos()` skips a template whose start date is later than today, before any other work. A series that begins in 2027 puts nothing on the task list in 2026 — that is the difference between a start date and a far-off due date
  - Built-in **Monthly** rolls the first occurrence forward when this month's day has already passed, instead of handing back a task due three weeks ago. This changes newly created templates only: anything already generating has a `last_generated_date` and never reaches that path
  - The modal reads the rule back as dates through `POST /api/recurring-todos/preview` (*First task: Friday, January 1, 2027 — then January 1, 2028*), and the Recurring list row appends `· starts Jan 1, 2027` while the start date is still in the future, so a series with nothing on the task list is still visibly scheduled
  - Recurrence processing runs during dashboard generation
  - Activate/deactivate templates without deleting
- ✅ Shopping list (`/shopping`) - Store-grouped family shopping list, a peer of Tasks and the Meal Planner
  - `Add Item` is the header button, in the same position and styling as `Add Task` and `Add Meal`, and opens a dual-mode modal (add/edit) following `todo.html` exactly. `Save` closes it — burst entry was tried inline and as a stay-open modal, and both times cost more in consistency than they bought in keystrokes. The store select reads `Anywhere` on every open, ignoring the active chips, matching `openAddModal()` on `/todo`
  - Autocomplete is the shared `attachAutocomplete` (`static/autocomplete.js`), a custom dropdown (not a native `datalist`) reading `GET /api/shopping/suggestions` server-side, with a ~150 ms debounce and a request-sequence guard against out-of-order replies. ↑/↓ move, Enter accepts, Esc dismisses, `×` forgets a suggestion. Accepting fills the store **only when the user hasn't already chosen one**. `note` is deliberately not restored. The menu lives inside `.modal-body`, which is a scroll box, so it is capped at 240px with its own scroll rather than spilling down the page. Wired in add mode only — editing is a correction, not a lookup
  - Completed items stay on the list until **local midnight**, exactly like tasks, via the shared `today_start_utc()` helper in `utils/settings.py`. There is no countdown and no client-side expiry sweep — the page just refetches periodically
  - Purchased items live on their own page (`/shopping/purchased`), reached by a `.view-switch` link exactly as `/todo/completed` is. A checkbox that changes what the list means underneath you is a mode; the archive is different data with a different lifetime. Backed by `GET /api/shopping/purchased` — search, the store filter and paging are all server-side, since the page only holds what it has loaded; the store chips come from the response's `stores`
  - Store filter chips describe **what is on the list**, not what stores exist: a store earns a chip when it has an item in the current fetch, or when it is currently selected. That second clause prevents a filter that cannot be seen or undone. There is no `All` chip — no selection is the unfiltered state, matching the assignee chips on `/todo`
  - `Manage stores` sits in the Store toolbar group beside the chips it manages, styled `.filter-clear`. Opening it from the page rather than from the item modal designs the stacked-overlay problem out instead of mitigating it
  - **Two separate memories, deliberately.** `shopping_items` is a 30-day rolling record whose completed rows are *deleted*; `shopping_item_history` is permanent and deduplicated with a use counter. The purge is safe precisely because autocomplete reads history, not items — trimming one never damages the other
  - The purge runs opportunistically from the items listing (the `process_recurring_todos` precedent), gated on the `shopping_last_purge_date` settings row so it executes at most once per local day. The 4 AM container job would be the obvious home but lives in `entrypoint.sh` and only runs under Docker, so a `dev`-served instance would never purge
  - Open items optionally feed the AI daily summary via `shopping_list_in_summary_enabled` (Settings → Shopping List, default off). Completed items never reach the LLM
  - **Drag to reorder**, via a grip on every open row. A drop on another store's group *is* the store change — one gesture, one request (`POST /api/shopping/items/reorder`), so a move can never half-apply. Order is per-store (`shopping_items.sort_order`), which is the point: a list is arranged in the order the aisles are walked
  - The drag is pointer events (`static/drag_reorder.js`), **not** HTML5 drag-and-drop, because `dragstart` never fires from a touch and Rally is used on a phone and a wall tablet at least as much as on a desktop. The dragged row is not a stand-in placeholder — the real element moves through the DOM while a copy follows the pointer, so the live DOM is always exactly what a release would save, and committing is a matter of reading the destination list back
  - The grip is a real `<button>`, so ↑/↓ reorder it from the keyboard and focus is restored to the moved row after the list re-renders. Moving stores by keyboard is the edit form's Store field, which already does it. Each move is announced through an `aria-live` region
  - Purchased rows have no grip. `sort_order` is neutralised for them in `_list_ordering()` so a position held from before they were ticked off cannot float them back up; they sit below the arranged rows, newest-first
  - The 60-second refetch is skipped mid-drag — re-rendering the list would delete the row out of the user's hand
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
- ✅ Meal planner - Full CRUD API and UI
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
  - **Owner-derived fields are reconciled on every path that skips re-expansion** (`_restamp_owner_fields`): the 304, the CalDAV sync token, the identical body hash, and the failure branch. `member`, `member_color`, `calendar_label` and `attendees` are stamped on by Rally rather than parsed from the feed, and all four of those paths guard on the *feed's* content — which does not move when a member is renamed, recolored, or a calendar relabeled, so without this they stay frozen until the feed happens to change, which for a quiet calendar is never. `attendees` is the one with teeth: the member filter matches on it, so a renamed member's imported events dropped out of their own filter entirely rather than merely showing a stale name. Overwriting it is lossless because `component_to_occurrence` sets it to `(member,)` and never reads the feed's own ATTENDEE properties; a cached row is external-only and pre-merge, so its attendee list is only ever its own calendar's owner. The reconciliation compares before it reassigns, so a warm cache is not rewritten every pass, and it deliberately does **not** touch `changed_at` or the `synced`/`unchanged`/`failed` counts — those report the feed, and this is not a feed change. `owner_display_label` in `occurrence.py` is shared by the live fetch, the cached fetch and the reconciliation, because a label built three ways would drift silently
  - The generator still fetches **live** (`use_cache` defaults to False): a briefing built from a cache that had been failing silently for a day would be wrong, and it is the one caller with nobody watching
- ✅ Overdue preparedness stock in the daily summary (toggle in Settings → Preparedness, default on)
  - `load_overdue_prep_items()` lists only genuinely **overdue** items, never "due soon". The Pushover digest already announces the approach once per refresh date; the briefing is the standing nag for the ones nobody dealt with, and repeating every upcoming refresh would be noise
  - The section and its guideline are both omitted when nothing is overdue — an empty labeled section invites the model to comment on it anyway
  - The guideline states the section is the complete list of overdue items, so the model cannot pad it, and tells it to keep the mention to one line of housekeeping rather than the theme of the day
  - **Read-only with respect to `prep_refresh_notices`.** A summary run must never disturb the digest's announce-once guarantee; there is a test asserting the notice count is unchanged
- ✅ Preparedness AI review (`prep_review.py`, toggle in Settings → Preparedness) - Asks the configured LLM what the kit is missing
  - Sees the **entire** inventory, family members, the active family context (which is where ages come from — there is no age column) and the home location from #147
  - **Groundedness is the whole design.** Asking what is *absent* is the prompt shape most likely to produce invention, so the model is told the inventory is the only evidence of what the family owns, warned not to flag a category the list already covers under different words, and required to put anything it does not know — unstated ages, unset home — into an `assumptions` field rather than guessing. Absent inputs are passed as an explicit `(not recorded)` so there is no silent hole to fill
  - Responses are normalized before storage: unknown priorities coerce to `medium`, gaps without an item are dropped, and the list is capped — a review is read by someone deciding what to buy, so a half-parsed field is worse than a missing one
  - Snapshotted into `prep_reviews` and read back on view, following `DashboardSnapshot`. The response carries `stale` so a review of 38 items is visibly stale once you hold 44
- ✅ Seed command for development data
- ✅ Generate command for real API data
- ✅ Scheduled generation at 4:00 AM in configured timezone (in Docker)
  - Reads timezone from DB settings or config.toml (default: UTC)
  - Uses date-based tracking to prevent duplicate runs
  - Robust against server timezone settings
- ✅ Environment mode detection (dev/production)
- ✅ Elegant grayscale design with serif typography
- ✅ Static CSS stylesheet (`static/styles.css`), organized in token/base/primitive/component layers
- ✅ Design system: tokens, layout primitives, one button family, one modal chassis (`docs/visual-design-system.md`, `/styleguide`)
- ✅ Design-system regression tests: static stylesheet lint plus a Playwright suite (`tests/test_stylesheet.py`, `tests/visual/`)
- ✅ uv-based dependency management

## Design System

`static/styles.css` is organized in layers — tokens, base, layout primitives,
components, page-specific, responsive — in that order. A rule's position tells
you its blast radius. Full rationale and the audit that produced it:
`docs/visual-design-system.md`. Live reference: `/styleguide`.

When touching the UI:

- **Use tokens, never literals.** `var(--space-4)`, `var(--text-sm)`,
  `var(--ink-muted)`. A raw px or hex in a component is a bug unless it is a
  1px hairline. `tests/test_stylesheet.py` fails the build otherwise.
- **Member color is the one color on a page.** `--member-*` is a closed
  five-entry palette (`src/rally/member_colors.py`); never add a sixth or hand
  a component a raw member color. The spacing between entries is a luminance
  ladder, not an aesthetic choice — it is what keeps members apart on a
  monochrome e-ink panel.
- **Block content that arrived as markup is `.rich-text`; a link inside a line of somebody else's text is `.inline-link` (or `.phone-link`).** `.rich-text` owns the spacing between paragraphs and lists and inherits its size and color, so it is right in a list row and a detail row alike; `.editable-item-multiline` is only the row's typography and goes on the same element. `.link-quiet` is a page-header control with a 44px box, not something that can sit in a sentence.
- **Text is `--ink`, `--ink-muted` or `--ink-subtle`.** `--rule` and
  `--rule-subtle` are hairlines and fail WCAG AA as text.
- **Buttons are `.btn` plus `--secondary`, `--quiet`, `--sm`.** Do not add a
  new button class; Save must look the same everywhere it appears.
- **Never write `outline: none`.** One `:focus-visible` rule in the base layer
  covers everything.
- **Every page extends `templates/base.html`.** Never write a `<head>`, a
  header or a nav into a page template; the layout owns them.
- **Page structure is `.page.stack` > `.page-header` + `.toolbar` + content.**
  Spacing between blocks comes from `.stack`, not from component margins.
- **Toolbars own their reset slot.** `Clear Filters` goes in `.toolbar-reset`
  as the toolbar's last child, never inside a `.toolbar-group`.
- **Modals are `.modal-content > h3 + .modal-scroll > .modal-body`**, and the
  page loads `/static/modal.js`.
- **A grouped list is `.list-group > .list-group-header +
  .list-container > .editable-item[data-id]`, written by `listGroupHtml()`
  in `/static/list_group.js`** rather than by hand; a reorderable one also
  loads `/static/drag_reorder.js`. The group wrapper is what makes a whole
  group — heading included, and an empty one — a drop target.
  `listGroupHtml({ collapsible: true, open })` writes the same group as a
  `<details class="list-group list-group--collapsible">` whose `<summary>` is
  the header, so the whole header row folds it, with the `.disclosure` caret;
  every class and `data-group` stay put. A folded group is still a drop
  target, and a row dropped on it goes to its end. A packing list's groups
  use it; Shopping and Purchased do not. Folded groups stack close: a folded
  header drops its bottom margin, and a folded group right after another sits
  `--space-2` below it; an open group, and the group after one, keep the full
  heading spacing.
  `listGroupHtml({ leadHtml })` adds rows that head a group without being part
  of its order (a person's bags) as `.list-group-lead`, between the header and
  the `.list-container`. They must not go in the list itself:
  `drag_reorder.js` treats every row in a list it cannot drag as a trailing
  block (`firstTrailingChild`) and keeps dragged rows above it.
- **Shared list components are named for what they are, not for the page that
  introduced them.** A row's checkbox hit area is `.item-checkbox`, a titled
  group is `.list-group`, and a row in a "Manage …" modal is `.manage-row`
  (once `.todo-checkbox`, `.shopping-group` and `.store-manage-row`), and a
  recurring template's ↻ is `.recurring-icon` (once `.todo-recurring-icon`). A
  small muted glyph after a name, saying where the row comes from with the
  words in its `title`, is `.title-indicator` (once `.recurring-indicator`):
  ↻ a task made from a template or a schedule's day, ⧉ a day whose packing
  list comes from a template, ▣ a bag to grab. A row in a "Manage …" modal
  unfolded to edit more than its name is `.manage-row--editing` (fields
  stacked, `.manage-row-actions` under them; `.manage-row-text` is a folded
  row's name and muted line), used by Manage Bags and Change bags. A component
  a second page wants is renamed before it is reused.
- **Part of a row kept folded away is `.disclosure`** — a native `<details>`
  whose summary swaps `.disclosure-more` / `.disclosure-less`. A packing list's
  day entry and its template row each hold their items in one.
- **A day's plans are one `.day-box`**: everything planned for a date stacks
  inside it as `.day-box-entry`s, and the date is stated once, as the box's
  `.date-label` footer (once `.meal-day` / `.meal-day-meal`). The Meal
  Planner's meals and the Packing Lists page's packing lists. `.day-box--divided`
  adds a hairline between entries; only Packing Lists uses it so far.
- **A template is `.template-item`**, the dashed outline a recurring task's
  row has always had (once `.recurring-template`), on Recurring Tasks and
  Packing List Templates.
- **A row that cannot be acted on is `.is-read-only`**: an archived day's
  items. **A note on a row about where it came from is `.item-mark`**:
  `(added)` / `(changed)` on a day's packing list.
- **A text field's suggestion menu is `attachAutocomplete()`** in
  `/static/autocomplete.js`: `.autocomplete-wrap > input + .autocomplete-menu`,
  a suggestion's second line is `.autocomplete-detail` (once
  `.autocomplete-store`). The page supplies the source and what accepting does;
  the debounce, the stale-reply guard, the keys and the × are the component's.
- **An open modal locks the page behind it**: `modal.js` sets `html.modal-open`
  while any overlay is shown, so a wheel or a finger in the modal never
  scrolls the page underneath. Show and hide overlays with
  `showModalOverlay()` / `hideModalOverlay()`, never by toggling the class
  yourself, or the lock is left on.
- **The repeat controls are one component**: `templates/_recurrence_fields.html`
  plus `static/recurrence_form.js` (`RecurrenceForm`), on `/todo` and
  `/packing-lists`. Set `task_options` on the include for the task-only
  control, and `weekdays_only_title` for the hover text the page gives
  Custom's weekdays-only checkbox. Never copy them into a page.
- **Start from is one component**: `templates/_start_from_fields.html`, on
  Add Packing List and Add Packing List Template. Set `start_from_prefix` on the
  include (it names the ids and the radio group, since both modals share a
  page) and `sync_options` for the copy-or-keep-in-sync choice only a day has.
  The page fills the select with `templateOptionsHtml()` each time the modal
  opens.
- **Hit areas are `var(--target-min)`**, which is 44px on coarse pointers and
  narrow viewports. The calendar is where this bites, and the resolution is the
  same for both of its grids: hold the column at 44px and let the grid scroll
  **inside its own container** rather than hiding it or pushing the body
  sideways. The month grid carries `min-width: 20rem`; the time grid computes
  `min-width: calc(gutter + cols × --target-min)`. Where an element cannot be a
  44px target it must not be a target at all — the phone month cell's event
  rows are `pointer-events: none` and the day number is the way in.

Run `uv run pytest tests/test_stylesheet.py` for the static checks, and the
visual suite (above) before shipping a layout change.

## Application Routes

### Page Routes
- `/` - Redirects to `/dashboard`
- `/dashboard` - Serves the generated daily summary from cached snapshot (shows error if missing)
- `/calendar` - **Two orthogonal controls**: `View` picks the renderer — `Calendar` or `Agenda` — and `Range` picks the slice of time. They used to be one dropdown, which is why `Day` and `Week` both rendered agenda lists: there was no way to say "a week, drawn as a calendar". **The week starts Sunday.** Prev/Today/Next move by the selected *range* in both modes
  - `Calendar` + `Day`/`Week` is a **time grid** — hour gutter, one column per day, blocks positioned and sized by start and duration. Day, `Next 3 days` and Week are the same component with `--timegrid-cols` set to 1, 3 or 7. Overlapping events cluster transitively and each takes `1/n` of the column, which is what makes a collision visible rather than something you derive from two timestamps. All-day events sit in a band pinned above the scrolling hours, spanning every column they cover. A timed event crossing midnight draws twice — to the bottom of the first day, from the top of the next. **The grid opens with the previous hour at the top**, hour-aligned and clamped at midnight, on every render and in every displayed range — where to open is a question about when you are asking, not about what the day contains. The rule this replaced opened an hour ahead of the day's first event, capped at 7 AM, so every visit started around 6 AM and the afternoon was ten hours of empty morning away on a device you are usually standing at
  - Block times are parsed back off the server-rendered `time_label` / `end_time_label` rather than derived from the UTC instant, because those labels are already in the family's configured zone. Doing the arithmetic in the browser would put blocks where the labels beside them disagree, for anybody traveling
  - `Calendar` + `Month` is the existing month grid. `Agenda` + any range is the existing day-grouped list. `Next 30 days` is the old rolling agenda window, kept as a first-class range and offered in `Agenda` only — a grid of 30 arbitrary days starting on a Wednesday is not a calendar. Switching to `Calendar` while on it falls back to `Month`. `Next 3 days` is its mirror: today and the next two days as a three-column time grid, offered in `Calendar` only, stepping 3 days per Prev/Next and following the date at midnight as `Day` does. Switching to `Agenda` while on it falls back to `Day`. Each rolling range is drawn by one View (`VIEW_ONLY_RANGES`), and the option for the other View is detached from the select rather than hidden
  - **Month's window depends on the mode**: the grid draws the 42-day block containing the month because it has to square itself off; the list uses the calendar month, having no reason to carry the leading and trailing days
  - **Nothing is persisted, but the landing view is configurable per person per device.** Width is not consulted again after load, and changing `View` or `Range` while you are there is still never remembered — which calendar you want *next* is a function of why you opened it, and a remembered `Day` view is exactly wrong for the Sunday planning session. Where you *start* is a different question, and Settings → Personal Defaults answers it for the person using **this device** (`calendar_default_view`, stored as the `<view>:<range>` pair the toolbar draws). The default is `auto`, which is the width rule this page has always had — `const NARROW` lives here and nowhere else, because `auto` names Rally's rule and only the page holding the rule can resolve it. A device nobody has claimed, a person who has never chosen, and an `auto` answer all land in the same place: `Calendar` + `Month` on a computer, `Calendar` + `Day` on a phone
  - Every mode and range is reachable at every width. The month grid renders on a phone with **event text stacked in the cell**, not just a date and dots: a heat map says Tuesday is busy without saying with what, so reading any day cost a tap and a round trip. Cell height comes from content, so an empty week stays one row tall. **No dots in the cell**: they were the phone's entire answer to "who is busy today" when the cell could not carry text, and once every row carries a name they repeat it — worst in a crowded cell, where they sat above three rows *and* a `+N more`. `.calendar-day-dots` is gone from both the markup and the stylesheet; `.calendar-dot` still carries member color in the desktop rows, the agenda, the all-day band and the legend. Those rows are **real targets at `--target-min`**, not inert text: the old rule (one target per cell) was right when the cell was locked to one target-height, but the cell grows to fit now, so the rows clear the floor honestly and tapping an event opens it. `pointer-events: none` was tried and is wrong — it blocks only the pointer, leaving a `<button>` a keyboard can reach and a finger cannot
  - **The grid is drawn on a five-minute lattice.** Starts and heights both snap to five minutes (`snapToFive`), which is the resolution a calendar is read at. `paintGeometry()` decides all of it in one place — start, body height, tab height — and both `layoutColumns` and the renderer read it, so the columns are packed against the same rectangle that reaches the screen
  - **A body is never shorter than 30 minutes**, and proportional above that. **`--timegrid-hour` is 5.5rem so that 30 minutes is exactly 44px** — the shortest body and the hit-area floor are pinned to each other, which is the whole reason for that value. A day is 2112px and scrolls inside the grid. At the old 3.5rem a half-hour block was 28px and had to be inflated to stay tappable, which is what made it read as three quarters of an hour
  - **An event under 30 minutes gets a tab** (`.is-short`): a `--space-2`-wide strip down the left at the event's own rounded length, carrying the member color, with the body beside it at the 30-minute minimum. Duration reads off the tab; the words and the tap target live in the body. Tab + body is exactly the column width, and both sit inside one button, so it stays a single 44px control. At or above 30 minutes there is nothing to reconcile and no tab is drawn — the color goes back to the left border
  - **Rounding is applied before the short test**, so a 28-minute event rounds to 30 and is *not* short. A tab is never shorter than 5 minutes, so a one-minute event still shows one (7.3px)
  - **Packing is on the painted rectangle, not the true span.** A 15-minute event at 3:45 paints to 4:15, so it and a 4:00 event sit side by side rather than the first covering the second's title. Isolated events keep the full width — only genuinely colliding paint is split
  - **The grids are full-bleed on a phone.** `--page-pad-x` lives on `body`, so `.calendar-timegrid` and `.calendar-grid-scroll` cancel it with negative side margins and run edge to edge, handing the whole 32px to the day columns: a week column goes 45px → **50px** and a month cell 51px → **56px**. Only the grids break out — the range title and the legend stay on the text column, where a heading is expected to start. The grid's side borders are dropped there, since a rule on the bezel reads as a cropped box rather than an edge-to-edge one
  - **Hit areas hold in both axes.** Height is covered by the 30-minute body minimum above. Width is the same rule applied sideways: `min-width: calc(gutter + cols × --target-min)` plus `overflow-x: auto` on the grid, so no column ever goes under 44px. The phone gutter shrinks to 2.5rem, which is what puts a 390px phone at 45px per column; at 320px the grid scrolls inside itself rather than pushing the body sideways. `.calendar-timegrid-col` clips at the day's edge, so a 30-minute body on an 11:50 PM event cannot paint out of the grid
  - **The now line is one line across the whole grid**, drawn in *every* displayed range rather than only the one containing today. The grid's value is a shared vertical axis, so the element naming your place on it has to cross every column to be read against any of them; drawn inside today's column it was a mark on Thursday rather than a time, and on a week three months out there was no time reference at all. It lives on `.calendar-timegrid-body` and clears the gutter with `left: var(--timegrid-gutter)` — it cannot live in a column, which is `overflow: hidden` so a late-night block does not paint past the bottom of the day. Identical on every date: which column is today's is already said by `is-today`, structurally. Hidden in print, where a week is a document about a date range rather than about the moment it came off the printer
  - **`nowMinutes()` is the only place the grid asks the time.** The line and the opening scroll have to agree to the minute and two `new Date()` calls either side of a render can straddle one. It is also the seam `tests/visual/test_design_system.py` stubs, which is how "4:30 PM opens at 3 PM" is assertable at 3 AM
  - **A five-minute tick keeps now moving.** Rally's primary device is a tablet on a wall that is never reloaded, and a line frozen at page load is worse than no line because it is confidently wrong. The common tick moves one style property — re-rendering every five minutes would throw away an open modal and reset the scroll under whoever is reading. **On a date change it re-renders instead**, because the line, `is-today` and the Day title all go stale at once; `Day` follows the date over (it names exactly one, and a tablet nobody touches has to be showing today at breakfast) while `Week` and `Month` name a span and keep the one they are on
  - **A Day title carries a `· Today` marker** when its date is today, in *both* modes — Day names exactly one date, so the title is where that is said, and the two Day views must not disagree about saying it. Calendar also marks today structurally (`is-today` on the day label, plus the now line); the words are in addition to that, not instead. Week and Month name a span, and today is not a span, so they never carry it
  - **Agenda + Day drops its day heading**, which repeated the title an inch below; the marker moved up rather than being lost. Every other agenda range keeps its headings, because there they are what separates one day from the next — work a single title cannot do
  - **The range name leads the content**, as an `h3.calendar-range-title` emitted by `bodyHtml()` for every mode and range — not a caption in `.page-header-meta`, which is gone from this page. A caption three controls above the grid was too quiet for the words that say what you are looking at, it moved when you changed View, and `.page-header-meta` is `display: none` in print, so a printed calendar carried no date at all. It wraps rather than `nowrap`: "WEDNESDAY, SEPTEMBER 30, 2026" is about 400px uppercase against 358px of page on a 390px phone
  - On a phone `View` and `Range` share one toolbar row via `.toolbar-group--pair`. Two stacked selectors would be two more rows and push the first event below the fold — the regression #139 fixed
  - `Add Event` opens a dual-mode modal carrying attendees, recurrence and a reminder lead time; editing an occurrence of a series keeps the same `Save` / `Cancel` / `Delete` as any other event, and only after `Save` or `Delete` does a small modal over it (`#event-scope-modal-overlay`, #263) ask which events it reaches: `This occurrence`, `This and following` or `All events` as radios, **none chosen in advance**, with the confirm button (`Save` or `Delete`) disabled until one is. The form is validated before it opens, so nobody picks a scope and is then sent back to fix a field. Its `Cancel` returns to the edit modal with the edits intact; a failed request closes it and leaves the edit modal open. An event that does not repeat never sees it, and deleting one still asks `confirm()`. External events render read-only. The recurrence controls read themselves back as a sentence (*Repeats every 2 weeks on Tuesday and Thursday, until Dec 18, 2026*) fetched from `POST /api/events/describe-recurrence`, so the vocabulary lives in one place rather than being reimplemented in JavaScript
  - Clicking an occurrence opens a read-only detail view that names the cadence in a `Repeats` row, **whichever protocol the calendar arrived over**. Native events and ICS feeds carry their rule in the document. A CalDAV server expands remotely, so an instance has had its `RRULE` resolved away — `_master_rrules` asks the server a second time with `expand=False` and joins the answers on UID, which every instance of a series shares. That second request is only made when the window actually holds a series (an expanded instance carries `RECURRENCE-ID`, a one-off does not), so a calendar with nothing repeating in it costs nothing extra, and an unchanged calendar costs nothing at all because the rule is already in `calendar_cache`. It is best-effort: a server that refuses an unexpanded query degrades to `Yes — Rally could not read the schedule.` rather than losing the events. The fallback is deliberately kept rather than deleted — omitting the row would claim a one-off, and an incomplete truth beats a confident wrong answer (#209)
- `/todo` - Todo management page with full CRUD interface
- `/todo/completed` - Read-only page of todos completed before today (local time); reachable only via the `View completed tasks` link on `/todo`, not from the nav bar
- `/shopping` - Shopping list page: an `Add Item` header button opening a dual-mode modal with history-backed autocomplete, store grouping, store filter chips derived from the items on the list, a `Manage stores` button in the Store toolbar group, and drag-to-reorder via the grip on each open row
- `/shopping/purchased` - Read-only page of items purchased before today (local time), grouped by store; reachable only via the `View purchased items` link on `/shopping`, not from the nav bar
- `/notes` - **Notes**: one **Daily Note** per day, from today onward with no upper bound. A day with no note has no card. `Add Note` opens a dual-mode modal; a date that already has a note returns `409` carrying that note's id, and the modal switches to editing it rather than refusing or overwriting. Text is markdown — bold, italic, bullet and numbered lists, and a line break per Enter — rendered **server-side** by `rally.markdown` and returned as `body_html` beside the raw `body`. Markup is rejected at write time (`schemas._reject_markup`) *and* escaped at render; the rule is tag-shaped (`<` + optional `/` + a letter) so `temp < 40` survives
- `/notes/previous` - Read-only notes for days before today, newest first, with server-side search and paging. Reachable only via `View previous notes` on `/notes`, not from the nav
- `/meal-planner` - Meal planning page with date picker and plan management
- `/packing-lists` - **Packing Lists**: a `View` toggle (`By owner` / `By bag`), a `Packing: Due now` filter and an `Items: Not packed / Packed` filter; `Coming Up` (one day box per date from today on, each packing list in it with its pack day, progress, Edit and its items under a collapsed `View more`); and `Packing List Templates` (`Add Packing List Template`, and per template `Schedule` (repeating only), `Pause`/`Resume`, `Edit` and its items under `View more`). `Add Packing List` is the header's button: a one-off, a copy of a template, or a template kept in sync on one day; kept in sync on a date the template is already on, it opens that day. `View previous packing lists` and `Manage bags` sit in the header's meta line
- `/packing-lists/previous` - Read-only day boxes for days before today, newest first, with the `View` toggle, server-side search and paging. Reachable only via `View previous packing lists` on `/packing-lists`, not from the nav
- `/meal-planner/previous` - **Previous Meals**: meals from days before today, with ratings and reviews, Meal Type and Rating chips, Sort, server-side search over the meal and its review, and paging. Reachable only via `View previous meals` on `/meal-planner`, not from the nav. The one archive you can edit, so a save reloads what is loaded rather than jumping back to the first page
- `/settings` - Settings, family member, calendar, and followed-team management page. **Personal Defaults** is the per-person, per-device behavioral section, and everything in it is scoped to the device it is being read on: a `This device` name, a `This device belongs to` control (the device→member binding, `localStorage` only, never sent anywhere), one dropdown per family member per setting in `member_prefs.CATALOG`, and **Devices Rally remembers** — every device, its answer count, when it was last seen, and a `Forget`. Saved on change; the `PUT` carries only the setting that moved
- `/styleguide` - Design system reference: every component and state rendered from the real stylesheet. Unlinked from the nav, but it ships — a styleguide that exists only in development stops matching production
- `/preparedness` - Preparedness stock, grouped by location. Location and status chips, search, and an `Add Item` modal carrying the refresh schedule. Each scheduled row has a `Refreshed` button — the one action performed while standing in the garage holding the thing
- `/go-list` - The printable packing list: every item grouped by location, in walking order, with the unassigned group last. Print stylesheet plus Markdown / CSV / PDF export. Reachable only via the `View go list` link on `/preparedness`, not from the nav bar — it is a view of the inventory, and its nav marks Preparedness as the section you are in

### API Routes
- `/api/dashboard/regenerate` - Force dashboard regeneration and save new snapshot
- `/api/events` - Calendar events. **Two shapes travel through here and they are deliberately different**: an *event* is the stored rule (what the edit form reads), an *occurrence* is one dated instance of it (what every view renders)
  - `GET /api/events?start=&end=&member=&source=` - Expanded **occurrences** from every source, merged and ordered. Local dates; window capped at 366 days; repeatable `member` filters by attendee with OR semantics; `source` is `all` (default), `native`, or `external`. Also runs the once-per-minute due-reminder check
  - `POST /api/events` - Create. Times are **local wall times plus `tzid`**, never UTC instants — the browser does no timezone maths. An all-day `end` is the inclusive last day. `rrule` is validated by parsing it
  - Each occurrence carries **`start_form` / `end_form`** — its own start and end in the shape the edit modal's fields take, formatted server-side in the install's zone. The modal opens on one *occurrence*, so filling it from the event showed the series' date for every occurrence after the first and then sent that date back: `Only this event` wrote it onto the override and the occurrence vanished, and `This and future` started the tail series there rather than at the split, drawing the overlap twice. An occurrence that names no form values falls back to the series, which is what editing the event rather than one instance means
  - The modal **omits `start`/`end` from a save it was not asked to change**, so the API's "leave alone" path applies and `_split_series` derives the tail from the split date. The same habit in `formPayload` that recompiled an `RRULE` on every save (see `tests/visual/test_event_recurrence_roundtrip.py`) applied to times as well. Both defects are invisible to the API's own tests — hand the endpoint a payload without a `start` and all three scopes are already correct — so the guard is a browser test: the payload is the evidence and only the page builds it
  - `GET /api/events/{id}` - The stored series row plus its overrides
  - `GET /api/events/{id}/occurrences?start=&end=` - Occurrences of one series, so the UI can show what a change affects
  - `PUT /api/events/{id}?scope=this|following|all&occurrence_date=` - `this` writes an `event_overrides` row keyed on the **original** occurrence date; `following` truncates the series with `UNTIL` and creates a new event carrying the tail (moving the overrides at or after the split with it); `all` updates the row and **keeps existing overrides** — a moved occurrence stays moved. `occurrence_date` is required for the first two
  - `DELETE /api/events/{id}?scope=…&occurrence_date=` - Cancel one occurrence, truncate the tail, or delete the event and cascade its attendees, overrides and notifications (SQLite does not enforce the references)
  - Creating (`POST`), editing (`PUT`) or deleting (`DELETE`) an event pushes a notice to its attendees, at every scope. The response is unaffected: the notice is best-effort and never fails the write
  - `POST /api/events/describe-recurrence` - Read an **unsaved** rule back as a phrase: `{rrule}` in, `{"description": "every 2 weeks on Tuesday and Thursday"}` out. This is why the event modal does not own a second copy of the recurrence vocabulary, the same reasoning as `POST /api/recurring-todos/preview`. A malformed rule is *described as nothing* rather than rejected — the form asks on every keystroke, so a half-typed rule is the normal case; `validate_rrule` still guards the save
  - `POST /api/events/{id}/notify` - Push now to the event's attendees. Returns `{sent, skipped, muted, failed}` **by name**: "it worked" and "both phones buzzed" are different claims. An attendee with no Pushover key is reported as *skipped*, and one who turned event reminders off is reported as *muted* — the button is filtered like every other push rather than exempted, so it has to say who it dropped
- `/api/notes` - Daily Note CRUD. `GET` lists `date >= today` ascending; `POST`/`PUT` reject markup, an empty body, and any write into the past (`403`); a duplicate date is `409` with `{message, id}`
  - `GET /api/notes/previous?search=&limit=&offset=` - Days before today, newest first. Returns `{items, has_more, total}`; `total` counts every match, which is what the results count reports
- `/api/packing-list-templates` - Packing list templates and their items. Every edit here reaches every day the template is on, except an item a day has changed for itself
  - `GET /api/packing-list-templates` - Every template by name, **whole**: `item_count`, `day_count` (past included — what a delete would make templateless), `upcoming_days`, its `items` in order and its `schedule` (or `null`)
  - `POST /api/packing-list-templates` - Create. `{name, description?, pack_days_before?, copy_from_template_id?}` (`pack_days_before` ≥ 0, default `0`); `409` on a case-insensitive name clash. `copy_from_template_id` starts it with a copy of that template's items (#260), each whole (name, note, owner, bag) in the source's order, unchecked and with nothing linking it back, in the same transaction; only items come over, never the name, description, lead time or schedule. Copying is not typing, so item history is untouched. An unknown source is a `422`; the name check runs first, so neither failure leaves anything behind
  - `GET /api/packing-list-templates/{id}` - One template, whole
  - `PUT /api/packing-list-templates/{id}` - Partial update; `description` uses `UNSET`
  - `DELETE /api/packing-list-templates/{id}` - Delete it with its items and schedule (`204`). **Every day it is on stays**, past and upcoming, made templateless first so it reads exactly as it did (`packing_lists.delete_template`). Bags and history stay, and no count changes
  - `POST /api/packing-list-templates/{id}/items` - `{name, note?, owner_id?, bag? | bag_id?}`. `bag` is a **name** (found ignoring case, or made); `bag_id` must exist; sending both is `422`, as is an unknown owner. Goes to the bottom and records item history
  - `PUT /api/packing-list-templates/{id}/items/{item_id}` - Partial; `note`, `owner_id`, `bag` and `bag_id` use `UNSET`. Keeps its place and its checks; a new name is recorded in history at 0, as a typed name is
  - `DELETE /api/packing-list-templates/{id}/items/{item_id}` - Delete it, its checks and every day's reading of it
  - `POST /api/packing-list-templates/{id}/items/reorder` - `{view: "owner" | "bag", key, item_ids}`: one group as it should now read. Every listed item takes `key` as its owner or bag (`null` is Everyone / No bag) and the items are dealt into the slots they held between them. Duplicates keep their first mention; an item not on this template is `404` and nothing changes; an unknown owner or bag is `422`. Clears `item_order` on the template's days from today on: the template's order wins
  - Every template response carries `bags`: the bags on it as it reads them, outermost first, each `{id, name, owner_id, parent_bag_id, changed, checked}` (`checked` is always false on a template)
  - `PUT /api/packing-list-templates/{id}/bags/{bag_id}` - `{owner_id, parent_bag_id}`: this template's reading of the bag, both fields. Reaches every day the template is on except a day with its own reading. A bag not on the template is `404`; an unknown owner or bag, or a bag put inside itself (at this template's nesting), is `422`; an owner that would make it read as another bag's name and owner on this template is `409`
  - `DELETE /api/packing-list-templates/{id}/bags/{bag_id}/reading` - Reset: the bag reads as the household has it. Idempotent
  - `POST /api/packing-list-templates/{id}/bags/{bag_id}/remove` - Take the bag off the template: its items go to No bag on the template, and the bags that went in it go in nothing there (a reading). No item leaves the list
- `/api/packing-list-bags` - The household's bags, shared by every template
  - `GET /api/packing-list-bags` - A to Z, each with its household `owner_id` and `parent_bag_id` and `item_count` (template items in it)
  - `POST /api/packing-list-bags` - `{name}`, made with no owner; `409` when an ownerless bag already has the name, ignoring case
  - `PUT /api/packing-list-bags/{id}` - Partial: `{name?, owner_id?, parent_bag_id?}` (`owner_id` and `parent_bag_id` use `UNSET`). Renames it everywhere it is used; `409` when the name and owner it would end up with are another bag's (`Emma already has a bag called "Backpack".`); an unknown owner or bag, or a bag put inside itself or a bag inside it, is `422`
  - `DELETE /api/packing-list-bags/{id}` - Delete it; what was in it goes to No bag on templates, days' own items and history, the bags that went in it go in nothing (by default and in every reading), and its readings and grab checks go with it
- `/api/packing-list-items` - Item history, for autocomplete
  - `GET /api/packing-list-items/suggestions?q=&limit=8` - Substring match, prefix matches first, then by `times_added` (past days the name was on a list) and by when it was last typed; empty `q` returns the most packed. Each carries the `owner_id`, `bag_id` and `bag_name` it last had. `limit` is clamped to 25
  - `DELETE /api/packing-list-items/suggestions/{id}` - Forget one; templates and days are left alone
- `/api/packing-list-days` - Templates on days, the checks made on them and each day's own changes. Nothing here writes to a template, and nothing here writes to a day before today (`403`)
  - `GET /api/packing-list-days` - Today on, soonest first, each day **whole** so an entry expands without a second fetch: `packing_list_template_id` (`null` on a templateless day), `name`, `description` (the template's, or a templateless day's own), `label`, `pack_days_before` (resolved), `pack_date`, `schedule_id`, `total`, `checked`, `changed_count` and its `items` as `resolve_day` reads them (each with `source` — `template` or `day` — `owner_id`, `bag_id`, `checked` and `changed`). Runs `process_schedules()` and `count_packed_days()` first
  - `GET /api/packing-list-days/previous?search=&limit=&offset=` - Days before today, newest first, whole. `search` matches the template's name, a templateless day's own name or the day's label, case-insensitively. Returns `{items, has_more, total}`
  - `POST /api/packing-list-days` - One of three bodies (#254). **Kept in sync**: `{packing_list_template_id, date, label?, pack_days_before?}` (`pack_days_before` omitted or `null` follows the template); `409` with `{message, id}` when it is already on that date. **One-off**: `{name, date, label?, pack_days_before}`. **Copy**: the one-off body plus `copy_from_template_id`, which copies the template's items as the day's own, unchecked, with no link back and no history written. A template id with a name or a copy, none of the three, a one-off without a lead time or a name, an unknown template, or a date before today is a `422`. A one-off never clashes
  - `GET /api/packing-list-days/{id}` - One day, whole
  - `PUT /api/packing-list-days/{id}` - Move it (`date`, same `422`/`409` rules), relabel it (`label`, `UNSET`; marks the label hand-edited) or give it its own lead time (`pack_days_before`, `UNSET`; `null` follows the template). Checks stay. On a templateless day `pack_days_before: null` is a `422`, a date never clashes, and `name` renames it (a `422` on a templated day)
  - `DELETE /api/packing-list-days/{id}` - Take the packing list off the day, with its checks and own changes (a templateless day is simply deleted). A scheduled day stays off
  - `PUT /api/packing-list-days/{id}/template-items/{template_item_id}` - Check, uncheck (`checked`) or edit (`name`, `note`, `owner_id`, `bag` / `bag_id`) a **template** item on this day only. Idempotent; `404` for an item not on that template. Returns the whole day. A new name is recorded in history at 0. `404` on a templateless day, which has no template items
  - `DELETE /api/packing-list-days/{id}/template-items/{template_item_id}` - Remove a template item from this day only
  - `POST /api/packing-list-days/{id}/day-items` - Add an item to this day only, after everything else; records item history (`201`, the whole day)
  - `PUT|DELETE /api/packing-list-days/{id}/day-items/{own_id}` - Edit, check or delete one of the day's own items; a new name is recorded in history at 0
  - `POST /api/packing-list-days/{id}/items/reorder` - `{view: "owner" | "bag", key, items: [{source, id}]}`: one group of that day as it should now read, for that day only. The listed items are dealt into the places they held between them and the day's whole order is stored in `item_order`. Within a group it is order only; into another group the item takes that owner or bag on this day (a template item through its reading, so `changed`). Duplicates keep their first mention; an item not on the day is `404` and nothing changes; an unknown owner or bag is `422`; a past day is `403`
  - `POST /api/packing-list-days/{id}/reset` - Uncheck everything on that day, its bags included
  - `POST /api/packing-list-days/{id}/check-all` - Check everything on that day as it reads (a removed item is not on it), and grab every bag on it. Idempotent
  - Every day response also carries `bags_total`, `bags_checked` and `bags` (as the template response, with `checked` meaning grabbed). Reading a day from today on deletes the grab check of any bag no longer on it
  - `PUT /api/packing-list-days/{id}/bags/{bag_id}` - `{checked?, owner_id?, parent_bag_id?}`: grab the bag on this day, or read it differently on this day only. Setting either field writes the day's reading, copying the other from how the bag reads now. Returns the whole day. A bag not on the day is `404`; an unknown owner or bag, or a bag put inside itself, `422`; an owner that would make it read as another bag's name and owner on this day `409`; a past day `403`
  - `DELETE /api/packing-list-days/{id}/bags/{bag_id}/reading` - Reset: the bag reads as the template (or the household) has it again. Idempotent; its grab check stays
  - `POST /api/packing-list-days/{id}/bags/{bag_id}/remove` - Take the bag off this day: each item in it goes to No bag on this day (a template item through its reading, so `changed`), the bags that went in it go in nothing on this day, and its grab check goes. No item leaves the day
- `/api/packing-list-template-schedules` - Repeating schedules, at most one per template. A schedule creates days ahead of time and has no hold over them after, except its label
  - `GET /api/packing-list-template-schedules` - Every schedule, paused ones included, by template name, with `template_name`
  - `POST /api/packing-list-template-schedules` - `{packing_list_template_id, recurrence_type, recurrence_day?, custom_rule?, start_date?, end_date?, label?}`. The rule is checked (`check_recurrence_rule`) — a weekly rule needs a day, a custom weekly one at least one weekday — and so is the range (`end_date` not before `start_date`); either is a `422`, as is an unknown template. A template that already repeats is `409` with `{message, id}`. Nothing is put on a day until days are next listed
  - `GET /api/packing-list-template-schedules/{id}` - One schedule
  - `PUT /api/packing-list-template-schedules/{id}` - Partial; `recurrence_day`, `custom_rule`, `start_date`, `end_date` and `label` use `UNSET`. `{"active": false}` pauses. The rule is re-checked against the **merged** schedule. Days already made keep their dates; a new `label` relabels the ones it made from today on that were not relabeled by hand
  - `DELETE /api/packing-list-template-schedules/{id}` - Delete it; its days stay, with `schedule_id` cleared
- `/api/todos` - Todo CRUD endpoints
  - `GET /api/todos` - List todos (incomplete, plus those completed since local midnight today)
  - `GET /api/todos/completed` - List todos completed **before** local midnight today — the exact complement of the above. Query params: `sort` (one of `completed-newest` (default), `completed-oldest`, `due-soonest`, `due-furthest`, `assignee`, `newest`, `oldest`), repeatable `assignee` (family member ID and/or `unassigned`; OR semantics, empty means all), `limit` (default 50, max 200), `offset`. Returns `{items, has_more, total}`. Sorting, filtering and paging are server-side; recurring processing is deliberately **not** run here.
  - `POST /api/todos` - Create new todo. Pushes to the assignee when one is set (see **Pushover on task assignment**)
  - `GET /api/todos/{id}` - Get specific todo
  - `PUT /api/todos/{id}` - Update todo. Pushes to the assignee only when `assigned_to` changes to somebody new
  - `DELETE /api/todos/{id}` - Delete todo
- `/api/shopping` - Shopping list endpoints
  - `GET /api/shopping/stores` - List stores, ordered by name ASC
  - `POST /api/shopping/stores` - Create a store. `409` on a case-insensitive name conflict
  - `PUT /api/shopping/stores/{id}` - Rename. `409` on conflict with a *different* store
  - `DELETE /api/shopping/stores/{id}` - Delete. **Reassigns the store's items to `store_id = NULL` first** — SQLite FKs aren't enforced, so an orphaned `store_id` would make those items vanish from every rendered group
  - `GET /api/shopping/items?include_hidden=false` - List items, ordered `completed ASC`, then by the hand-arranged `sort_order ASC`, then `created_at DESC`. `sort_order` is neutralised for completed rows so they stay newest-first among themselves. Hides items completed before local midnight today unless `include_hidden=true`. Runs the once-per-local-day retention purge (see below)
  - `POST /api/shopping/items` - Create. Runs the once-per-minute shopping-additions pass **before** the insert (a pass taken afterwards would always find the batch still settling, leaving a `dev`-served instance silent). `201`, or `200` with the existing row when an **open** item with the same trimmed, case-insensitive name already exists in the same store (a merely *completed* match creates a new item). Accepts `store` as a store **name** in place of `store_id` for scripted/voice clients; sending both is `422`, and an unrecognized name falls back to the catch-all rather than erroring or auto-creating a store. A `201` upserts `shopping_item_history`; a `200` does not. A new item is given `min(sort_order) - 1` **within its own store**, so it lands at the top of that group — which is what `created_at DESC` used to do on its own
  - `PUT /api/shopping/items/{id}` - Partial update of `name`, `note`, `store_id`, `completed` (`note`/`store_id` use the `UNSET` sentinel). Completion stamping matches `PUT /api/todos/{id}` exactly. Does **not** touch history. A *changed* `store_id` re-places the item at the top of its new group — a rank held at the old store means nothing at the new one
  - `POST /api/shopping/items/reorder` - Rewrite one store group's order. Body is `{store_id, item_ids}`: the **destination** store (`null` for the catch-all) and that group's items in the order they should read. Every listed item is assigned to `store_id` and numbered by its index, so a cross-store drag is the same call as a within-store one. Idempotent. Duplicate ids keep their first mention; an unknown id is `404` and changes nothing (all-or-nothing — a half-applied order is one nobody asked for); an unknown `store_id` is `422`. The group the item *left* is deliberately not renumbered, because positions are only ever compared. Returns the listed items in their new order
  - `DELETE /api/shopping/items/{id}` - Delete an item; history is untouched
  - `GET /api/shopping/purchased?search=&store=&limit=&offset=` - List items purchased **before** local midnight today — the exact complement of `GET /api/shopping/items`, including completed rows whose `completed_at` is `NULL` so nothing is invisible in both views. Ordered most-recent-first. Optional case-insensitive `search` across name and note; repeatable `store` (store ids and/or `anywhere`; OR semantics, empty means all); `limit` (default 50, max 200), `offset`. Returns `{items, has_more, total, stores}` — `stores` is the chip values with a purchase matching the search, **ignoring** the store filter, so a selected chip does not take the others with it. Runs the once-per-local-day retention purge
  - `GET /api/shopping/suggestions?q=&limit=8` - Autocomplete over `shopping_item_history`. Substring (wildcard) match with `%`/`_` escaped, ranked prefix-matches-first then by `times_added` DESC, `last_added_at` DESC, `name` ASC. Empty `q` returns the top entries by use count. `limit` defaults to 8 and is clamped to 25
  - `DELETE /api/shopping/suggestions/{id}` - Forget a suggestion (history is permanent, so a typo'd add would otherwise haunt autocomplete forever). Leaves `shopping_items` alone
- `/api/recurring-todos` - Recurring todo template CRUD endpoints
  - `GET /api/recurring-todos` - List all recurring todo templates
  - `POST /api/recurring-todos` - Create new recurring todo template
  - `POST /api/recurring-todos/preview` - Ask an **unsaved** rule (`{recurrence_type, recurrence_day, custom_rule, start_date}`) what dates it produces; returns `{"occurrences": ["2027-01-01", "2028-01-01", "2029-01-01"]}`. This exists so the modal's read-back line does not reimplement the recurrence math in JavaScript — `rally.recurrence` stays the only place that knows what "every 12 months on the first Sunday" means. Computed from the rule and today, the same way a new template's first instance is placed; a series already running from a completion anchor can differ
  - `GET /api/recurring-todos/{id}` - Get specific template
  - `PUT /api/recurring-todos/{id}` - Update template. `start_date` uses the `UNSET` sentinel like `custom_rule`, and its three edit states are enforced here: freely editable before anything is generated; after the first instance exists but nothing has been completed, a change re-dates the open instance and resets `last_generated_date` to the new first occurrence (the template owns the anchor — hand-editing the task never moved it); after any instance has been completed the change is a `409`, because the last completion drives the series from then on. Re-sending the value already stored is not a change. A malformed date, or one that is not `YYYY-MM-DD`, is a `422`
  - `DELETE /api/recurring-todos/{id}` - Delete template
- `/api/meal-planner` - Meal plan CRUD endpoints
  - `GET /api/meal-planner` - List all meal plans
  - `POST /api/meal-planner` - Create new meal plan (multiple per date allowed)
  - `GET /api/meal-planner/previous?sort=&min_rating=&meal_type=&search=&limit=&offset=` - Meals before today (local time). `sort` is `rating_desc` (default), `date_desc` or `date_asc`, each ending in `id` so offset paging never repeats or skips a tied meal; repeatable `meal_type`; `search` matches the meal or its review, case-insensitively; `limit` (default 50, max 200), `offset`. Returns `{items, has_more, total}`
  - `GET /api/meal-planner/{id}` - Get specific plan
  - `GET /api/meal-planner/date/{date}` - Get all plans for a date (YYYY-MM-DD)
  - `PUT /api/meal-planner/{id}` - Update plan
  - `PUT /api/meal-planner/{id}/review` - Set or clear a past meal's rating and review
  - `DELETE /api/meal-planner/{id}` - Delete plan
- `/api/family` - Family member CRUD endpoints. Every response carries `notifications: {kind: bool}` — **resolved** values with the defaults already filled in, so no client has to know what the defaults are. Behavioral preferences deliberately do *not* travel here: they belong to a person *on a device*, so a member record cannot carry one without carrying every device the household has ever used
  - `GET /api/family` - List all family members
  - `POST /api/family` - Create new family member. Accepts an optional `notifications` map; omitting it starts the member on the catalog defaults (everything on except `shopping_added`)
  - `GET /api/family/{id}` - Get specific family member
  - `POST /api/family` / `PUT /api/family/{id}` - `color` must be one of `rally.member_colors.MEMBER_COLORS`; anything else is a `422`, including a well-formed but unlisted value like `#ffffff`. Omitting it on create assigns the first unused palette entry; omitting it on update leaves the stored value alone. Responses are **not** validated against the palette — a legacy row is reported as it is, rather than failing the endpoint that Settings needs to repair it
  - `PUT /api/family/{id}` - Update family member. `notifications` is a **partial** map — kinds left out keep what they resolve to today, and an unknown kind is `422` rather than a stored preference nothing will ever read
  - `DELETE /api/family/{id}` - Delete family member. **Deletes their `member_notification_prefs` and `member_preferences` rows first** — nothing enforces the references, the same reason deleting an event cascades its own attendees by hand
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
- `/api/preferences/catalog` - Every per-person behavioral setting, its choices, and its default
  - `GET /api/preferences/catalog` - Reads nothing; this is configuration, not state — the menu, not the order. Settings and `/calendar` are both **server-rendered** from the same `member_prefs.CATALOG`, so the dropdowns and the landing view are in the first paint rather than arriving after a fetch — which matters on a display that repaints as slowly as e-ink. The endpoint exists so anything that was not server-rendered has one authority rather than a copy that drifts. Every setting's default is `auto`, and every setting offers it: a concrete default would move a device nobody had configured, and a catalog without `auto` would make "let Rally pick" an answer nobody can give back
- `/api/devices` - The device registry, and the per-person behavioral settings answered on one. A *device* is the closest thing Rally has to a session: one browser, one token it minted itself and keeps in `localStorage`, one set of answers. There is no enrollment — the household is already behind one front door, so the first request carrying a token creates the record
  - `GET /api/devices` - Every device Rally has heard from, most recently seen first, each with its `answer_count`. This is what lets the family see what is being remembered on its behalf and drop what is not; a browser that clears its site data comes back as a new device and leaves the old row behind
  - `PUT /api/devices/{id}` - Register the device, bump `last_seen_at`, and name it. A PUT to a known URL rather than a POST that assigns an id, because the browser minted the token and is addressing its own record. **Omitting `label` leaves a stored name alone**, so a page saying hello on load cannot overwrite one somebody typed
  - `DELETE /api/devices/{id}` - Forget the device **and every answer stored for it**. Keeping the answers would mean a forgotten device that came back — same browser, same token — found its old preferences waiting, which is not what "forget" says. Idempotent: forgetting one Rally never met is a `204`
  - `GET /api/devices/{id}/preferences` - Every member's answers on this device, **resolved** and keyed by member id. A device Rally has never seen gets the defaults rather than a `404` — a brand new browser asking what to do is the ordinary first request
  - `PUT /api/devices/{id}/preferences/{member_id}` - Write one member's answers on this device. **Partial**: a setting left out keeps its answer, so one dropdown saves itself without overwriting the others. Creates the device row if absent (saving a preference is the strongest possible statement that a device is real). An unknown setting or value is a `422`; an unknown member is a `404`
- `/api/notifications/overview` - What Rally sends, and who currently hears it
  - `GET /api/notifications/overview` - One row per kind: its stable key, label, audience sentence, default, install-wide settings key and whether that switch is on, plus `receiving` / `muted` / `no_key` **by name**. Read-only on purpose: the editor for a preference is the person's own family member record, and an editable member × kind matrix does not survive 390px. Also reports `token_configured`, the first of the five gates
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
  - `GET /api/preparedness/go-list` - Grouped JSON, honors `?location=`
  - `GET /api/preparedness/go-list/export?format=md|csv|pdf` - Download as an attachment
  - `POST /api/preparedness/digest/run?dry_run=true` - Run the digest now. Defaults to a dry run — the honest way to answer "is this working" without waiting until morning or burning the notice rows that suppress a real send
  - `GET /api/preparedness/digest/log` - Recent announcements, newest first
  - `POST /api/preparedness/review` - Run an LLM review of the inventory and store it. `400` with a message written for the person who pressed the button when the feature is off, the inventory is empty, no LLM is configured, or the model did not answer usefully
  - `GET /api/preparedness/review` - The last stored review, plus `stale` (the item count has changed since it ran). Reading **never** calls the model — a review costs real money and several seconds, so a page load must never spend either. `404` until one has been run

### Navigation
Every page extends **`templates/base.html`**, which owns the `<head>`, the wordmark header, the menu button and the sidebar. A page supplies only its `title` block (the part after `Rally — `, always an em dash), its `subtitle`, any page-specific `head` scripts, and its `content`; it names its own sidebar entry with `{% set nav_active = "…" %}` at the top. A nav change is therefore made **once**, in `base.html` — the old nav was copied into fourteen templates and had already drifted. Every page renders through the one Jinja environment in `src/rally/templating.py`, including the Dashboard, which used to be filled in with `str.replace` and so could not share a layout; its HTML-bearing values arrive as `Markup` so they are inserted exactly as before.

Navigation is a **sidebar on the right** (`static/sidebar.js`): docked open where there is room beside the page, and behind a hamburger menu where it would cover content. It lists, in order: Dashboard, Tasks, Shopping, Calendar, Notes, Meal Planner, Packing Lists, Preparedness, then a hairline and **Settings** — the order the old row and `Other` dropdown had, with Settings moved up from the footer (which had left five pages with no way to reach Settings at all). A list scales with new pages where a button row did not, and it scrolls on its own once it runs out of height.

- **A subpage marks its parent**: `/todo/completed` → Tasks, `/shopping/purchased` → Shopping, `/notes/previous` → Notes, `/meal-planner/previous` → Meal Planner, `/packing-lists/previous` → Packing Lists, `/go-list` → Preparedness. `/settings` marks Settings; `/styleguide` marks nothing. The mark is `aria-current="page"`, drawn in the `--ink`/`--inverse` inversion the old active button used
- **The go list is not in the sidebar**: it is a view of the inventory, reached by `View go list` on Preparedness
- **Width alone decides the treatment**, in three bands:
  - **75rem (1200px) and wider — docked.** The page column (`--page-max`, 900px) and the sidebar (`--sidebar-width`, 18rem) fit side by side, so the sidebar is simply open: no button, no scrim, and `html` gains `padding-right: var(--sidebar-width)` so the column centers in the space left of it. Hiding it there would cost a click per navigation to save space the page cannot use. `sidebar.js` never repeats this width — it treats "the button is not displayed" as docked, and drops an overlay's open state when a resize crosses the line
  - **768px up to 75rem — overlay, button in the header.** An open sidebar would cover content here (the laptop window, the wall tablet), so it stays behind a borderless icon at the header's right edge that scrolls away with the page
  - **Below 768px — overlay, button in the corner.** A bordered box fixed to the bottom-right corner is the only way to the nav on a phone, and `body` gains bottom padding so the last row's actions can scroll clear of it
- **The page stays scrollable behind an open sidebar**, and is dimmed more lightly than a modal. The scrim is fixed and never scrollable itself, so a wheel or a finger dragged across it scrolls the document; a *tap* on it lands on the scrim, closes the sidebar, and reaches nothing underneath. Closing is on `click`, never `pointerdown` — removing the scrim before the click fires would hand the click to the checkbox beneath
- Closes on a tap outside, `Esc` (focus returns to the button), the button where it is reachable, following a link, and tabbing out of it. A back/forward restore closes it too, so a cached page never comes back with it open
- On a phone the sidebar is capped narrower than the viewport so there is always a strip to tap; there is no close button
- Hidden in print, with the menu button and scrim. Only the Dashboard keeps a footer (`Last updated …`); every other page has none

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
- `RecurringTodo` - Recurring todo templates with title, description, recurrence_type (daily/weekly/monthly), recurrence_day, assigned_to, has_due_date, remind_days_before, optional start_date (YYYY-MM-DD; the earliest date the series may fire, NULL meaning "from today"), last_generated_date (tracks most recently generated instance's recurrence date), active flag, and timestamps
- `ShoppingStore` - User-defined store items are grouped under (Costco, Trader Joe's, …). Names are unique case-insensitively; there is no seeded "Anywhere" row — the catch-all is `store_id IS NULL`
- `ShoppingItem` - Shopping list item with name, optional note, optional store_id, completion status, completed_at, and timestamps. Uses the same `completed`/`completed_at` columns and semantics as `Todo`, so a completed item stays visible until local midnight; completed rows are deleted 30 days after completion
- `ShoppingItemHistory` - Permanent, deduplicated record of every name ever added (name_key = trimmed + casefolded), with the display casing, the most recently used store_id, a `times_added` counter and `last_added_at`. Powers autocomplete and deliberately survives the purchased-item purge
- `PrepLocation` - A place preparedness stock lives (Garage shelf, Truck, Bug-out bag). Names unique case-insensitively; the catch-all is `location_id IS NULL`, never a seeded row. `sort_order` is physical walking order — a go list is packed in the order you walk it, and alphabetical is the wrong order for that
- `PrepItem` - Preparedness stock with a free-text `quantity`, optional location and notes, and an optional refresh schedule (`refresh_mode` none/date/interval, `refresh_interval_months`, `next_refresh_date`, `remind_days_before`, `last_refreshed_on`). `next_refresh_date` is stored and indexed rather than derived — it is the only column the digest reads
- `PrepRefreshNotice` - Announce-once record keyed `f"{item_id}:{refresh_date}"`. Keying on the *pair* is what re-arms an item for free when its date moves; the unique index is the guarantee, not an optimization
- `MemberNotificationPref` - One family member's answer for one kind of notification (`event_reminder`, `event_change`, `task_assignment`, `prep_refresh`, `shopping_added`), unique on `(family_member_id, kind)`. **An absent row means the kind's default** — the row only exists once somebody has expressed a preference, the same discipline `todo_notify_enabled` follows. A preference only ever *narrows* the kind's audience rule; it can never add somebody to an audience they were not already in
- `PackingListTemplate` (`packing_list_templates`) - A packing list template: name (unique case-insensitively), optional description, `pack_days_before` (≥ 0; `0` packs the day of)
- `PackingListBag` - One of the household's bags, shared by every template and day. Unique by name (case-insensitively) and owner together; no bag is `bag_id IS NULL`. `owner_id` (none is Everyone) and `parent_bag_id` (the bag it goes in) are the household's defaults
- `PackingListTemplateBag` - A template's reading of a bag: `owner_id` and `parent_bag_id` on that template, copied whole. Unique per `(packing_list_template_id, bag_id)`
- `PackingListDayBag` - A day's reading of a bag, which wins over the template's. Unique per `(day_id, bag_id)`
- `PackingListDayBagCheck` - A bag grabbed on a day; the row's existence is the check. Unique per `(day_id, bag_id)`
- `PackingListTemplateItem` (`packing_list_template_items`) - One item on a template: name, optional note, optional `owner_id` (a family member; `NULL` is Everyone), optional `bag_id`, and `sort_order` — one order for the whole template, which both views group
- `PackingListItemHistory` - Every item name somebody has typed (`name_key` = trimmed + casefolded, unique), with the owner and bag last typed, `last_added_at` (last typed) and `times_added`: how many past days the name was on a packing list, counted by `count_packed_days`. A typed name starts at 0. Powers autocomplete and outlives every item
- `PackingListTemplateSchedule` (`packing_list_template_schedules`) - A template put on days by a repeating rule, one per template: `recurrence_type`, `recurrence_day`, `custom_rule`, `start_date`, `end_date` (inclusive), `label`, `active`, and `last_generated_date`, the high-water mark that keeps a removed day from coming back
- `PackingListDay` - A packing list on a date (YYYY-MM-DD): a template's (`packing_list_template_id`) or, when that is NULL, a templateless day's with its own `name`, `description` and `pack_days_before`. An optional `label` (and `label_edited` once it is changed by hand), an optional `pack_days_before` override on a templated day, the `schedule_id` that made it (NULL when added by hand), and `item_order`, the day's hand-arranged order (JSON; NULL is the default). Unique per `(packing_list_template_id, date)`, which lets any number of templateless days share a date. Holds no copy of a template's items
- `PackingListDayItem` - A day's own change: with `template_item_id`, that day's reading of a template item (its name, note, owner and bag, or `removed`); without, an item only that day has, with its own `checked` — every item on a templateless day. One reading per template item per day
- `PackingListDayCheck` - One template item checked on one day; the row's existence is the check. Unique per `(day_id, template_item_id)`
- `MealPlan` - Meal planning (stored in the `dinner_plans` table, a name kept from when it only planned dinners) with date, meal type, plan text, rating and review, attendee_ids (JSON array of family member IDs), cook_id (family member ID), and timestamps. Multiple plans per date are allowed.

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
