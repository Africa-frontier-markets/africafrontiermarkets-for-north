#!/bin/sh
# AFM Docker entrypoint.
#
# Migrations run here (gated by AFM_RUN_MIGRATIONS=true, set on exactly
# one service — the API — in render.yaml) rather than relying on
# preDeployCommand, since that field's support for Docker-runtime
# services on Render is less consistently documented than for native
# runtimes. This makes the image self-contained regardless.
set -e

if [ "${AFM_RUN_MIGRATIONS:-false}" = "true" ]; then
    echo "[entrypoint] Running database migrations..."
    alembic upgrade head
fi

exec "$@"
