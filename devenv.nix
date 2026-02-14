{ pkgs, lib, config, inputs, ... }:

let
  # Configure which commands `setup` runs
  setupCommands = [
    "install-deps"
    "db-init"
  ];
in
{
  # Packages
  packages = with pkgs; [
    docker
    docker-compose
    git
    curl
  ];

  # Language support
  languages.python = {
    enable = true;
    version = "3.14";
    uv = {
      enable = true;
      sync.enable = true;
    };
  };

  # Environment variables
  env.PYTHONUNBUFFERED = "1";
  env.PYTHONPATH = "${config.env.DEVENV_ROOT}/src";

  # Scripts
  scripts = {
    # Setup - runs configured commands in sequence
    setup.exec = lib.concatStringsSep " && " setupCommands;

    # Interactive dev commands
    dev.exec = "cd ${config.env.DEVENV_ROOT} && uv run --directory . fastapi dev src/rally/main.py --host 0.0.0.0 --port 8000";

    # Background dev commands
    dev-start.exec = ''
      mkdir -p .devenv/logs .devenv/pids
      cd ${config.env.DEVENV_ROOT}
      nohup uv run --directory . uvicorn rally.main:app --host 0.0.0.0 --port 8000 --reload > .devenv/logs/dev.log 2>&1 &
      echo $! > .devenv/pids/dev.pid
      echo "✓ Rally dev server started in background (PID: $!)"
      echo "  Logs: .devenv/logs/dev.log"
      echo "  Stop: dev-stop"
    '';

    dev-stop.exec = ''
      if [ -f .devenv/pids/dev.pid ]; then
        pid=$(cat .devenv/pids/dev.pid)
        if kill -0 $pid 2>/dev/null; then
          kill $pid && echo "✓ Stopped FastAPI dev server (PID: $pid)"
        else
          echo "FastAPI dev server not running"
        fi
        rm -f .devenv/pids/dev.pid
      else
        echo "No FastAPI PID file found"
      fi
    '';

    dev-status.exec = ''
      echo "=== Dev Process Status ==="
      pidfile=".devenv/pids/dev.pid"
      if [ -f "$pidfile" ]; then
        pid=$(cat "$pidfile")
        if kill -0 $pid 2>/dev/null; then
          echo "dev: Running (PID: $pid)"
        else
          echo "dev: Stopped (stale PID file)"
        fi
      else
        echo "dev: Not started"
      fi
    '';

    dev-logs.exec = "tail -50 .devenv/logs/dev.log 2>/dev/null || echo 'No dev logs found'";

    # Quality commands (NOTE: currently checks app/ which doesn't exist yet)
    lint.exec = "uv run ruff check .";
    lint-fix.exec = "uv run ruff check . --fix";
    format.exec = "uv run ruff format .";
    check.exec = ''
      uv run ruff check .
      uv run ruff format --check .
    '';

    # Dependency management
    install-deps.exec = "uv sync";

    # Database initialization
    db-init.exec = "cd ${config.env.DEVENV_ROOT} && uv run --directory . python -c 'from rally.database import init_db; init_db()' && echo '✓ Database initialized'";
    seed.exec = "cd ${config.env.DEVENV_ROOT} && uv run --directory . python -m rally.cli";
    resetdb.exec = ''
      cd ${config.env.DEVENV_ROOT}
      rm -f rally.db
      uv run --directory . python -c 'from rally.database import init_db; init_db()'
      echo "✓ Database reset"
    '';
    generate.exec = "cd ${config.env.DEVENV_ROOT} && uv run --directory . python -m rally.generator";

    # Testing and generation
    test-generate.exec = "cd ${config.env.DEVENV_ROOT} && uv run --directory . python -m rally.generator";

    # Docker commands (NOTE: docker-compose.yml not yet created)
    build.exec = "docker build -t rally .";
    up.exec = "docker run -d -p 8000:8000 -v $(pwd)/data:/data -v $(pwd)/output:/output --name rally rally";
    down.exec = "docker stop rally && docker rm rally";
    logs.exec = "docker logs -f rally";
    logs-tail.exec = "docker logs --tail=50 rally";
    restart.exec = "docker restart rally";
  };

  # Enter shell hook
  enterShell = ''
    echo "🚀 Rally Development Environment"
    echo ""
    echo "Python: $(python --version)"
    echo "uv: $(uv --version)"
    echo ""
    echo "📦 Installing dependencies..."
    echo ""
    echo "Setup:"
    echo "  setup            - Initialize repo (runs: ${lib.concatStringsSep ", " setupCommands})"
    echo ""
    echo "Interactive commands (block until killed):"
    echo "  dev              - Start Rally dev server (port 8000)"
    echo ""
    echo "Background commands (for agents/scripts):"
    echo "  dev-start        - Start FastAPI in background"
    echo "  dev-stop         - Stop background processes"
    echo "  dev-status       - Check process status"
    echo "  dev-logs         - View recent logs"
    echo ""
    echo "Quality commands:"
    echo "  lint             - Run ruff linter"
    echo "  lint-fix         - Run ruff with auto-fix"
    echo "  format           - Run ruff formatter"
    echo "  check            - Run lint + format check"
    echo ""
    echo "Database commands:"
    echo "  db-init          - Initialize SQLite database"
    echo "  seed             - Seed database with sample data"
    echo "  resetdb          - Delete and reinitialize database"
    echo "  generate         - Generate real dashboard snapshot using APIs"
    echo ""
    echo "Testing:"
    echo "  test-generate    - Test summary generation"
    echo ""
    echo "Docker commands:"
    echo "  build            - Build Docker image"
    echo "  up               - Start Docker containers"
    echo "  down             - Stop Docker containers"
    echo "  logs             - View Docker logs (follows)"
    echo "  logs-tail        - View last 50 lines"
    echo "  restart          - Restart Docker containers"
    echo ""
    echo "Other commands:"
    echo "  install-deps     - Install dependencies with uv"
    echo ""
  '';
}
