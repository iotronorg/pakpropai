#!/bin/sh
set -e

# Optionally run migrations before the first worker comes up.
# Use RUN_MIGRATIONS=true only on one instance (e.g. a dedicated init container).
if [ "${RUN_MIGRATIONS:-false}" = "true" ]; then
    echo "[worker] Running database migrations..."
    python manage.py migrate --noinput
fi

MODE="${1:-worker}"

case "$MODE" in
    worker)
        # CELERY_QUEUES env var selects which queue(s) this worker consumes.
        # default = lightweight tasks; high-resource = WA processing, STT, ML.
        # If unset, consume from all queues (useful for single-service deploys).
        QUEUE_ARGS=""
        if [ -n "${CELERY_QUEUES:-}" ]; then
            QUEUE_ARGS="-Q ${CELERY_QUEUES}"
        fi
        echo "[worker] Starting Celery worker (queues=${CELERY_QUEUES:-all}, concurrency=${CELERY_CONCURRENCY:-4})..."
        exec celery -A config worker \
            --loglevel "${CELERY_LOGLEVEL:-info}" \
            --concurrency "${CELERY_CONCURRENCY:-4}" \
            --without-gossip \
            --without-mingle \
            -Ofair \
            ${QUEUE_ARGS}
        ;;
    beat)
        echo "[beat] Starting Celery beat scheduler..."
        exec celery -A config beat \
            --loglevel "${CELERY_LOGLEVEL:-info}" \
            --scheduler django_celery_beat.schedulers:DatabaseScheduler
        ;;
    *)
        echo "[entrypoint] Unknown mode: $MODE. Use 'worker' or 'beat'." >&2
        exit 1
        ;;
esac
