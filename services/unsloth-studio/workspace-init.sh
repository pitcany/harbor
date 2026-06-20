#!/bin/sh
# Pre-create /workspace/work as the host user. Docker creates missing
# bind-mount targets as root:root by default, and the upstream unsloth
# entrypoint then `chmod -R 777`s them — leaving the host directory
# undeletable without `sudo`. By chowning here before the main container
# starts, host ownership survives the upstream chmod.
#
# Also pre-creates /studio_auth (the bind-mount target for Studio's auth
# directory) and chowns it to the in-container `unsloth` user (uid=1001),
# which Studio runs as. Without this, Studio fails to create auth.db and
# the bootstrap sidecar has no first-run creds to scrape.
set -e

if [ -n "${HARBOR_USER_ID}" ] && [ -n "${HARBOR_GROUP_ID}" ]; then
  chown -R "${HARBOR_USER_ID}:${HARBOR_GROUP_ID}" /workspace/work 2>/dev/null || true
fi

# Studio's container `unsloth` user is uid=1001:gid=1001 (baked into the
# upstream image; not configurable). Mirror that owner on the bind mount,
# but keep the dir traversable by the host group so the user can `ls` /
# wipe it from the host without `sudo`. Studio applies its own 0600 perms
# to auth.db inside the dir.
if [ -d /studio_auth ]; then
  chown -R "1001:${HARBOR_GROUP_ID:-1001}" /studio_auth 2>/dev/null || true
  chmod 0775 /studio_auth 2>/dev/null || true
fi

# Studio's state dir (/home/unsloth/.unsloth/studio in the main container)
# holds studio.db, exports/, outputs/, runs/, .venv_t5_*, etc. Pre-chown
# top-level only — existing user data keeps its original ownership so
# manual host-side fixups don't get clobbered on every up. Without this
# top-level chown the very first `harbor up` lands on a root:root dir and
# Studio fails to write studio.db. Same chown shape as /studio_auth: 1001
# owns, host group has rwx so the host user (when host_gid matches
# HARBOR_GROUP_ID) can `rm -rf` from the host without `sudo`.
if [ -d /studio_state ]; then
  chown "1001:${HARBOR_GROUP_ID:-1001}" /studio_state 2>/dev/null || true
  chmod 0775 /studio_state 2>/dev/null || true
fi

# Studio bakes HF_HOME=/workspace/.cache/huggingface into the upstream
# image and runs as uid=1001. Without owner fixup here, new downloads
# via Studio's UI / `POST /v1/load` fail with EACCES on a host cache
# owned by the host user. Top-level chown only: existing cache contents
# (downloaded by vllm/llamacpp/host) keep their original ownership, so
# the host user can still `rm` files they downloaded outside Studio
# without `sudo`. The host group bit (0775) lets host_gid traverse and
# edit when host gid matches HARBOR_GROUP_ID.
if [ -d /workspace/.cache/huggingface ]; then
  chown "1001:${HARBOR_GROUP_ID:-1001}" /workspace/.cache/huggingface 2>/dev/null || true
  chmod 0775 /workspace/.cache/huggingface 2>/dev/null || true
fi
