#!/usr/bin/env bash
# Suite: integration
#
# Full httpYac battery against the mock-openai fixture. httpyac is already
# pre-installed into every row image (see tests/containers/*.Containerfile).
set -euo pipefail

suite_log() { echo "[integration] $*"; }

HARBOR_TEST_REPO="${HARBOR_TEST_REPO:-/opt/harbor-test/repo}"
# shellcheck source=../lib/readiness.sh
source "${HARBOR_TEST_REPO}/tests/lib/readiness.sh"

cleanup() {
  local rc=$?
  suite_log "Capturing mock-openai container log..."
  mkdir -p /opt/harbor-test/artifacts/integration
  docker logs harbor.mock-openai \
    > /opt/harbor-test/artifacts/integration/mock-openai.log 2>&1 || true
  suite_log "Tearing down mock-openai (trap, exit=${rc})..."
  harbor down mock-openai >/dev/null 2>&1 || true
  return $rc
}
trap cleanup EXIT

suite_log "harbor up --no-defaults mock-openai"
harbor up --no-defaults mock-openai

wait_for_http "http://localhost:${HARBOR_MOCK_OPENAI_HOST_PORT:-29350}/health" 60

suite_log "Running httpYac battery (tests/http/*.http)..."
# Run from a scratch dir so httpyac does not walk the repo for git roots —
# stale named pipes and build artefacts can throw EIO/ENOTDIR there.
# Spec (hardening.md:298) mandates `httpyac send tests/http/*.http --all`,
# so iterate every .http in the staging dir. Each file is one thematic
# battery — smoke (health/models/chat), streaming (SSE), errors (non-200
# contract), headers (propagation + echo). Vars are inlined per-file so
# there are no zero-request variable-only files polluting the output.
staging=$(mktemp -d -t harbor.XXXXXX)
cp "${HARBOR_TEST_REPO}"/tests/http/*.http "$staging/"
pushd "$staging" >/dev/null
for http_file in ./*.http; do
  suite_log "  → $(basename "$http_file")"
  httpyac send "$http_file" --all --output short
done
popd >/dev/null
rm -rf "$staging"

suite_log "OK"
