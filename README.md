# Rally

**Your family command center—coordinate, work hard, and make the most of every day.**

Rally helps families come together around a shared daily plan. It synthesizes calendars, weather, and todos into an empowering daily summary that helps your family work as a team and excel at what you do.

## Features

- 📅 **Unified Calendar** - Pulls from Google Calendar and iCloud, filters to the next 7 days, deduplicates automatically
- 🌤️ **Smart Weather Guidance** - Clothing recommendations and activity adjustments
- ✅ **Todo Management** - Full CRUD interface for family tasks
  - Create, edit, complete, and delete todos
  - Optional due dates with elegant date picker
  - Configurable reminder window — set how many days before a due date a todo appears in AI briefings
  - 24-hour visibility window for completed tasks
  - Integrated into AI summaries for schedule optimization
  - Assign todos to family members
- 🔁 **Recurring Todos** - Automate repeating tasks
  - Define daily, weekly, or monthly recurring templates
  - Auto-generates todo instances when due (no open duplicates)
  - Optional due dates and reminder windows per template
  - Assign to family members
  - Activate/deactivate templates without deleting
- 🍕 **Dinner Planner** - Plan meals ahead with prep reminders
  - Date-based meal planning with simple text field
  - View next 7 days of planned dinners
  - AI checks tonight's dinner and suggests prep in "The Briefing" section
- 👨‍👩‍👧‍👦 **Family Members** - Manage family members
- 📆 **Calendar Management** - Add and manage calendars per family member via the Settings UI (ICS feeds, Google CalDAV, Apple iCloud CalDAV)
- ⚙️ **Settings** - Configure API keys, LLM provider, timezone, and calendars through a web UI, with automatic connection verification on save
- 🤖 **AI-Powered Summaries** - Configurable LLM generates encouraging, action-oriented daily plans (Anthropic Claude or any OpenAI-compatible provider. GLM 4.7 Flash works well for local inference.)
- 🏠 **Family-Centered** - Understands your routines, roles, and how you work together
- 📱 **Smart Display Ready** - Elegant grayscale design perfect for e-ink or any display
- 🎨 **Beautiful Design** - Serif typography, clean layout, professional aesthetic
- ⏰ **Scheduled Updates** - Automatically regenerates dashboard at 4:00 AM in your configured timezone
- 💾 **Smart Caching** - Dashboard loads instantly from database, no unnecessary API calls

## Architecture

- **FastAPI** - Modern Python web framework with automatic API docs
- **SQLite** - Zero-config database for todos, recurring todos, dashboard snapshots, family members, calendars, and settings
- **SQLAlchemy** - Modern ORM with type hints
- **Idempotent Migrations** - File-based migration system that runs automatically on startup
- **Uvicorn** - High-performance ASGI server
- **Anthropic / OpenAI** - Configurable LLM provider for summary generation (Anthropic Claude or any OpenAI-compatible API)
- **caldav** - CalDAV protocol support for Google and Apple calendar access
- **icalendar + recurring-ical-events** - ICS calendar parsing with full recurring event support
- **Python 3.14** - Latest Python with modern syntax
- **uv** - Fast Python dependency management
- **Nix + devenv** - Reproducible development environment
- **Docker** - Containerized with scheduled generation (4 AM in configured timezone)

## Prerequisites

### For Development

