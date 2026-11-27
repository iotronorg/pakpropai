#!/bin/sh
# Deploy one tested build on a Docker host (owner decision 2026-11-25, ADR-053).
#
# Runs ON the server, from the deploy directory CI copies it into:
#   <DEPLOY_DIR>/remote_deploy.sh
#   <DEPLOY_DIR>/docker/docker-compose.prod.yml   (copied by CI on every deploy)
#   <DEPLOY_DIR>/.env                             (the operator's; never copied by CI)
#
# Steps: pull the images for IMAGE_TAG → start db and redis → migrate → check_release (stop at either
# on failure: nothing has switched) → start the new containers → wait until /health/ reports IMAGE_TAG and
# is healthy → record the tag. If health fails, the previous tag is started again and the script
# exits 1. Migrations are not reversed: they are expand-only and safe for the previous build (ADR-023).
#
# Env: IMAGE_TAG, WEB_IMAGE, WORKER_IMAGE (required); COMPOSE_PROJECT (default realtron);
#      COMPOSE_EXTRA_FILES (space separated, e.g. docker/docker-compose.monitoring.yml);
#      HEALTH_HOST (Host header, default localhost; must be in ALLOWED_HOSTS);
#      HEALTH_TIMEOUT_S (default 300), HEALTH_REQUIRED_OK (default 3), HEALTH_INTERVAL_S (default 5).
set -eu

fail() { echo "deploy: $*" >&2; exit 1; }

: "${IMAGE_TAG:?IMAGE_TAG is required}" "${WEB_IMAGE:?WEB_IMAGE is required}" "${WORKER_IMAGE:?WORKER_IMAGE is required}"
echo "$IMAGE_TAG" | grep -Eq '^[0-9a-f]{7,40}$' || fail "IMAGE_TAG must be a git sha"
for image in "$WEB_IMAGE" "$WORKER_IMAGE"; do
  echo "$image" | grep -Eq '^[a-z0-9][a-z0-9._/:-]*$' || fail "bad image name"
done
HEALTH_HOST=${HEALTH_HOST:-localhost}
echo "$HEALTH_HOST" | grep -Eq '^[A-Za-z0-9.-]+$' || fail "bad HEALTH_HOST"

cd "$(dirname "$0")"
[ -f .env ] || fail ".env is missing in $(pwd); create it from .env.example first"
[ -f docker/docker-compose.prod.yml ] || fail "docker/docker-compose.prod.yml is missing"

# --env-file: compose substitutes ${VARS} from the project directory (docker/) by default, which would
# leave POSTGRES_PASSWORD blank; the containers themselves read .env through env_file.
set -- -p "${COMPOSE_PROJECT:-realtron}" --env-file .env -f docker/docker-compose.prod.yml
# The monitoring stack (checklist §52) reads its settings from .env.monitoring.
[ ! -f .env.monitoring ] || set -- "$@" --env-file .env.monitoring
for extra in ${COMPOSE_EXTRA_FILES:-}; do
  [ -f "$extra" ] || fail "missing $extra"
  set -- "$@" -f "$extra"
done
compose() { docker compose "$@"; }
APP="migrate migrate-eu migrate-uk web worker worker-hr beat"

previous=$(cat .deployed_tag 2>/dev/null || true)
export WEB_IMAGE WORKER_IMAGE IMAGE_TAG

echo "deploy: $IMAGE_TAG (previous: ${previous:-none})"
# shellcheck disable=SC2086
compose "$@" pull $APP
compose "$@" up -d --no-build --wait db redis || fail "db or redis did not become healthy; the running build is unchanged"
echo "deploy: migrating"
compose "$@" run --rm --no-deps migrate || fail "migration failed; the running build is unchanged"
compose "$@" run --rm --no-deps migrate-eu || fail "eu migration failed; the running build is unchanged"
compose "$@" run --rm --no-deps migrate-uk || fail "uk migration failed; the running build is unchanged"
# T-6040: frozen modules off, required settings present. Builds older than the command (a rollback)
# skip it. A failure leaves the running build in place; the migrations above are expand-only.
echo "deploy: release check"
compose "$@" run --rm --no-deps migrate sh -c \
  'if python manage.py help check_release >/dev/null 2>&1; then exec python manage.py check_release; fi; echo "check_release is not in this build; skipped"' \
  || fail "release check failed; the running build is unchanged (fix the settings above and deploy again)"
# shellcheck disable=SC2086
compose "$@" up -d --no-build web worker worker-hr beat

probe() {
  compose "$@" exec -T web python -c "
import json, sys, urllib.request
req = urllib.request.Request('http://localhost:8000/health/', headers={'Host': '$HEALTH_HOST', 'X-Forwarded-Proto': 'https'})
try:
    body = json.load(urllib.request.urlopen(req, timeout=5))
except Exception as exc:
    print(type(exc).__name__); sys.exit(1)
print(body.get('version'), body.get('status'))
sys.exit(0 if body.get('version') == '$IMAGE_TAG' and body.get('status') == 'ok' else 1)
" 2>/dev/null
}

deadline=$(( $(date +%s) + ${HEALTH_TIMEOUT_S:-300} ))
ok=0
while [ "$(date +%s)" -lt "$deadline" ]; do
  if out=$(probe "$@"); then
    ok=$((ok + 1)); echo "deploy: healthy ($ok/${HEALTH_REQUIRED_OK:-3}): $out"
    if [ "$ok" -ge "${HEALTH_REQUIRED_OK:-3}" ]; then
      echo "$IMAGE_TAG" > .deployed_tag
      echo "deploy: $IMAGE_TAG is live"
      exit 0
    fi
  else
    ok=0; echo "deploy: waiting for health: ${out:-no answer}"
  fi
  sleep "${HEALTH_INTERVAL_S:-5}"
done

echo "deploy: $IMAGE_TAG did not become healthy" >&2
if [ -n "$previous" ]; then
  echo "deploy: rolling back to $previous" >&2
  IMAGE_TAG=$previous
  export IMAGE_TAG
  compose "$@" up -d --no-build web worker worker-hr beat || true
fi
exit 1
