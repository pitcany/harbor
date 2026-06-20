# syntax=docker/dockerfile:1
# Harbor test row — shared base layer.
#
# Every per-distro row builds on top of harbor-test/base:latest via a
# first-stage `FROM harbor-test/base AS harbor-base` and then
# `COPY --from=harbor-base /daemon.json /etc/docker/daemon.json`.
# The orchestrator builds this image before any row — see
# tests/run.ts :: buildBaseImage.
#
# Why it's a `scratch` image with only a file:
#
#   The rows genuinely diverge at the OS layer (systemd vs OpenRC,
#   apt/dnf/apk, docker.io vs moby-engine vs alpine's `docker` pkg) —
#   there is nothing substantial we can share as a runnable base
#   without picking a single distro and forcing the other four to
#   duplicate it anyway. What we can share is config-file staging:
#   the `/etc/docker/daemon.json` pin to fuse-overlayfs is byte-for-byte
#   identical across every row (avoids the overlay-on-overlay EINVAL
#   that the nested daemon hits on Fedora's overlayfs host rootfs).
#
#   A single edit to this file now propagates to every row without
#   copy-paste drift. That is the point. The spec (hardening.md §
#   "Container-as-VM technique") called for a shared layer; this is
#   the thinnest honest one.
#
# Staged per-row shared state:
#
#   - /daemon.json — nested-daemon storage-driver pin (fuse-overlayfs).
#
# NOT shared (each row still owns these, intentionally):
#
#   - STOPSIGNAL SIGRTMIN+3 (systemd-specific; alpine/OpenRC keeps the
#     default SIGTERM).
#   - CMD ["/sbin/init"] (PID 1 target; systemd's init vs OpenRC's).
#   - `mkdir -p /opt/harbor-test/{repo,artifacts}` (trivial, would be
#     more code to COPY than to write inline).
#   - The package-install step (distro-specific by definition).
FROM scratch

COPY daemon.json /daemon.json
