# Rally

**Your family command center—coordinate, work hard, and make the most of every day.**

Rally helps families come together around a shared daily plan. It synthesizes calendars, weather, and todos into an empowering daily summary that helps your family work as a team and excel at what you do.

## Features

- 📅 **Unified Calendar** - Pulls from Google Calendar and iCloud, filters to today's events, deduplicates automatically
- 🌤️ **Smart Weather Guidance** - Clothing recommendations and activity adjustments
- ✅ **Todo Management** - Track family tasks with priority system (API ready)
- 🤖 **AI-Powered Summaries** - Claude generates encouraging, action-oriented daily plans
- 🏠 **Family-Centered** - Understands your routines, roles, and how you work together
- 📱 **Smart Display Ready** - Elegant grayscale design perfect for e-ink or any display
- 🎨 **Beautiful Design** - Serif typography, clean layout, professional aesthetic
- ⏰ **Scheduled Updates** - Automatically regenerates dashboard at 4:00 AM Central daily
- 💾 **Smart Caching** - Dashboard loads instantly from database, no unnecessary API calls

## Architecture

- **FastAPI** - Modern Python web framework with automatic API docs
- **SQLite** - Zero-config database for todos and dashboard snapshots
- **SQLAlchemy** - Modern ORM with type hints
- **Uvicorn** - High-performance ASGI server
- **Claude AI** - Natural language generation for summaries (Sonnet 4)
- **icalendar** - ICS calendar parsing and filtering
- **Python 3.14** - Latest Python with modern syntax
- **uv** - Fast Python dependency management
- **Nix + devenv** - Reproducible development environment
- **Docker** - Containerized with scheduled generation (4 AM Central)

## Routes

- `/` - Redirects to dashboard
- `/dashboard` - Daily summary with weather, schedule, and suggestions (from cache)
- `/todo` - Todo management interface (placeholder)
- `/dinner-planner` - Dinner planning interface (placeholder)
- `/api/dashboard/regenerate` - Force dashboard regeneration
- `/api/todos/*` - RESTful todo API (wired up, ready for implementation)

All pages include navigation bar for easy switching between sections.

## Prerequisites

### For Development

