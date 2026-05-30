#!/bin/sh
set -e

# Collect static files on every container start so the image doesn't need
# build-time secrets and static assets always match the current code revision.
echo "[web] Collecting static files..."
python manage.py collectstatic --noinput

# Run migrations only when explicitly requested, e.g. from a one-off init
# container or a deploy hook.  Never run on every replica — the first replica
# or a dedicated migration job should own this.
if [ "${RUN_MIGRATIONS:-false}" = "true" ]; then
    echo "[web] Running database migrations..."
    python manage.py migrate --noinput
fi

# Use daphne (ASGI) so Django Channels WebSockets work alongside REST.
# daphne is single-process — scale horizontally via Docker replicas.
echo "[web] Starting daphne (ASGI)..."
exec daphne config.asgi:application \
    --bind 0.0.0.0 \
    --port "${PORT:-8000}" \
    --access-log -
