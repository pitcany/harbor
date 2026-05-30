#!/bin/bash

echo "Harbor: MCPO Entrypoint"
uv run python --version

# Playwright MCP needs the Chrome-for-Testing runtime libs (libnss3,
# libnspr4, etc). Browser binaries live under /app/cache (host-mounted,
# persistent), but OS libs do not survive container recreation. Install
# once per cold boot; subsequent boots short-circuit on the dpkg check.
PLAYWRIGHT_DEPS="libnss3 libnspr4 libxss1 libgbm1 libxdamage1 libxcomposite1 libxrandr2 libxshmfence1 fonts-liberation libdrm2 libxkbcommon-x11-0 libpango-1.0-0 libcairo2 libatspi2.0-0"
if ! dpkg -s libnss3 libnspr4 >/dev/null 2>&1; then
    echo "Installing Playwright Chrome runtime libs..."
    apt-get update -qq
    apt-get install -y -qq --no-install-recommends $PLAYWRIGHT_DEPS
    echo "Playwright deps installed."
else
    echo "Playwright Chrome runtime libs already present."
fi

echo "JSON Merger is starting..."
uv run python /app/json_config_merger.py --pattern ".json" --output "/app/config.json" --directory "/app/configs"

echo "Merged Configs:"
cat /app/config.json

echo
echo "Starting MCPO..."

# Function to handle shutdown
shutdown() {
    echo "Shutting down..."
    exit 0
}

# Trap SIGTERM and SIGINT signals and call shutdown()
trap shutdown SIGTERM SIGINT

# Original entrypoint
uvx mcpo --config /app/config.json &
# Wait for the process to finish or for a signal to be caught
wait $!