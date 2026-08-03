#!/usr/bin/env bash
# Open WebUI tool-use regression suite.
#
# Runs the full scenario matrix (core behaviors + UI-faithful native mode +
# fault injection) through scripts/owui_tool_harness.py. Temporarily registers
# two mock tool servers (with an automatic backup of the tool-server config)
# and removes them afterwards, even on failure.
#
# Usage: ./scripts/test_openwebui_tools.sh [--quick]
#   --quick  core scenarios only, no fault injection, single trials
#
# Exit codes: 0 all pass, 1 failures, 2 setup error.

set -u
cd "$(dirname "$0")/.."
TS=$(date +%Y%m%d-%H%M%S)
OUT_DIR=artifacts
mkdir -p "$OUT_DIR"
QUICK=${1:-}

command -v python3 >/dev/null || { echo "python3 required"; exit 2; }
curl -sf -m 5 http://127.0.0.1:33801/health >/dev/null || { echo "Open WebUI not reachable on :33801"; exit 2; }

# 1. mock fault server (needed by F* scenarios)
MOCK_PID=""
cleanup() {
  python3 scripts/toolserver_config.py remove-mocks >/dev/null 2>&1
  [ -n "$MOCK_PID" ] && kill "$MOCK_PID" 2>/dev/null
}
if [ "$QUICK" != "--quick" ]; then
  if ! curl -s -m 2 http://172.17.0.1:8123/openapi.json | grep -q mock-fault; then
    python3 scripts/mock_tool_server.py --port 8123 >/tmp/mock_tool_server.log 2>&1 &
    MOCK_PID=$!
    sleep 1
  fi
  trap cleanup EXIT
  python3 scripts/toolserver_config.py add-mocks || { echo "failed to register mock tool servers"; exit 2; }
fi

# 2. run the matrix
STATUS=0
if [ "$QUICK" = "--quick" ]; then
  python3 scripts/owui_tool_harness.py \
    --scenarios scripts/scenarios_baseline.json --repeat 1 \
    --out "$OUT_DIR/regression_core_$TS.json" || STATUS=1
else
  python3 scripts/owui_tool_harness.py \
    --scenarios scripts/scenarios_baseline.json \
    --out "$OUT_DIR/regression_core_$TS.json" || STATUS=1
  python3 scripts/owui_tool_harness.py \
    --scenarios scripts/scenarios_ui_native.json \
    --out "$OUT_DIR/regression_ui_$TS.json" || STATUS=1
fi

echo
echo "results: $OUT_DIR/regression_*_$TS.json"
exit $STATUS