- [Nix](https://nixos.org/download.html) with flakes enabled
- [devenv](https://devenv.sh/getting-started/)
- [direnv](https://direnv.net/) (recommended for automatic environment activation)

### For Production

- Docker
- OpenWeather API key (free tier)
- LLM API key (Anthropic or an OpenAI-compatible provider)
- Calendar access — ICS feed URLs, or Google/Apple CalDAV with app-specific passwords (configured via the Settings UI)
- Your local timezone (IANA format, e.g. "America/Chicago")

## Development Setup

### 1. Install Nix

```bash
# Install Nix with flakes
sh <(curl -L https://nixos.org/nix/install) --daemon

# Enable flakes (add to ~/.config/nix/nix.conf or /etc/nix/nix.conf)
experimental-features = nix-command flakes
```

### 2. Install devenv

```bash
nix-env -iA devenv -f https://github.com/NixOS/nixpkgs/tarball/nixpkgs-unstable
```

### 3. Install direnv (optional but recommended)

```bash
# On macOS
brew install direnv

# On Linux (using Nix)
nix-env -i direnv

# Add to your shell rc file (~/.bashrc, ~/.zshrc, etc.)
eval "$(direnv hook bash)"  # or zsh, fish, etc.
```

### 4. Clone and Configure

```bash
git clone <your-repo>
cd rally

# Allow direnv (if using)
direnv allow

# Or manually enter the devenv shell
devenv shell
```

### 5. Initialize and Start

```bash
# Inside devenv shell

# Run setup (installs deps, initializes database)
setup

# Start development server
dev
```

Rally will be available at `http://localhost:8000`

## Available Commands (in devenv shell)

See `AGENTS.md` for complete command reference. Common commands:

```bash
setup           # Initialize repo (install deps, init database)
dev             # Start Rally dev server (interactive, port 8000)
dev-start       # Start dev server in background
dev-stop        # Stop background dev server
dev-status      # Check process status
dev-logs        # View recent logs
seed            # Seed database with sample data
generate        # Generate real dashboard from APIs
resetdb         # Delete and reinitialize database
lint            # Check code with ruff
lint-fix        # Auto-fix linting issues
format          # Format code with ruff
check           # Run lint + format check
test-generate   # Test summary generation
build           # Build Docker image
up              # Start Docker container
down            # Stop Docker container
```

## Production Deployment

```bash
# Build the Docker image
docker build -t rally .

# Run the container
docker run -d \
  -p 8000:8000 \
  -v $(pwd)/data:/data \
  -v $(pwd)/output:/output \
  --name rally \
  --restart unless-stopped \
  rally
```

## Database Migrations

Rally uses a **file-based migration system** that runs automatically on container startup. All migrations are **idempotent** (safe to run multiple times).

### How It Works

1. **On Startup**: `entrypoint.sh` runs `migrations/run_migrations.py` before starting the web server
2. **Idempotent**: Each migration checks if changes already exist before executing
3. **Ordered**: Migrations run in sequence as defined in `run_migrations.py`
4. **Fail-Safe**: Container won't start if migrations fail

### Existing Migrations

| Migration | Description |
|-----------|-------------|
| `001_add_due_date` | Add optional `due_date` field to todos table |
| `002_add_family_members` | Add `family_members` and `calendars` tables, `assigned_to` on todos |
| `003_add_settings` | Add key-value `settings` table |
| `004_add_recurring_todos` | Add `recurring_todos` table and `recurring_todo_id` on todos |
| `005_add_dinner_plan_assignees` | Add `attendee_ids` and `cook_id` to dinner plans |
|| `006_add_reminder_window` | Add `remind_days_before` to todos and recurring_todos |
|| `007_add_last_generated_date` | Add `last_generated_date` to recurring_todos |
|| `008_add_caldav_support` | Add `cal_type`, `username`, `password` to calendars for CalDAV |

### Running Migrations Manually

```bash
# Development
python3 migrations/run_migrations.py

# Docker
docker exec rally python migrations/run_migrations.py

# Test idempotency (should succeed twice)
python3 migrations/run_migrations.py && python3 migrations/run_migrations.py
```

### Creating New Migrations

See the Database Migrations section in `AGENTS.md` for complete documentation including template and patterns. Quick overview:

1. **Create** `migrations/migrate_XXX_description.py` using the template in `AGENTS.md`
2. **Test** locally: `python3 migrations/migrate_XXX_description.py`
3. **Register** in `migrations/run_migrations.py` migrations list
4. **Deploy** - runs automatically on container startup

**Key Principle**: Every migration must be **idempotent** - it checks if changes exist before applying them.

## Directory Structure

```
rally/
├── src/rally/              # Application source code
│   ├── main.py             # FastAPI application entry point
│   ├── database.py         # SQLAlchemy database configuration
│   ├── models.py           # Database models (FamilyMember, Calendar, Setting, DashboardSnapshot, Todo, RecurringTodo, DinnerPlan)
│   ├── schemas.py          # Pydantic request/response schemas
│   ├── cli.py              # CLI commands (seed, etc.)
│   ├── recurrence.py       # Recurring todo processing (template → instance generation)
│   ├── generator/          # Summary generation
│   │   ├── generate.py     # Core generation logic with calendar, todos, dinner plans
│   │   └── __main__.py     # CLI entry point
│   ├── utils/              # Shared utilities
│   │   └── timezone.py     # Timezone helpers (now_utc, today_utc, ensure_utc)
│   └── routers/            # API route handlers
│       ├── dashboard.py    # Dashboard routes
│       ├── todos.py        # Todo CRUD API
│       ├── recurring_todos.py # Recurring todo template CRUD API
│       ├── dinner_planner.py # Dinner plan CRUD API
│       ├── family.py       # Family member CRUD API
│       └── settings.py     # Settings and calendar management API
├── static/                 # Static assets
│   └── styles.css          # Application stylesheet
├── templates/              # HTML templates
│   ├── dashboard.html      # Daily dashboard template
│   ├── todo.html           # Todo management page
│   ├── dinner_planner.html # Dinner planner page
│   └── settings.html       # Settings and family/calendar management page
├── data/                   # Configuration and data (not in git)
│   ├── config.toml         # API keys, calendar URLs, coordinates (optional if using Settings UI)
│   ├── context.txt         # Family context for AI
│   ├── agent_voice.txt     # AI agent voice/tone profile
│   └── rally.db            # SQLite database
├── migrations/             # Database migration scripts
│   ├── migrate_add_due_date.py        # Migration 001: add due_date to todos
│   ├── migrate_add_family_members.py  # Migration 002: add family_members, calendars, assigned_to
│   ├── migrate_add_settings.py        # Migration 003: add settings table
│   ├── migrate_add_recurring_todos.py # Migration 004: add recurring_todos table, recurring_todo_id on todos
│   ├── migrate_add_dinner_plan_assignees.py # Migration 005: add attendee_ids, cook_id to dinner_plans
│   ├── migrate_add_reminder_window.py # Migration 006: add remind_days_before to todos and recurring_todos
│   ├── migrate_add_last_generated_date.py # Migration 007: add last_generated_date to recurring_todos
│   ├── migrate_add_caldav_support.py  # Migration 008: add CalDAV columns to calendars
│   └── run_migrations.py              # Migration runner (executes all migrations)
├── config.toml.example     # Example configuration file
├── context.txt.example     # Example family context
├── agent_voice.txt.example # Example AI agent voice profile
├── devenv.nix              # Development environment scripts
├── devenv.yaml             # devenv configuration
├── pyproject.toml          # Python dependencies (Python 3.14)
├── uv.lock                 # Locked dependency versions
├── Dockerfile              # Production container
├── entrypoint.sh           # Docker entrypoint (migrations + scheduled generation + web server)
├── AGENTS.md               # AI agent instructions
├── LICENSE
└── README.md               # This file
```

## Design Philosophy

Rally uses an elegant, **grayscale design** inspired by premium financial terminals and high-end newspapers:

- **Typography**: Playfair Display (headers) and Crimson Text (body) via Google Fonts
- **Colors**: Pure grayscale palette (black, charcoal, grays, white)
- **Layout**: Clean single-column with generous whitespace
- **Borders**: Consistent 1px solid borders throughout
- **Responsive**: Adapts beautifully from phone to tablet to e-ink display

Perfect for e-ink displays, grayscale tablets, or any modern screen.

## Customization

### Adjusting Refresh Interval

Edit `templates/dashboard.html`:

```javascript
// Auto-refresh every 30 minutes (default)
setTimeout(function() { location.reload(); }, 30 * 60 * 1000);

// Change to 15 minutes:
setTimeout(function() { location.reload(); }, 15 * 60 * 1000);
```

- Consider running behind reverse proxy with HTTPS for remote access

## Configuration

Rally supports two configuration approaches:

1. **Settings UI** (recommended) - Configure LLM provider, API keys, timezone, family members, and calendars through the `/settings` page. Settings are stored in the database. When you save LLM, Weather, or Calendar settings, Rally automatically verifies the connection and shows the result in a modal.
2. **config.toml** (fallback) - File-based configuration for API keys, calendar URLs, and coordinates. DB settings take precedence when both exist.

Additional context files:
- `context.txt` - Family scheduling context for AI generation
- `agent_voice.txt` - AI agent tone/voice profile

Copy example files to get started: `config.toml.example`, `context.txt.example`, `agent_voice.txt.example`

### Calendar Setup

Rally supports three calendar types, all configured through the Settings UI:

**ICS Feed** — For public calendar URLs that don't require authentication. Paste the ICS URL directly.

**Google CalDAV** — Connects to Google Calendar using an app-specific password. To set up:

1. Enable 2-Step Verification on your Google account at [myaccount.google.com/security](https://myaccount.google.com/security)
2. Go to [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords)
3. Select "Other" and enter a name like "Rally"
4. Copy the generated 16-character password
5. In Rally Settings, add a calendar with type "Google CalDAV", enter your Gmail address and the app-specific password

**Apple iCloud CalDAV** — Connects to iCloud Calendar using an app-specific password. To set up:

1. Enable Two-Factor Authentication on your Apple account at [appleid.apple.com](https://appleid.apple.com)
2. Go to [appleid.apple.com/account/manage](https://appleid.apple.com/account/manage)
3. Under "Sign-In and Security", select "App-Specific Passwords"
4. Generate a new password with a label like "Rally"
5. Copy the generated password
6. In Rally Settings, add a calendar with type "Apple iCloud CalDAV", enter your Apple ID email and the app-specific password

## Environment Variables

- `RALLY_ENV` - Set to `production` in Docker (default: `development`)
- `RALLY_DB_PATH` - Override database location (default: auto-detected based on env)

## Contributing

Pull requests welcome! Please run `check` before submitting.

For AI agents working on this codebase, see `AGENTS.md` for detailed instructions.
