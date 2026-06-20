#!/bin/sh
# Post-Studio sidecar: turns the manual "log in, change password, create API
# key, paste into HARBOR_UNSLOTH_STUDIO_API_KEY" sequence into a one-shot
# zero-click bootstrap.
#
# Studio's auth surface (verified live):
#   1. Bootstrap creds are inlined in the SPA HTML as
#      window.__UNSLOTH_BOOTSTRAP__={"username":"unsloth","password":"<words>"}
#      until the user completes first-run setup.
#   2. POST /api/auth/login with those creds returns a JWT and
#      must_change_password=true.
#   3. POST /api/auth/change-password with the JWT clears the flag and
#      returns a fresh JWT.
#   4. POST /api/auth/api-keys mints a long-lived bearer key (sk-unsloth-...)
#      that other Harbor services consume via HARBOR_UNSLOTH_STUDIO_API_KEY.
#
# Studio's auth.db is bind-mounted at ./services/unsloth-studio/.studio-auth/,
# so the minted account+key normally survive container recreate. The script
# is still idempotent — it tests the stored key first and exits fast when it
# works, and re-runs the full flow only if the key 401s (i.e. someone wiped
# the auth dir or rotated the key out-of-band).
set -e

ENV_FILE="/harbor_env"
KEY_VAR="HARBOR_UNSLOTH_STUDIO_API_KEY"
KEY_PLACEHOLDER="sk-unsloth-studio"
STUDIO_URL="${STUDIO_URL:-http://unsloth-studio:8000}"
# Optional default model auto-load. When non-empty, the bootstrap calls
# POST /v1/load with this value after the API key is in place — turns a
# fresh `harbor up unsloth-studio` into a fully-ready inference endpoint
# for webui/boost/aider with no manual UI step. Studio loads off the
# bind-mounted HF cache; when the model is already cached the call is
# near-instant. Best-effort: a non-200 response is logged but does not
# fail the sidecar.
DEFAULT_MODEL="${HARBOR_UNSLOTH_STUDIO_DEFAULT_MODEL:-}"
# Sidecar key file: cross-integration containers (webui, boost, aider) read
# this at runtime to override the env-baked placeholder API key. Bind-mounted
# from ./services/unsloth-studio/.studio-auth/ on the host, which is also the
# parent dir cross-integrations mount read-only as /run/unsloth-studio-auth/.
KEY_SIDECAR_DIR="/studio_auth"
KEY_SIDECAR_FILE="${KEY_SIDECAR_DIR}/api_key.txt"

# Files written by this container land on bind mounts; chown them on every
# exit path so the host user can edit/delete .env without sudo.
chown_workspace() {
  if [ -n "${HARBOR_USER_ID}" ] && [ -n "${HARBOR_GROUP_ID}" ]; then
    chown "${HARBOR_USER_ID}:${HARBOR_GROUP_ID}" "${ENV_FILE}" 2>/dev/null || true
    if [ -f "${KEY_SIDECAR_FILE}" ]; then
      chown "${HARBOR_USER_ID}:${HARBOR_GROUP_ID}" "${KEY_SIDECAR_FILE}" 2>/dev/null || true
    fi
  fi
}
trap chown_workspace EXIT

# Persist the minted key to a sidecar text file so cross-integration start
# scripts can pick it up at runtime (Compose env substitution happens at
# create time, before this sidecar runs — see compose.x.*.unsloth-studio.yml).
# Idempotent: no-op if the file already matches.
write_key_sidecar() {
  new_key="$1"
  if [ ! -d "${KEY_SIDECAR_DIR}" ]; then
    return 0
  fi
  if [ -f "${KEY_SIDECAR_FILE}" ]; then
    existing=$(cat "${KEY_SIDECAR_FILE}" 2>/dev/null | tr -d '\n')
    if [ "${existing}" = "${new_key}" ]; then
      return 0
    fi
  fi
  printf '%s\n' "${new_key}" > "${KEY_SIDECAR_FILE}"
  chmod 0640 "${KEY_SIDECAR_FILE}" 2>/dev/null || true
}

