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

echo "[web] Starting gunicorn..."
exec gunicorn config.wsgi:application \
    --workers "${GUNICORN_WORKERS:-4}" \
    --threads "${GUNICORN_THREADS:-2}" \
    --worker-class gthread \
    --worker-tmp-dir /dev/shm \
    --max-requests 1200 \
    --max-requests-jitter 50 \
    --timeout 30 \
    --graceful-timeout 20 \
    --keep-alive 5 \
    --log-file - \
    --access-logfile - \
    --bind "0.0.0.0:${PORT:-8000}"
