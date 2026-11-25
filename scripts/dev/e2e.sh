#!/usr/bin/env bash
# MVP criterion 1 on this machine: real web server (daphne), real Celery workers started
# as in docker/docker-compose.prod.yml, Postgres and Redis; only the model and
# WhatsApp's API are stand-ins.
#   scripts/dev/e2e.sh            KEEP=1 scripts/dev/e2e.sh   (leave it running)
# Uses its own containers and network, with no published ports, and removes them.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
IMAGE="pakpropai-test:dev"
N="pakpropai-e2e"
docker image inspect "$IMAGE" >/dev/null 2>&1 || docker build -t "$IMAGE" -f "$ROOT/Dockerfile" "$ROOT"

cleanup() {
  code=$?
  if [[ $code -ne 0 ]]; then
    for c in web worker worker-ai; do
      echo "── $c (last errors)"
      docker logs "$N-$c" 2>&1 | grep -E 'ERROR|CRITICAL|Traceback|Error:' | tail -15 | cut -c1-400 || true
    done
  fi
  if [[ "${KEEP:-0}" != "1" ]]; then
    docker rm -f "$N-web" "$N-worker" "$N-worker-ai" "$N-stub" "$N-pg" "$N-redis" >/dev/null 2>&1 || true
    docker network rm "$N" >/dev/null 2>&1 || true
  fi
  exit $code
}
trap cleanup EXIT
docker rm -f "$N-web" "$N-worker" "$N-worker-ai" "$N-stub" "$N-pg" "$N-redis" >/dev/null 2>&1 || true
docker network inspect "$N" >/dev/null 2>&1 || docker network create "$N" >/dev/null

docker run -d --name "$N-pg" --network "$N" --platform linux/amd64 -e POSTGRES_DB=e2e -e POSTGRES_USER=e2e \
  -e POSTGRES_PASSWORD=e2e postgis/postgis:15-3.4-alpine >/dev/null
docker run -d --name "$N-redis" --network "$N" redis:7-alpine >/dev/null

ENVS=(-e DATABASE_URL="postgres://e2e:e2e@$N-pg:5432/e2e" -e REDIS_URL="redis://$N-redis:6379/0"
  -e SECRET_KEY=e2e-only-not-for-production -e DJANGO_SETTINGS_MODULE=config.settings.e2e
  -e REALTRON_PLATFORM_DOMAIN="$N-web" -e AI_BACKEND=local -e OLLAMA_BASE_URL="http://$N-stub:9000" -e LOCAL_MODEL=stub
  -e WA_API_URL="http://$N-stub:9000/wa" -e WA_APP_SECRET=e2e-secret -e WA_PHONE_NUMBER_ID=e2e-number
  -e WA_ACCESS_TOKEN=e2e-token -e WA_VERIFY_TOKEN=e2e-verify
  -e E2E_SEED_DATABASE=e2e -e E2E_AGENT_PHONE=+923000009009)
run() { docker run --network "$N" -v "$ROOT:/app" -w /app "${ENVS[@]}" "$@"; }

run -d --name "$N-stub" "$IMAGE" python scripts/e2e/stub_server.py >/dev/null
for _ in $(seq 1 120); do docker logs "$N-pg" 2>&1 | grep -c "PostgreSQL init process complete" >/dev/null && break; sleep 1; done
for _ in $(seq 1 60); do docker exec "$N-pg" pg_isready -U e2e -d e2e >/dev/null 2>&1 && break; sleep 1; done
echo "Migrating…"; run --rm "$IMAGE" python manage.py migrate --noinput >/dev/null 2>&1
run --rm "$IMAGE" python scripts/e2e/seed.py 2>/dev/null | tail -1

run -d --name "$N-web" "$IMAGE" daphne config.asgi:application --bind 0.0.0.0 --port 8000 >/dev/null
run -d --name "$N-worker" -e CELERY_QUEUES=default,outbound -e CELERY_CONCURRENCY=2 "$IMAGE" sh docker/entrypoint.worker.sh worker >/dev/null
run -d --name "$N-worker-ai" -e CELERY_QUEUES=high-resource -e CELERY_CONCURRENCY=2 "$IMAGE" sh docker/entrypoint.worker.sh worker >/dev/null
for _ in $(seq 1 90); do
  run --rm "$IMAGE" python -c "import urllib.request as u; u.urlopen('http://$N-web:8000/api/v1/auth/csrf/', timeout=3)" >/dev/null 2>&1 && break
  sleep 2
done
run --rm -e PYTHONUNBUFFERED=1 -e E2E_BASE_URL="http://$N-web:8000" -e E2E_STUB_URL="http://$N-stub:9000" \
  -e E2E_WA_APP_SECRET=e2e-secret -e E2E_WA_PHONE_ID=e2e-number "$IMAGE" python scripts/e2e/consumer_journey.py
