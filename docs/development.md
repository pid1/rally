# Development

Rally is a FastAPI application over SQLite, with server-rendered Jinja templates and vanilla JavaScript. There is no build step and no frontend framework.

The development environment is reproducible through Nix and devenv, which is what pins Python 3.14 and every tool the project expects.

## Setup

**1. Nix, with flakes:**

```bash
sh <(curl -L https://nixos.org/nix/install) --daemon

# in ~/.config/nix/nix.conf or /etc/nix/nix.conf
experimental-features = nix-command flakes
```

**2. devenv:**

```bash
nix-env -iA devenv -f https://github.com/NixOS/nixpkgs/tarball/nixpkgs-unstable
```

**3. direnv**, optional, but it activates the environment automatically:

```bash
brew install direnv          # macOS
nix-env -i direnv            # Linux

eval "$(direnv hook zsh)"    # or bash, fish …
```

**4. The repository:**

```bash
git clone <this repo>
cd rally

direnv allow    # or: devenv shell

setup           # installs dependencies, initializes the database
dev             # http://localhost:8000
```

## Commands

All of these are devenv scripts, available inside the shell.

| Command                   | What it does                                                   |
| ------------------------- | -------------------------------------------------------------- |
| `setup`                   | Install dependencies and initialize the database               |
| `dev`                     | Run the dev server on port 8000 (blocks)                       |
| `dev-start` / `dev-stop`  | Same server in the background                                  |
| `dev-status` / `dev-logs` | Check it, tail it                                              |
| `demo`                    | A fresh, seeded demo instance on port 8100 in its own database |
| `seed`                    | Seed the dev database with sample data                         |
| `resetdb`                 | Delete and reinitialize the dev database                       |
| `generate`                | Generate a real dashboard summary from the configured APIs     |
| `lint` / `lint-fix`       | ruff                                                           |
| `format`                  | ruff format                                                    |
| `check`                   | `lint` plus format check, which is what CI runs                |
| `build` / `up` / `down`   | Docker image and container                                     |

## Tests

```bash
uv run pytest                        # the whole suite
uv run pytest tests/test_events_api.py -q
```

Every test gets an isolated in-memory database, so the suite never touches your local `rally.db`.

The design-system suite in `tests/visual/` drives a real browser and is skipped unless Playwright and Chromium are installed:

```bash
uv sync --group visual
uv run playwright install chromium
uv run pytest tests/visual -v
```

It asserts the rules in [visual-design-system.md](visual-design-system.md) geometrically: left edges, touch targets, focus rings, fold position. A regression names the rule it broke instead of showing a pixel diff.

CI runs `pytest`, `ruff check .` and `ruff format --check .`, plus the visual suite as a separate job.

## Sample data

`seed` populates a whole family: four members with their own calendars and events, tasks including recurring templates, a shopping list with purchase history, meal plans past and future, and a preparedness inventory spanning overdue, due-soon and scheduled stock.

It seeds no external calendar feeds. A seeded feed URL cannot resolve, so it would only ever render an error banner.

For anything you intend to record or screenshot, use the demo instance instead. It builds the same data in a throwaway database, leaving your own dev database alone.

## The demo instance

```bash
devenv shell    # or: direnv allow
demo
```

`demo` deletes and rebuilds `demo.db`, seeds it, and serves Rally on **http://localhost:8100**. Your `rally.db` and the dev server on port 8000 are untouched. Ctrl+C stops it, and running `demo` again gives you a clean slate.

Without devenv, the same steps by hand:

```bash
export RALLY_DB_PATH="$PWD/demo.db" PYTHONPATH="$PWD/src"
rm -f "$RALLY_DB_PATH"
uv run python -c 'from rally.database import init_db; init_db()'
uv run python -m rally.cli
uv run uvicorn rally.main:app --port 8100
```

The screenshots in `docs/screenshots/` are regenerated from that same seed by
`screenshots` (`scripts/capture_screenshots.py`), which seeds a temporary
database, serves it on its own port and captures every image at a fixed
viewport. Run it after any change that alters what a page looks like. Two
images are deliberately outside it: `demo-poster.png` is the thumbnail for a
walkthrough video hosted on a GitHub release, so refreshing it would advertise
a UI the video does not show, and `event-notify.png` shows the notify control
rather than its result banner, because producing that banner means really
pushing to Pushover.

The sample family is Mom, Dad, Emma and Jake. The data is anchored to the day you run it, so the calendar always has this week's events in it and the preparedness list always has something overdue. The screenshots in this repository and the walkthrough video in the README were both recorded against it.

## Database migrations

Migrations are plain Python files under `migrations/`, run in order by `migrations/run_migrations.py`, which `entrypoint.sh` executes on container startup. Every migration is **idempotent**: it checks whether its change already exists before applying it, so running it twice is safe and an upgrade needs no manual step.

```bash
python3 migrations/run_migrations.py          # run them all
python3 migrations/run_migrations.py && python3 migrations/run_migrations.py   # prove idempotency
docker exec rally python migrations/run_migrations.py
```

To add one: write `migrations/migrate_XXX_description.py`, test it locally, and register it in the list in `run_migrations.py`. The template, the SQLite-specific patterns and the list of existing migrations live in [AGENTS.md](../AGENTS.md#database-migrations).

## Project layout

```
src/rally/          Application code
  main.py           FastAPI app and page routes
  models.py         SQLAlchemy models
  routers/          API route handlers, one module per feature
  calendars/        Calendar adapters (native, ICS, CalDAV) and the shared Occurrence shape
  generator/        Daily summary generation
  notifications.py  Pushover delivery: reminders, change notices, digests
templates/          Jinja templates, one per page
static/styles.css   The whole stylesheet
migrations/         Idempotent migration scripts
tests/              Pytest suite; tests/visual is the design-system suite
docs/               This documentation
```

`AGENTS.md` carries the fuller map, the route reference and the conventions the code follows.
