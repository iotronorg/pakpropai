#!/usr/bin/env bash
# Encrypted database backup (T-6010). Run on the server by a timer (docs/runbooks/backups.md).
#
# 1. pg_dump inside the running db container (same major version as the server), custom format;
# 2. encrypted on the way out with gpg (AES-256, passphrase from BACKUP_PASSPHRASE_FILE): no
#    plaintext dump is ever written to disk;
# 3. checked: the archive decrypts and pg_restore can read its table of contents;
# 4. checksum file written, optional off-host copy (BACKUP_UPLOAD_CMD), old local files pruned;
# 5. recorded in the database (BackupRun) for the metrics and alerts.
#
# Env (or <DEPLOY_DIR>/backup.env): BACKUP_PASSPHRASE_FILE (required), BACKUP_DIR (default
# /var/backups/realtron), BACKUP_KEEP_DAYS (default 14), BACKUP_UPLOAD_CMD (optional; run as
# `sh -c "$BACKUP_UPLOAD_CMD" upload <file>` for the dump and its .sha256), COMPOSE_PROJECT.
set -Eeuo pipefail
umask 077
LOG_TAG=backup
# shellcheck source=common.sh
. "$(dirname "$0")/common.sh"

KEEP_DAYS=${BACKUP_KEEP_DAYS:-14}
[[ "$KEEP_DAYS" =~ ^[0-9]+$ ]] && [ "$KEEP_DAYS" -ge 1 ] || fail "BACKUP_KEEP_DAYS must be a whole number ≥ 1"
require_passphrase
mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"

started=$(now_iso)
name="realtron-$(date -u +%Y%m%dT%H%M%SZ).dump.gpg"
final="$BACKUP_DIR/$name"
partial="$final.partial"

on_error() {
  # set -E also runs this trap in the pipeline's subshells; only the main shell reports and exits.
  [ "$BASH_SUBSHELL" -eq 0 ] || return 0
  local line=$1
  trap - ERR
  rm -f "$partial"
  record backup failed "$started" "" "" "" "failed at backup.sh line $line"
  log "FAILED (line $line)"
  exit 1
}
trap 'on_error $LINENO' ERR

log "dumping to $final"
# shellcheck disable=SC2016  # $POSTGRES_* expand inside the db container, from its own environment
compose exec -T "$DB_SERVICE" sh -c 'exec pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom --no-password' \
  | gpg --batch --yes --quiet --pinentry-mode loopback --passphrase-file "$BACKUP_PASSPHRASE_FILE" \
        --symmetric --cipher-algo AES256 --compress-algo none --output "$partial"

# The archive must decrypt and be readable by pg_restore (catches truncation and a wrong key).
gpg --batch --quiet --pinentry-mode loopback --passphrase-file "$BACKUP_PASSPHRASE_FILE" --decrypt "$partial" \
  | compose exec -T "$DB_SERVICE" pg_restore --list >/dev/null

mv "$partial" "$final"
sha=$(sha256_of "$final")
size=$(size_of "$final")
echo "$sha  $name" > "$final.sha256"

detail="pg_restore --list ok"
if [ -n "${BACKUP_UPLOAD_CMD:-}" ]; then
  sh -c "$BACKUP_UPLOAD_CMD" upload "$final"
  sh -c "$BACKUP_UPLOAD_CMD" upload "$final.sha256"
  detail="$detail; uploaded"
else
  detail="$detail; NOT copied off this host (BACKUP_UPLOAD_CMD unset)"
  log "warning: BACKUP_UPLOAD_CMD is not set; the backup exists only on this host"
fi

find "$BACKUP_DIR" -maxdepth 1 -type f \( -name 'realtron-*.dump.gpg' -o -name 'realtron-*.dump.gpg.sha256' \) \
  -mtime +"$KEEP_DAYS" -delete
find "$BACKUP_DIR" -maxdepth 1 -type f -name 'realtron-*.partial' -mmin +60 -delete

trap - ERR
record backup ok "$started" "$name" "$size" "$sha" "$detail"
log "ok: $name ($size bytes, sha256 $sha)"