# Best-effort: load HARBOR_UNSLOTH_STUDIO_DEFAULT_MODEL into Studio if set.
# Idempotent — checks /v1/models first and skips when the requested model
# is already loaded. Container restarts drop the loaded model, so this
# also re-loads after `harbor down/up`. Failures are logged, not fatal.
maybe_load_default_model() {
  bearer="$1"
  if [ -z "${DEFAULT_MODEL}" ]; then
    return 0
  fi
  if [ -z "${bearer}" ]; then
    return 0
  fi
  loaded=$(curl -sS -H "Authorization: Bearer ${bearer}" \
    "${STUDIO_URL}/v1/models" 2>/dev/null \
    | jq -r '.data[0].id // empty' 2>/dev/null || true)
  if [ "${loaded}" = "${DEFAULT_MODEL}" ]; then
    echo "[unsloth-studio-bootstrap] default model '${DEFAULT_MODEL}' already loaded — skipping"
    return 0
  fi
  echo "[unsloth-studio-bootstrap] loading default model '${DEFAULT_MODEL}' (first download may take minutes)..."
  load_status=$(curl -sS -o /tmp/load.out -w '%{http_code}' \
    -X POST "${STUDIO_URL}/v1/load" \
    -H "Authorization: Bearer ${bearer}" \
    -H 'Content-Type: application/json' \
    -d "$(jq -nc --arg m "${DEFAULT_MODEL}" '{model_path:$m}')" \
    --max-time 1800 || echo "000")
  if [ "${load_status}" = "200" ]; then
    echo "[unsloth-studio-bootstrap] default model loaded"
  else
    echo "[unsloth-studio-bootstrap] WARN: /v1/load returned HTTP ${load_status} (continuing): $(dd if=/tmp/load.out bs=1 count=300 2>/dev/null)" >&2
  fi
  rm -f /tmp/load.out
}

# Read current value of HARBOR_UNSLOTH_STUDIO_API_KEY from .env.
current_key() {
  grep -E "^${KEY_VAR}=" "${ENV_FILE}" 2>/dev/null | head -1 | sed -E "s/^${KEY_VAR}=\"?([^\"]*)\"?$/\1/"
}

# Replace HARBOR_UNSLOTH_STUDIO_API_KEY=... in-place. sed -i would error
# with "Resource busy" because .env is bind-mounted as a single file (the
# atomic rename crosses inodes). Render to a tmp file then `cat >` back to
# preserve the original inode that Docker has bound.
write_key() {
  new_key="$1"
  esc_key=$(printf '%s' "${new_key}" | sed 's/[\/&]/\\&/g')
  tmp=$(mktemp -t harbor.XXXXXX)
  sed -E "s|^${KEY_VAR}=.*$|${KEY_VAR}=\"${esc_key}\"|" "${ENV_FILE}" > "${tmp}"
  cat "${tmp}" > "${ENV_FILE}"
  rm -f "${tmp}"
}

echo "[unsloth-studio-bootstrap] waiting for Studio at ${STUDIO_URL}..."
if ! curl -sS --retry 30 --retry-delay 2 --retry-all-errors "${STUDIO_URL}/api/health" >/dev/null; then
  echo "[unsloth-studio-bootstrap] FATAL: Studio not reachable after 60s" >&2
  exit 1
fi

# Step 1: decide whether to run.
#   - Empty / placeholder → first-run bootstrap.
#   - Anything else → respect it (could be a user-set key from the Studio
#     UI, a long-lived JWT, or our own previous bootstrap). If it's our
#     bootstrap output and it 200s, exit fast (idempotent). If it 401s
#     and the prefix matches our minted-key shape (sk-unsloth-<32 hex>),
#     re-bootstrap because Studio's auth.db must have been wiped. Any
#     other value is treated as a manual override and left alone.
KEY=$(current_key)
case "${KEY}" in
  '' | "${KEY_PLACEHOLDER}")
    echo "[unsloth-studio-bootstrap] no key set — running first-run bootstrap"
    ;;
  sk-unsloth-????????????????????????????????)
    status=$(curl -sS -o /dev/null -w '%{http_code}' \
      -H "Authorization: Bearer ${KEY}" \
      "${STUDIO_URL}/v1/models" || true)
    if [ "${status}" = "200" ]; then
      # Always refresh the sidecar file — cross-integration containers read
      # it at runtime even when bootstrap is a no-op.
      write_key_sidecar "${KEY}"
      echo "[unsloth-studio-bootstrap] existing API key still valid — skipping"
      maybe_load_default_model "${KEY}"
      exit 0
    fi
    echo "[unsloth-studio-bootstrap] stored key returned HTTP ${status}; re-bootstrapping (Studio DB likely reset)"
    ;;
  *)
    # Manual override: don't re-mint, but still write the sidecar so
    # cross-integration containers can read this key at runtime.
    write_key_sidecar "${KEY}"
    echo "[unsloth-studio-bootstrap] manual override detected (${KEY_VAR} not in sk-unsloth-* shape) — leaving it alone"
    maybe_load_default_model "${KEY}"
    exit 0
    ;;
esac

# Step 2: scrape the bootstrap creds from the served HTML. Studio only
# emits __UNSLOTH_BOOTSTRAP__ until first-run setup completes; after that
# it's gone. So if we land here with no creds, the auth DB still has a
# user account — either someone changed the password manually, or our
# stored key was revoked while the DB persisted (the .studio-auth bind
# mount survives container recreates).
HTML=$(curl -sS "${STUDIO_URL}/")
BOOTSTRAP_JSON=$(printf '%s' "${HTML}" | grep -oE '__UNSLOTH_BOOTSTRAP__=\{[^<]*\}' | head -1 | sed 's/^__UNSLOTH_BOOTSTRAP__=//')
if [ -z "${BOOTSTRAP_JSON}" ]; then
  cat >&2 <<'EOF'
