# shellcheck shell=bash
# Shared by backup.sh and restore_drill.sh (T-6010). Sourced, not run.
#
# Layout on the server (CI copies these next to remote_deploy.sh):
#   <DEPLOY_DIR>/ops/{common,backup,restore_drill}.sh
#   <DEPLOY_DIR>/docker/docker-compose.prod.yml, <DEPLOY_DIR>/.env
#   <DEPLOY_DIR>/backup.env   optional, the operator's: BACKUP_* settings (sourced; keep it mode 600)

fail() { echo "${LOG_TAG:-ops}: $*" >&2; exit 1; }
log() { echo "${LOG_TAG:-ops}: $*"; }
now_iso() { date -u +%Y-%m-%dT%H:%M:%SZ; }

DEPLOY_DIR=${DEPLOY_DIR:-$(cd "$(dirname "$0")/.." && pwd)}
cd "$DEPLOY_DIR" || fail "cannot enter $DEPLOY_DIR"
# shellcheck disable=SC1091
[ ! -f backup.env ] || . ./backup.env

BACKUP_DIR=${BACKUP_DIR:-/var/backups/realtron}
BACKUP_PASSPHRASE_FILE=${BACKUP_PASSPHRASE_FILE:-}
DB_SERVICE=${DB_SERVICE:-db}
WEB_SERVICE=${WEB_SERVICE:-web}
HOST_LABEL=${HOST_LABEL:-$(hostname -s 2>/dev/null || hostname)}

if [ -n "${COMPOSE_ARGS:-}" ]; then
  # shellcheck disable=SC2206
  COMPOSE=(docker compose $COMPOSE_ARGS)          # tests and unusual layouts
else
  [ -f .env ] || fail ".env is missing in $DEPLOY_DIR"
  COMPOSE=(docker compose -p "${COMPOSE_PROJECT:-realtron}" --env-file .env -f docker/docker-compose.prod.yml)
fi
compose() { "${COMPOSE[@]}" "$@"; }

require_passphrase() {
  [ -n "$BACKUP_PASSPHRASE_FILE" ] || fail "BACKUP_PASSPHRASE_FILE is not set (see docs/runbooks/backups.md)"
  [ -f "$BACKUP_PASSPHRASE_FILE" ] || fail "BACKUP_PASSPHRASE_FILE $BACKUP_PASSPHRASE_FILE does not exist"
  [ -s "$BACKUP_PASSPHRASE_FILE" ] || fail "BACKUP_PASSPHRASE_FILE is empty"
  local mode
  mode=$(stat -c %a "$BACKUP_PASSPHRASE_FILE" 2>/dev/null || stat -f %Lp "$BACKUP_PASSPHRASE_FILE")
  case "$mode" in
    400|600) ;;
    *) fail "BACKUP_PASSPHRASE_FILE must be mode 600 or 400 (is $mode)" ;;
  esac
  command -v gpg >/dev/null || fail "gpg is not installed (apt-get install gnupg)"
}

sha256_of() { (sha256sum "$1" 2>/dev/null || shasum -a 256 "$1") | cut -d' ' -f1; }
size_of() { stat -c %s "$1" 2>/dev/null || stat -f %z "$1"; }

# record KIND STATUS STARTED [FILE SIZE SHA256] DETAIL — best effort: a backup that could not be
# recorded is still a backup, and the stale metric makes the alert fire.
record() {
  local kind=$1 status=$2 started=$3 file=${4:-} size=${5:-} sha=${6:-} detail=${7:-}
  local args=(--kind "$kind" --status "$status" --started "$started" --host "$HOST_LABEL" --detail "$detail")
  [ -z "$file" ] || args+=(--file "$file")
  [ -z "$size" ] || args+=(--size "$size")
  [ -z "$sha" ] || args+=(--sha256 "$sha")
  if ! compose exec -T "$WEB_SERVICE" python manage.py record_backup_run "${args[@]}" >/dev/null; then
    echo "${LOG_TAG:-ops}: warning: could not record the $kind run in the database" >&2
  fi
}
