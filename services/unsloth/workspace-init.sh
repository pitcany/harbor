#!/bin/sh
# Pre-create /workspace/work as the host user. Docker creates missing
# bind-mount targets as root:root by default, and the upstream unsloth
# entrypoint then `chmod -R 777`s them — leaving the host directory
# undeletable without `sudo`. By chowning here before the main container
# starts, host ownership survives the upstream chmod.
set -e

if [ -n "${HARBOR_USER_ID}" ] && [ -n "${HARBOR_GROUP_ID}" ]; then
  chown -R "${HARBOR_USER_ID}:${HARBOR_GROUP_ID}" /workspace/work 2>/dev/null || true
fi

# Upstream image bakes HF_HOME=/workspace/.cache/huggingface and runs
# Jupyter/SSH as uid=1001. Top-level chown only — existing cache contents
# (downloaded by vllm/llamacpp/host) keep their original ownership so
# the host user can still `rm` files they downloaded outside this
# container without `sudo`. Same pattern as services/unsloth-studio/
# workspace-init.sh.
if [ -d /workspace/.cache/huggingface ]; then
  chown "1001:${HARBOR_GROUP_ID:-1001}" /workspace/.cache/huggingface 2>/dev/null || true
  chmod 0775 /workspace/.cache/huggingface 2>/dev/null || true
fi
