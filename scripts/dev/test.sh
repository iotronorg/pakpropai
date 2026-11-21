#!/usr/bin/env bash
# Run the backend test suite in Docker, mirroring .github/workflows/ci.yml.
#
#   scripts/dev/test.sh                       # full suite (tests/ + apps/)
#   scripts/dev/test.sh tests/test_foo.py -x  # any pytest args
#   REBUILD=1 scripts/dev/test.sh             # rebuild the image (after requirements change)
#
# Creates a private network with throwaway postgres:15 and redis:7 containers.
# Source is bind-mounted, so code edits need no rebuild. No secrets are used:
# all env values below are CI-only placeholders.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
IMAGE="pakpropai-test:dev"
NET="pakpropai-test-net"
PG="pakpropai-test-pg"
RD="pakpropai-test-redis"

if [[ "${REBUILD:-0}" == "1" ]] || ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
  docker build -t "$IMAGE" -f "$ROOT/Dockerfile" "$ROOT"
fi

docker network inspect "$NET" >/dev/null 2>&1 || docker network create "$NET" >/dev/null

if ! docker ps --format '{{.Names}}' | grep -qx "$PG"; then
  docker rm -f "$PG" >/dev/null 2>&1 || true
  docker run -d --name "$PG" --network "$NET" \
    -e POSTGRES_DB=realtron_test -e POSTGRES_USER=realtron -e POSTGRES_PASSWORD=realtronpass \
    postgres:15-alpine >/dev/null
fi
if ! docker ps --format '{{.Names}}' | grep -qx "$RD"; then
  docker rm -f "$RD" >/dev/null 2>&1 || true
  docker run -d --name "$RD" --network "$NET" redis:7-alpine >/dev/null
fi

for _ in $(seq 1 30); do
  docker exec "$PG" pg_isready -U realtron -d realtron_test >/dev/null 2>&1 && break
  sleep 1
done

ARGS=("$@")
if [[ ${#ARGS[@]} -eq 0 ]]; then
  ARGS=(tests/ apps/ -q -p no:cacheprovider)
fi

docker run --rm --network "$NET" \
  -v "$ROOT:/app" -w /app \
  -v pakpropai-test-pip:/root/.cache/pip \
  -e DATABASE_URL="postgres://realtron:realtronpass@$PG:5432/realtron_test" \
  -e SECRET_KEY="ci-only-not-for-production" \
  -e REDIS_URL="redis://$RD:6379/0" \
  -e DJANGO_SETTINGS_MODULE=config.settings.test \
  -e ALLOWED_HOSTS=localhost,127.0.0.1 \
  -e DEBUG=False \
  -e AI_BACKEND=local \
  -e WA_VERIFY_TOKEN=ci-token \
  "$IMAGE" \
  sh -c 'pip install -q pytest pytest-django pytest-cov >/dev/null 2>&1; python -m pytest "$@"' _ "${ARGS[@]}"
