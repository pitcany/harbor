#!/bin/sh
# Post-hub sidecar: pre-provisions Beszel so the local Harbor host
# registers itself in the dashboard with no UI clicks.
#
# Beszel's documented headless flow (https://beszel.dev) is:
#   USER_EMAIL/USER_PASSWORD on the hub creates the first user, AUTO_LOGIN
#   signs the user in on visit, and an entry in the universal_tokens
#   collection lets the agent self-register over WebSocket.
#
# This script materializes the missing piece — the universal_tokens row —
# after the hub has finished its first-boot migration. It also extracts
# the hub's freshly generated SSH public key for the agent's KEY_FILE.
#
# Idempotent: every step is safe to repeat across container restarts.
set -e

DB="/beszel_data/data.db"
HUB_KEY="/beszel_data/id_ed25519"
TOKEN_FILE="/agent_data/token"
KEY_FILE="/agent_data/key.pub"

mkdir -p /agent_data

# Files written by this container land on bind mounts; chown them to the
# host user (via trap so it fires on every exit path).
chown_workspace() {
  if [ -n "${HARBOR_USER_ID}" ] && [ -n "${HARBOR_GROUP_ID}" ]; then
    chown -R "${HARBOR_USER_ID}:${HARBOR_GROUP_ID}" /agent_data 2>/dev/null || true
    chown -R "${HARBOR_USER_ID}:${HARBOR_GROUP_ID}" /beszel_data 2>/dev/null || true
  fi
}
trap chown_workspace EXIT

if ! apk add --no-cache openssh-keygen sqlite >/dev/null 2>&1; then
  echo "[beszel-agent-register] FATAL: failed to install openssh-keygen + sqlite (no network?)" >&2
  exit 1
fi

# Wait for the hub's first-boot artifacts: keypair, database, and the user
# row created by the USER_EMAIL/USER_PASSWORD migration.
echo "[beszel-agent-register] waiting for hub to finish first-boot..."
USER_ID=""
ESC_EMAIL=$(printf '%s' "${USER_EMAIL}" | sed "s/'/''/g")
i=0
while [ $i -lt 60 ]; do
  if [ -f "${HUB_KEY}" ] && [ -f "${DB}" ]; then
    USER_ID=$(sqlite3 "${DB}" "SELECT id FROM users WHERE email='${ESC_EMAIL}' LIMIT 1" 2>/dev/null || true)
    if [ -z "${USER_ID}" ]; then
      USER_ID=$(sqlite3 "${DB}" "SELECT id FROM users ORDER BY created LIMIT 1" 2>/dev/null || true)
    fi
    [ -n "${USER_ID}" ] && break
  fi
  i=$((i + 1))
  sleep 2
done

if [ -z "${USER_ID}" ]; then
  echo "[beszel-agent-register] FATAL: hub did not produce a user row after 120s" >&2
  exit 1
fi
echo "[beszel-agent-register] hub user_id=${USER_ID}"

# Always re-derive the agent's KEY_FILE from the hub's private key. The
# source of truth is the hub; overwriting here is safe and self-healing
# if the hub ever rotates keys.
ssh-keygen -y -f "${HUB_KEY}" > "${KEY_FILE}"
chmod 644 "${KEY_FILE}"

# The universal_tokens collection has a UNIQUE constraint on `user` —
# at most one token per user. Treat the DB as the source of truth: if a
# row already exists for our user, reuse its token. Otherwise generate a
# new one and insert. The agent's TOKEN_FILE is then a mirror of the DB.
EXISTING_TOKEN=$(sqlite3 "${DB}" "SELECT token FROM universal_tokens WHERE user='${USER_ID}' LIMIT 1" 2>/dev/null || true)
if [ -n "${EXISTING_TOKEN}" ]; then
  TOKEN="${EXISTING_TOKEN}"
  echo "[beszel-agent-register] reusing existing universal_tokens row for user ${USER_ID}"
else
  TOKEN=$(cat /proc/sys/kernel/random/uuid)
  ROW_ID=$(LC_ALL=C tr -dc 'a-z0-9' </dev/urandom | dd bs=1 count=15 2>/dev/null)
  sqlite3 "${DB}" "PRAGMA busy_timeout=5000; INSERT INTO universal_tokens (id, user, token, created) VALUES ('${ROW_ID}', '${USER_ID}', '${TOKEN}', strftime('%Y-%m-%d %H:%M:%S.000Z', 'now'));"
  echo "[beszel-agent-register] inserted universal_tokens row ${ROW_ID}"
fi
printf '%s' "${TOKEN}" > "${TOKEN_FILE}"
chmod 600 "${TOKEN_FILE}"

echo "[beszel-agent-register] ready — agent will self-register as ${SYSTEM_NAME:-the local hostname}"
