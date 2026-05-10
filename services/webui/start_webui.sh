#!/bin/bash

echo "Harbor: Custom Open WebUI Entrypoint"
python --version

# Local extension: ensure sqlite3 CLI is available for debugging webui.db
# (idempotent, ~3s first boot, no-op on subsequent boots while the container
#  layer survives. Lost on full recreate -- this script reinstalls then.)
if ! command -v sqlite3 >/dev/null 2>&1; then
    echo "Harbor: installing sqlite3 cli..."
    apt-get update -qq && apt-get install -y --no-install-recommends sqlite3 >/dev/null \
        && rm -rf /var/lib/apt/lists/* \
        && echo "Harbor: sqlite3 installed: $(sqlite3 --version)"
fi

echo "JSON Merger is starting..."
python /app/json_config_merger.py --pattern ".json" --output "/app/backend/data/config.json" --directory "/app/configs"

echo "Merged Configs:"
cat /app/backend/data/config.json

# Seed model presets from JSON if present (best-effort, errors won't block startup)
if [ -f /app/backend/data/seeds/seed_models.py ]; then
    echo
    echo "Seeding model presets..."
    python /app/backend/data/seeds/seed_models.py || echo "Harbor: seed_models.py failed (non-fatal)"
fi

echo
echo "Starting Open WebUI..."

# Function to handle shutdown
shutdown() {
    echo "Shutting down..."
    exit 0
}

# Trap SIGTERM and SIGINT signals and call shutdown()
trap shutdown SIGTERM SIGINT

# Original entrypoint
bash start.sh &
# Wait for the process to finish or for a signal to be caught
wait $!