#!/usr/bin/env bash
# Suite: smoke
#
# Brings the mock-openai fixture up, confirms /health, exercises a few
# read-only CLI commands, then tears down. No network dependency past the
# already-built image.
set -euo pipefail

suite_log() { echo "[smoke] $*"; }

HARBOR_TEST_REPO="${HARBOR_TEST_REPO:-/opt/harbor-test/repo}"
# shellcheck source=../lib/readiness.sh
source "${HARBOR_TEST_REPO}/tests/lib/readiness.sh"

cleanup() {
  local rc=$?
  suite_log "Tearing down mock-openai (trap, exit=${rc})..."
  harbor down mock-openai >/dev/null 2>&1 || true
  return $rc
}
trap cleanup EXIT

suite_log "harbor up --no-defaults mock-openai"
harbor up --no-defaults mock-openai

suite_log "Waiting for /health..."
wait_for_http "http://localhost:${HARBOR_MOCK_OPENAI_HOST_PORT:-29350}/health" 60

suite_log "harbor ls"
harbor ls >/dev/null

suite_log "harbor ps mock-openai"
harbor ps mock-openai

suite_log "harbor down mock-openai"
harbor down mock-openai
# Disarm trap — teardown happened cleanly.
trap - EXIT

suite_log "OK"
