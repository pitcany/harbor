#!/bin/sh
# Pre-create the workspace bind-mount with host-user ownership so the user
# can manage / delete files without sudo. Docker creates missing bind-mount
# targets as root:root by default; the upstream container then writes into
# them, leaving the host dir undeletable without `sudo`. Chowning here
# before the main container starts keeps host ownership intact.
#
# raglite mounts two subdirs of ${HARBOR_RAGLITE_WORKSPACE}: raglite/ and
# chainlit/. Pre-create both so Docker doesn't auto-create the leafs as
# root:root.
set -e
mkdir -p /workspace/raglite /workspace/chainlit
chown -R "${TARGET_UID:-1000}:${TARGET_GID:-1000}" /workspace
chmod -R 0775 /workspace
