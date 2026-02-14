#!/bin/bash
set -e

# Initialize database
echo "Initializing database..."
python -c 'from rally.database import init_db; init_db()'

# Start the scheduled generator in the background
(
  echo "Starting scheduled generator (runs at 4:00 AM Central daily)"
  while true; do
    # Get current time in Central (America/Chicago)
    current_hour=$(TZ=America/Chicago date +%H)
    current_minute=$(TZ=America/Chicago date +%M)
    
    # Check if it's 4:00 AM Central (04:00)
    if [ "$current_hour" = "04" ] && [ "$current_minute" = "00" ]; then
      echo "$(date): Running scheduled dashboard generation..."
      python -m rally.generator || echo "$(date): Generation failed"
      # Sleep for 2 minutes to avoid running multiple times in the same minute
      sleep 120
    fi
    
    # Check every 30 seconds
    sleep 30
  done
) &

# Start the web server in foreground
echo "Starting FastAPI server on port 8000..."
exec uvicorn rally.main:app --host 0.0.0.0 --port 8000
