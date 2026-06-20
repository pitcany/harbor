#!/bin/sh
# Provisions the harbor CLI inside a container:
# 1. Symlinks harbor.sh into PATH
# 2. Provisions docker group matching host socket GID
#
# Expects:
#   HARBOR_HOME — path to the mounted harbor repo
#   /var/run/docker.sock — mounted from host
#
# Optional:
#   HARBOR_CLI_USER — username to add to docker group (default: current user)

set -e

HARBOR_HOME="${HARBOR_HOME:-}"
CLI_USER="${HARBOR_CLI_USER:-$(whoami)}"

if [ -z "$HARBOR_HOME" ]; then
  echo "[harbor-cli] HARBOR_HOME is not set, skipping CLI init"
  exit 0
fi

CLI_PATH="$HARBOR_HOME/harbor.sh"
if [ ! -f "$CLI_PATH" ]; then
  echo "[harbor-cli] $CLI_PATH not found, skipping CLI init"
  exit 0
fi

# Symlink harbor into PATH
for bin_dir in /usr/local/bin /usr/bin; do
  if [ -d "$bin_dir" ]; then
    ln -sf "$CLI_PATH" "$bin_dir/harbor"
    break
  fi
done

# Provision docker group if socket is mounted
if [ -S /var/run/docker.sock ]; then
  DOCKER_GID=$(stat -c '%g' /var/run/docker.sock)  # harbor-lint disable=HARBOR010

  # Remove conflicting groups
  if command -v groupdel >/dev/null 2>&1; then
    groupdel docker 2>/dev/null || true
    existing=$(getent group "$DOCKER_GID" 2>/dev/null | cut -d: -f1)
    if [ -n "$existing" ]; then
      groupdel "$existing" 2>/dev/null || true
    fi
  fi

  # Create docker group with correct GID
  if command -v groupadd >/dev/null 2>&1; then
    groupadd -g "$DOCKER_GID" docker 2>/dev/null || true
  elif command -v addgroup >/dev/null 2>&1; then
    addgroup -g "$DOCKER_GID" docker 2>/dev/null || true
  fi

  # Add user to docker group
  if [ "$CLI_USER" != "root" ]; then
    if command -v usermod >/dev/null 2>&1; then
      usermod -aG docker "$CLI_USER" 2>/dev/null || true
    elif command -v adduser >/dev/null 2>&1; then
      adduser "$CLI_USER" docker 2>/dev/null || true
    fi
  fi
fi

# Mount the skill file path for discoverability when it is not already mounted.
SKILL_SOURCE="$HARBOR_HOME/skills/run-llms/SKILL.md"
SKILL_TARGET="/harbor/SKILL.md"
if [ -f "$SKILL_SOURCE" ] && [ ! -e "$SKILL_TARGET" ]; then
  mkdir -p /harbor
  ln -sf "$SKILL_SOURCE" "$SKILL_TARGET"
fi
