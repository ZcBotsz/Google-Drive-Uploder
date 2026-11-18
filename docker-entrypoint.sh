#!/bin/sh
# Prepare the data volume, then drop privileges.
#
# Why this exists: Render disks and Railway volumes are mounted root-owned, so
# a container that starts as `USER driveup` cannot create the SQLite file and
# dies with "unable to open database file". We start as root, fix ownership,
# and immediately step down to the app user.
set -e

DATA_ROOT="${DATA_ROOT:-/app/data}"

if [ "$(id -u)" = "0" ]; then
    mkdir -p "$DATA_ROOT" \
             "${TEMP_DIR:-$DATA_ROOT/tmp}" \
             "${DOWNLOAD_DIR:-$DATA_ROOT/downloads}"
    # best-effort: a read-only or already-correct volume must not kill the boot
    chown -R driveup:driveup "$DATA_ROOT" 2>/dev/null || true
    exec setpriv --reuid=driveup --regid=driveup --init-groups "$@"
fi

# Already unprivileged (e.g. `docker compose run --user`): just start.
exec "$@"