[unsloth-studio-bootstrap] FATAL: cannot bootstrap — no first-run creds in HTML
                           and the existing key in HARBOR_UNSLOTH_STUDIO_API_KEY
                           is invalid. The auth DB at
                           ./services/unsloth-studio/.studio-auth/ is intact but
                           we have no usable credentials.

Recovery options (pick one, then `harbor up unsloth-studio` again):
  A) Reset the auth DB (loses any custom users/passwords/named keys):
       harbor down unsloth-studio
       harbor exec unsloth-studio rm -f /home/unsloth/.unsloth/studio/auth/auth.db
     OR delete the file directly on the host:
       rm ./services/unsloth-studio/.studio-auth/auth.db
  B) If you generated a replacement key in the Studio UI:
       harbor config set unsloth-studio.api.key sk-unsloth-...
EOF
  exit 1
fi
USERNAME=$(printf '%s' "${BOOTSTRAP_JSON}" | jq -r '.username')
BOOTSTRAP_PW=$(printf '%s' "${BOOTSTRAP_JSON}" | jq -r '.password')
if [ -z "${USERNAME}" ] || [ -z "${BOOTSTRAP_PW}" ] || [ "${USERNAME}" = "null" ] || [ "${BOOTSTRAP_PW}" = "null" ]; then
  echo "[unsloth-studio-bootstrap] FATAL: failed to parse bootstrap creds" >&2
  exit 1
fi

# Step 3: log in with the bootstrap creds.
LOGIN_RESP=$(curl -sS -X POST "${STUDIO_URL}/api/auth/login" \
  -H 'Content-Type: application/json' \
  -d "$(jq -nc --arg u "${USERNAME}" --arg p "${BOOTSTRAP_PW}" '{username:$u, password:$p}')")
JWT=$(printf '%s' "${LOGIN_RESP}" | jq -r '.access_token // empty')
if [ -z "${JWT}" ]; then
  echo "[unsloth-studio-bootstrap] FATAL: login failed: ${LOGIN_RESP}" >&2
  exit 1
fi

# Step 4: change the password. Studio rejects /api/auth/api-keys until
# must_change_password is cleared. HARBOR_UNSLOTH_STUDIO_PASSWORD is harbor.sh-
# persisted so users can read it back via `harbor config get`; the random fallback
# only fires for raw `docker compose` runs that bypass harbor.sh.
NEW_PW="${HARBOR_UNSLOTH_STUDIO_PASSWORD:-harbor-bootstrap-$(LC_ALL=C tr -dc 'A-Za-z0-9' </dev/urandom | dd bs=1 count=24 2>/dev/null)}"
CHANGE_RESP=$(curl -sS -X POST "${STUDIO_URL}/api/auth/change-password" \
  -H "Authorization: Bearer ${JWT}" \
  -H 'Content-Type: application/json' \
  -d "$(jq -nc --arg c "${BOOTSTRAP_PW}" --arg n "${NEW_PW}" '{current_password:$c, new_password:$n}')")
JWT=$(printf '%s' "${CHANGE_RESP}" | jq -r '.access_token // empty')
if [ -z "${JWT}" ]; then
  echo "[unsloth-studio-bootstrap] FATAL: change-password failed: ${CHANGE_RESP}" >&2
  exit 1
fi

# Step 5: mint an API key.
KEY_RESP=$(curl -sS -X POST "${STUDIO_URL}/api/auth/api-keys" \
  -H "Authorization: Bearer ${JWT}" \
  -H 'Content-Type: application/json' \
  -d '{"name":"harbor-bootstrap"}')
NEW_KEY=$(printf '%s' "${KEY_RESP}" | jq -r '.key // empty')
if [ -z "${NEW_KEY}" ]; then
  echo "[unsloth-studio-bootstrap] FATAL: api-key creation failed: ${KEY_RESP}" >&2
  exit 1
fi

# Step 6: persist the key into .env so cross-integration compose files
# (webui, boost, aider) substitute it on their next compose-up. Also write
# a sidecar text file at ${KEY_SIDECAR_FILE} so cross-integration start
# scripts can read the freshly-minted key at runtime — Compose env
# substitution happens at create time, before this sidecar runs.
write_key "${NEW_KEY}"
write_key_sidecar "${NEW_KEY}"
echo "[unsloth-studio-bootstrap] wrote ${KEY_VAR} (prefix: $(printf '%s' "${NEW_KEY}" | cut -c1-12)...) to .env"
maybe_load_default_model "${NEW_KEY}"
echo "[unsloth-studio-bootstrap] zero-click bootstrap complete"
