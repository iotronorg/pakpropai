#!/bin/sh
set -e

# Optionally run migrations before the first worker comes up.
# Use RUN_MIGRATIONS=true only on one instance (e.g. a dedicated init container).
if [ "${RUN_MIGRATIONS:-false}" = "true" ]; then
    echo "[worker] Running database migrations..."
    python manage.py migrate --noinput
fi

# Prometheus multiprocess mode (ADR-050): start from an empty directory, or samples
# from processes of the previous container run are added to this one's.
if [ -n "${PROMETHEUS_MULTIPROC_DIR:-}" ]; then
    rm -rf "${PROMETHEUS_MULTIPROC_DIR:?}"
    mkdir -p "$PROMETHEUS_MULTIPROC_DIR"
fi

MODE="${1:-worker}"

case "$MODE" in
    worker)
        # CELERY_QUEUES env var selects which queue(s) this worker consumes.
        # default = lightweight tasks; high-resource = WA processing, STT, ML.
        # If unset, consume from every queue by name (single-service deploys):
        # without -Q Celery reads only the default queue, and WhatsApp is never answered.
        CELERY_QUEUES="${CELERY_QUEUES:-default,high-resource,outbound}"
        QUEUE_ARGS="-Q ${CELERY_QUEUES}"
        echo "[worker] Starting Celery worker (queues=${CELERY_QUEUES}, concurrency=${CELERY_CONCURRENCY:-4})..."
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
