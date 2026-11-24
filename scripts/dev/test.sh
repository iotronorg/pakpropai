#!/usr/bin/env bash
# Run the backend test suite in Docker, mirroring .github/workflows/ci.yml.
#
#   scripts/dev/test.sh                       # full suite (tests/ + apps/)
#   scripts/dev/test.sh tests/test_foo.py -x  # any pytest args
#   REBUILD=1 scripts/dev/test.sh             # rebuild the image (after requirements change)
#
# Creates a private network with throwaway PostGIS (Postgres 15) and redis:7
# containers. PostGIS is only *available*: the suite runs with
# GEO_POSTGIS_ENABLED off (today's production); tests/test_geo.py enables it
# per test (T-1060b). The image is amd64-only (emulated on Apple Silicon).
# Source is bind-mounted, so code edits need no rebuild. No secrets are used:
# all env values below are CI-only placeholders.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
IMAGE="pakpropai-test:dev"
NET="pakpropai-test-net"
PG="pakpropai-test-pg"
RD="pakpropai-test-redis"
PG_IMAGE="postgis/postgis:15-3.4-alpine"

if [[ "${REBUILD:-0}" == "1" ]] || ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
  docker build -t "$IMAGE" -f "$ROOT/Dockerfile" "$ROOT"
fi

docker network inspect "$NET" >/dev/null 2>&1 || docker network create "$NET" >/dev/null

# Recreate the DB container when it runs another image (e.g. the old postgres:15).
if docker ps --format '{{.Names}}' | grep -qx "$PG" && \
   [[ "$(docker inspect -f '{{.Config.Image}}' "$PG")" != "$PG_IMAGE" ]]; then
  docker rm -f "$PG" >/dev/null
fi
if ! docker ps --format '{{.Names}}' | grep -qx "$PG"; then
  docker rm -f "$PG" >/dev/null 2>&1 || true
  docker run -d --name "$PG" --network "$NET" --platform linux/amd64 \
    -e POSTGRES_DB=realtron_test -e POSTGRES_USER=realtron -e POSTGRES_PASSWORD=realtronpass \
    "$PG_IMAGE" >/dev/null
fi
if ! docker ps --format '{{.Names}}' | grep -qx "$RD"; then
  docker rm -f "$RD" >/dev/null 2>&1 || true
  docker run -d --name "$RD" --network "$NET" redis:7-alpine >/dev/null
fi

# The image's init scripts restart the server once (CREATE EXTENSION postgis
# in template DBs), so wait for the init to finish, then for readiness.
# `grep -c` reads all input. `grep -q` (or output to /dev/null) may exit early, and with pipefail a long log then fails the pipe (KI-070).
for _ in $(seq 1 120); do
  docker logs "$PG" 2>&1 | grep -c "PostgreSQL init process complete" >/dev/null && break
  sleep 1
done
for _ in $(seq 1 60); do
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
