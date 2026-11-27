#!/usr/bin/env bash
# Restore drill (T-6010): prove the newest backup (or the file given) restores, without touching
# the live database. Run monthly on the server (docs/runbooks/backups.md) and before risky changes.
#
#   ops/restore_drill.sh                      # newest realtron-<UTC time>.dump.gpg in BACKUP_DIR
#   ops/restore_drill.sh /path/to/file.dump.gpg
#
# 1. checksum checked against the .sha256 written by backup.sh;
# 2. a throwaway Postgres container from the SAME image as the live db, with no network;
# 3. decrypt → pg_restore --exit-on-error into it (no plaintext dump on disk);
# 4. checks: core tables present and migrations recorded; row counts of the main tables reported
#    (compare them with the live system: a restore that "works" but is empty is a failure);
# 5. timing reported (a measured RTO for this database size), container removed, run recorded.
set -Eeuo pipefail
umask 077
LOG_TAG=restore-drill
# shellcheck source=common.sh
. "$(dirname "$0")/common.sh"
require_passphrase

file=${1:-}
if [ -z "$file" ]; then
  # Newest by the UTC timestamp in the name (globs sort), not by mtime, which a copy changes.
  shopt -s nullglob
  backups=("$BACKUP_DIR"/realtron-*.dump.gpg)
  shopt -u nullglob
  [ "${#backups[@]}" -gt 0 ] || fail "no backups in $BACKUP_DIR"
  file=${backups[${#backups[@]}-1]}
fi
[ -f "$file" ] || fail "$file does not exist"
name=$(basename "$file")
started=$(now_iso)
t0=$(date +%s)
drill="realtron-restore-drill-$$"

cleanup() { docker rm -f "$drill" >/dev/null 2>&1 || true; }
on_error() {
  # set -E also runs this trap in subshells; only the main shell reports and exits.
  [ "$BASH_SUBSHELL" -eq 0 ] || return 0
  local line=$1
  trap - ERR
  cleanup
  record drill failed "$started" "$name" "" "" "failed at restore_drill.sh line $line"
  log "FAILED (line $line)"
  exit 1
}
trap 'on_error $LINENO' ERR
trap cleanup EXIT

if [ -f "$file.sha256" ]; then
  expected=$(cut -d' ' -f1 "$file.sha256")
  actual=$(sha256_of "$file")
  [ "$expected" = "$actual" ] || { log "checksum mismatch: $name"; false; }
else
  log "warning: no $name.sha256; checksum not verified"
fi

db_container=$(compose ps -q "$DB_SERVICE")
[ -n "$db_container" ] || { log "the $DB_SERVICE service is not running (needed for its image)"; false; }
image=$(docker inspect -f '{{.Config.Image}}' "$db_container")
log "restoring $name into a throwaway $image container"
docker run -d --name "$drill" --network none -e POSTGRES_USER=realtron -e POSTGRES_DB=realtron_drill \
  -e POSTGRES_PASSWORD="drill-$$" "$image" >/dev/null
ready=0
for _ in $(seq 1 60); do
  # The image restarts the server once after initdb; wait for two consecutive answers.
  if docker exec "$drill" pg_isready -q -U realtron -d realtron_drill; then
    ready=$((ready + 1)); [ "$ready" -ge 2 ] && break
  else
    ready=0
  fi
  sleep 1
done
[ "$ready" -ge 2 ] || { log "throwaway database did not start"; false; }

gpg --batch --quiet --pinentry-mode loopback --passphrase-file "$BACKUP_PASSPHRASE_FILE" --decrypt "$file" \
  | docker exec -i "$drill" pg_restore -U realtron -d realtron_drill --no-owner --no-privileges --exit-on-error
t_restore=$(( $(date +%s) - t0 ))

sql() { docker exec "$drill" psql -U realtron -d realtron_drill -tAq -v ON_ERROR_STOP=1 -c "$1"; }
for table in django_migrations users organizations leads audit_events backup_runs; do
  [ "$(sql "SELECT to_regclass('public.$table') IS NOT NULL")" = "t" ] || { log "table $table is missing"; false; }
done
migrations=$(sql "SELECT count(*) FROM django_migrations")
[ "$migrations" -gt 0 ] || { log "no migrations recorded"; false; }
users=$(sql "SELECT count(*) FROM users")
organizations=$(sql "SELECT count(*) FROM organizations")
leads=$(sql "SELECT count(*) FROM leads")
listings=$(sql "SELECT count(*) FROM properties" 2>/dev/null || echo "?")
newest=$(sql "SELECT coalesce(max(applied)::text, '') FROM django_migrations")
size=$(size_of "$file")

detail="restored in ${t_restore}s; migrations=$migrations (newest $newest); users=$users; organizations=$organizations; leads=$leads; listings=$listings"
trap - ERR
record drill ok "$started" "$name" "$size" "" "$detail"
log "ok: $name — $detail"
