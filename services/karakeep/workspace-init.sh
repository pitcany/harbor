#!/bin/sh
# Pre-create the workspace bind-mount with host-user ownership so the user
# can manage / delete files without sudo. Docker creates missing bind-mount
# targets as root:root by default; the upstream container then writes into
# them, leaving the host dir undeletable without `sudo`. Chowning here
# before the main container starts keeps host ownership intact.
#
# karakeep mounts ${HARBOR_KARAKEEP_WORKSPACE} at /data on the main service,
# and ${HARBOR_KARAKEEP_WORKSPACE}/meilisearch at /meili_data on the
# karakeep-meilisearch sidecar. Pre-create the meilisearch subdir under
# /workspace so the parent dir gets host-user ownership.
set -e
mkdir -p /workspace/meilisearch
chown -R "${TARGET_UID:-1000}:${TARGET_GID:-1000}" /workspace
chmod -R 0775 /workspace
