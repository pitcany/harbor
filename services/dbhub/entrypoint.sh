#!/bin/sh
# When DSN is empty, fall back to --demo (bundled sample SQLite database)
# so the service starts cleanly out of the box. Setting DSN via
# `harbor env dbhub DSN ...` switches to the configured database.
set -e

cd /app

# Docker creates missing bind-mount targets as root on the host. The upstream
# image runs as root by default; we briefly use that to chown /app/data to the
# host user, then drop privileges so any SQLite files dbhub writes land on the
# host as ${HARBOR_USER_ID}:${HARBOR_GROUP_ID} — recoverable without `sudo`.
if [ "$(id -u)" = "0" ] && [ -n "${HARBOR_USER_ID}" ] && [ -n "${HARBOR_GROUP_ID}" ]; then
  chown -R "${HARBOR_USER_ID}:${HARBOR_GROUP_ID}" /app/data 2>/dev/null || true
fi

if [ -z "${DSN}" ]; then
  set -- --demo "$@"
fi

# Drop to the host user only when started as root and the IDs are available.
# Busybox `su` doesn't accept `--` as an argv terminator, so anything in "$@"
# that starts with `-` (e.g. `--demo`) gets misparsed if passed positionally.
# Embed the args inside the -c command string instead — properly shell-quoted
# so values containing spaces still survive.
if [ "$(id -u)" = "0" ] && [ -n "${HARBOR_USER_ID}" ] && [ -n "${HARBOR_GROUP_ID}" ]; then
  target_user=$(awk -F: -v uid="${HARBOR_USER_ID}" '$3 == uid {print $1; exit}' /etc/passwd)
  if [ -z "${target_user}" ]; then
    # No user with HARBOR_USER_ID in the image — synthesize one so we never fall through as root.
    target_group=$(awk -F: -v gid="${HARBOR_GROUP_ID}" '$3 == gid {print $1; exit}' /etc/group)
    if [ -z "${target_group}" ]; then
      target_group=harbor
      addgroup -g "${HARBOR_GROUP_ID}" "${target_group}" 2>/dev/null || true
    fi
    target_user=harbor
    adduser -u "${HARBOR_USER_ID}" -G "${target_group}" -D -H "${target_user}" 2>/dev/null || true
  fi
  if [ -n "${target_user}" ]; then
    quoted_args=""
    for arg in "$@"; do
      # Wrap each argument in single quotes; embedded single quotes become '\''.
      escaped=$(printf '%s' "$arg" | sed "s/'/'\\\\''/g")
      quoted_args="${quoted_args} '${escaped}'"
    done
    exec su "${target_user}" -s /bin/sh -c "exec node dist/index.js${quoted_args}"
  fi
fi

exec node dist/index.js "$@"