- [Nix](https://nixos.org/download.html) with flakes enabled
- [devenv](https://devenv.sh/getting-started/)
- [direnv](https://direnv.net/) (recommended for automatic environment activation)

### For Production (NAS Deployment)

- Docker
- OpenWeather API key (free tier)
- Anthropic API key
- Calendar ICS URLs from Google Calendar and/or iCloud

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

# Copy example config files
cp config.toml.example config.toml
cp context.txt.example context.txt

# Edit with your API keys and calendar URLs
vim config.toml

# Edit with your family information
vim context.txt

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

### 1. On Your NAS

```bash
# SSH into your NAS
ssh user@nas-ip

# Clone repository
git clone <your-repo>
cd rally
```

### 2. Prepare Configuration

```bash
# Create data directory
mkdir -p data output

# Copy and edit config
cp config.toml.example data/config.toml
nano data/config.toml

# Copy and edit family context
cp context.txt.example data/context.txt
nano data/context.txt

# Copy agent voice profile (or create your own)
cp agent_voice.txt data/agent_voice.txt
```

### 3. Deploy with Docker

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

### 5. Access

- Dashboard: `http://your-nas-ip:8000/dashboard`
- Todo page: `http://your-nas-ip:8000/todo`
- Dinner Planner: `http://your-nas-ip:8000/dinner-planner`
- Root: `http://your-nas-ip:8000/` (redirects to dashboard)

### 6. Point Smart Display

Configure your tablet or smart display browser to:
- Open: `http://your-nas-ip:8000/dashboard`
- Auto-refresh: The page auto-refreshes every 30 minutes via JavaScript
- Dashboard updates: Automatically regenerated at 4:00 AM Central daily

## Configuration Guide

### Getting Calendar ICS URLs

**Google Calendar:**
1. Open Google Calendar
2. Click ⋮ next to your calendar → Settings and sharing
3. Scroll to "Integrate calendar"
4. Copy "Secret address in iCal format"

**iCloud Calendar:**
1. Open Calendar.app (or iCloud.com)
2. Right-click calendar → Share Calendar
3. Enable "Public Calendar"
4. Copy the webcal:// URL and change to https://

### OpenWeather API

1. Sign up at https://openweathermap.org/api
2. Free tier includes 1000 calls/day
3. Copy your API key

### Anthropic API

1. Sign up at https://console.anthropic.com/
2. Add credits to your account
3. Create an API key
4. Cost: ~$0.02-0.03 per summary generation with Claude Sonnet 4

## Directory Structure

```
rally/
├── src/rally/              # Application source code
│   ├── main.py             # FastAPI application entry point
│   ├── database.py         # SQLAlchemy database configuration
│   ├── models.py           # Database models (DashboardSnapshot, Todo)
│   ├── schemas.py          # Pydantic request/response schemas
│   ├── cli.py              # CLI commands (seed, etc.)
│   ├── generator/          # Summary generation
│   │   ├── generate.py     # Core generation logic with ICS parsing
│   │   └── __main__.py     # CLI entry point
│   └── routers/            # API route handlers
│       ├── dashboard.py    # Dashboard routes
│       └── todos.py        # Todo API endpoints
├── templates/              # HTML templates
│   ├── dashboard.html      # Daily dashboard template
│   ├── todo.html           # Todo management page
│   └── dinner_planner.html # Dinner planner page
├── data/                   # Configuration and data (not in git)
│   ├── config.toml         # API keys, calendar URLs, coordinates
│   ├── context.txt         # Family context for AI
│   ├── agent_voice.txt     # AI agent voice/tone profile
│   └── rally.db            # SQLite database
├── config.toml.example     # Example configuration file
├── context.txt.example     # Example family context
├── devenv.nix              # Development environment scripts
├── devenv.yaml             # devenv configuration
├── pyproject.toml          # Python dependencies (Python 3.14, includes icalendar)
├── Dockerfile              # Production container
├── entrypoint.sh           # Docker entrypoint (scheduled generation + web server)
├── AGENTS.md               # AI agent instructions
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

### Adding More Calendars

Edit `data/config.toml`:

```toml
[calendars]
google_family = "https://..."
icloud_sarah = "https://..."
icloud_work = "https://..."
google_kids_activities = "https://..."
```

### Modifying Family Context

Edit `data/context.txt` with plain English descriptions of:
- Family members and roles
- Daily routines and schedules
- Preferences and constraints
- Weather considerations

This context is fed to Claude to personalize the daily summary.

### Customizing AI Voice

Edit `data/agent_voice.txt` to adjust the tone and style of generated summaries. The file provides guidance to Claude on how to communicate with your family.

## Troubleshooting

### devenv Issues

```bash
# Update devenv inputs
devenv update

# Clean and rebuild
rm -rf .devenv
devenv shell
```

### Python Version Not Available

If Python 3.14 isn't available yet in nixpkgs, edit `devenv.nix`:

```nix
languages.python = {
  enable = true;
  version = "3.13";  # or "3.12"
};
```

### Docker Issues

```bash
# View logs
docker logs rally

# Restart
docker restart rally

# Rebuild from scratch
docker stop rally
docker rm rally
docker build --no-cache -t rally .
docker run -d -p 8000:8000 -v $(pwd)/data:/data --name rally rally
```

### Database Issues

```bash
# The database is auto-created when the app starts
# To reset it:
rm data/rally.db
docker restart rally
```

### Port Already in Use

```bash
# Find and kill process on port 8000
lsof -ti:8000 | xargs kill

# Or use devenv commands
dev-stop
```

## Development Workflow

```bash
# Enter development environment (if not using direnv)
devenv shell

# Make code changes
vim src/rally/main.py

# Check formatting
check

# Auto-fix issues
lint-fix
format

# Test locally
dev

# Build and test with Docker
build
up
```

## Security Notes

- Keep `config.toml` private (contains API keys)
- ICS URLs contain secrets - don't commit them to git
- Consider running behind reverse proxy with HTTPS for remote access
- Database and config files are in `data/` which is .gitignored
- In production, mount `/data` and `/output` as volumes

## Environment Variables

- `RALLY_ENV` - Set to `production` in Docker (default: `development`)
- `RALLY_DB_PATH` - Override database location (default: auto-detected based on env)

## Contributing

Pull requests welcome! Please run `check` before submitting.

For AI agents working on this codebase, see `AGENTS.md` for detailed instructions.
