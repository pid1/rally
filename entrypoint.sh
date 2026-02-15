#!/bin/bash
set -e

# Initialize database
echo "Initializing database..."
python -c 'from rally.database import init_db; init_db()'

# Start the scheduled generator in the background
(
  echo "Starting scheduled generator (runs at 4:00 AM in configured timezone)"
  LAST_RUN_DATE=""
  
  while true; do
    # Get local timezone from config (default to America/Chicago for backward compatibility)
    LOCAL_TZ=$(python -c "import tomllib; f=open('/data/config.toml', 'rb'); cfg=tomllib.load(f); print(cfg.get('local_timezone', 'America/Chicago'))" 2>/dev/null || echo "America/Chicago")
    
    # Get current hour and date in the configured timezone
    current_hour=$(TZ="$LOCAL_TZ" date +%H)
    current_date=$(TZ="$LOCAL_TZ" date +%Y-%m-%d)
    
    # Check if it's 4:00 AM in local timezone AND we haven't run today
    if [ "$current_hour" = "04" ] && [ "$current_date" != "$LAST_RUN_DATE" ]; then
      echo "$(date): Running scheduled dashboard generation for $current_date..."
      python -m rally.generator || echo "$(date): Generation failed"
      LAST_RUN_DATE="$current_date"
      echo "$(date): Generation complete. Next run: tomorrow at 4:00 AM $LOCAL_TZ"
    fi
    
    # Check every 60 seconds (more efficient than 30s)
    sleep 60
  done
) &

# Start the web server in foreground
echo "Starting FastAPI server on port 8000..."
exec uvicorn rally.main:app --host 0.0.0.0 --port 8000
