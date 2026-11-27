# Runbook: database backups, restore drills and restoring production

T‑6010 (M6), MVP launch checklist "Backup restore drill completed", TR‑17. Production Postgres is the `db` container on the server's Docker volume (ADR‑053): **if the server or its disk is lost, only backups that left the server can bring the data back.**

| What | How | When |
|---|---|---|
| Backup | `ops/backup.sh`: `pg_dump` inside the `db` container (same Postgres version), encrypted on the way out with `gpg` (AES‑256), checked with `pg_restore --list`, `.sha256` written, copied off the host by `BACKUP_UPLOAD_CMD`, local copies kept `BACKUP_KEEP_DAYS` (14) | Daily, 02:15 UTC (systemd timer) |
| Restore drill | `ops/restore_drill.sh`: checksum, decrypt, `pg_restore --exit-on-error` into a throwaway container with no network, table and row-count checks, time taken | Monthly, the 1st, 04:00 UTC; and before risky changes |
| Record | Each run is a `BackupRun` row (`manage.py record_backup_run`); `/metrics/` exposes the latest ones | Every run |

No plaintext dump is written to disk at any point. The files contain personal data in encrypted form; the key decides who can read them.

**RPO/RTO.** Daily dumps lose up to 24 hours of data (RPO ≤ 24 h). The drill reports how long a restore of the current database takes; add the time to provision a server and deploy for the RTO. Targets are the owner's to set (PRD `ASSUMPTION`); less than 24 h of loss needs WAL archiving (T‑6050).

## One-time setup (each server: staging and production)

1. **Tools:** `sudo apt-get install -y gnupg`. Docker and the deploy user exist already (deploy.md).
2. **Passphrase** (the backup key). Generate it on the server and **store a copy outside the server** (the owner's password manager or secret store). Without it no backup can be read; it is separate from `FIELD_ENCRYPTION_KEY`, which you also need to read the restored data (KI‑028).
   ```sh
   sudo install -d -m 700 -o deploy -g deploy /etc/realtron
   openssl rand -base64 48 | sudo -u deploy tee /etc/realtron/backup-passphrase >/dev/null
   sudo chmod 600 /etc/realtron/backup-passphrase
   ```
3. **Backup directory:** `sudo install -d -m 700 -o deploy -g deploy /var/backups/realtron` (on a disk with room for 14 days of dumps).
4. **Settings:** create `<DEPLOY_DIR>/backup.env` (mode 600, owned by the deploy user):
   ```sh
   BACKUP_PASSPHRASE_FILE=/etc/realtron/backup-passphrase
   BACKUP_DIR=/var/backups/realtron
   BACKUP_KEEP_DAYS=14
   # Off-host copy: the command gets the file as "$1" (called for the dump and its .sha256). Examples:
   # BACKUP_UPLOAD_CMD='rclone copy "$1" offsite:realtron-backups/production/'
   # BACKUP_UPLOAD_CMD='aws s3 cp "$1" s3://<bucket>/production/ --only-show-errors'
   # BACKUP_UPLOAD_CMD='scp -q "$1" backup@<other-host>:/srv/backups/realtron/'
   ```
   Until `BACKUP_UPLOAD_CMD` is set, every run says **"NOT copied off this host"** in its record. Give the off-host location its own retention (e.g. 35 daily + 12 monthly) and write-only credentials if the provider has them.
5. **Timers:** the deploy copies the units to `<DEPLOY_DIR>/ops/systemd/`. Edit `User=` and the `/srv/realtron` paths if yours differ, then:
   ```sh
   sudo cp <DEPLOY_DIR>/ops/systemd/realtron-*.{service,timer} /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable --now realtron-backup.timer realtron-restore-drill.timer
   ```
6. **First run, by hand:** `sudo systemctl start realtron-backup.service`, then `sudo systemctl start realtron-restore-drill.service`. Check `journalctl -u realtron-backup -u realtron-restore-drill -n 50`: both end in `ok:`. The drill's line has the row counts: compare them with the live system. That is the launch checklist's "restore drill completed": note the date and the time it took.

## Every day
Nothing, while no alert fires. `journalctl -u realtron-backup --since today` shows the run; the Django admin lists `Backup runs`.

## Alerts

### backupmissing
`BackupMissing` (critical): no successful backup in 26 hours (also fires if there has never been one).
1. `systemctl status realtron-backup.timer realtron-backup.service` and `journalctl -u realtron-backup -n 100`.
2. If the timer is not active: `sudo systemctl enable --now realtron-backup.timer`.
3. If the run fails, see [backupfailed](#backupfailed). If it succeeds but the alert stays, the run could not be recorded (the `web` container was down): the log says `could not record`. Start `web` and run the backup again.
4. Run `sudo systemctl start realtron-backup.service` and wait for `ok:`; the alert clears at the next scrape.

### backupfailed
`BackupFailed` (warning): the newest run failed. The log line `FAILED (line N)` points at the step:
- **pg_dump:** is `db` up? `docker compose … ps db`. Disk full on the `db` volume?
- **gpg / passphrase:** the script names the problem (file missing, wrong mode, `gpg` not installed).
- **pg_restore --list:** the archive did not decrypt or is truncated; usually disk full in `BACKUP_DIR` (`df -h /var/backups/realtron`).
- **upload:** run `BACKUP_UPLOAD_CMD` by hand on the `.dump.gpg` to see the provider's error (credentials, bucket, network). The local file of that run is kept.

### restoredrilloverdue
`RestoreDrillOverdue` (warning): no successful drill in 35 days. Run `sudo systemctl start realtron-restore-drill.service`. If it fails:
- **checksum mismatch:** the file changed after it was written (disk fault, tampering). Drill an older file (`ops/restore_drill.sh /var/backups/realtron/<file>`) and investigate the disk.
- **pg_restore failed / table missing:** the backup is not usable. Treat it as an incident: take a fresh backup, drill it at once, and find out since when backups were bad.

### backupstatusabsent
`BackupStatusAbsent` (warning): `/metrics/` has no backup samples for 30 minutes. The API cannot read `backup_runs` (database trouble; see `DatabaseDown`), or the running build is older than T‑6010. `MetricsTargetDown` fires instead if the API cannot be scraped at all.

## Restoring production

Only when the live data is lost or damaged. It is reversible until the last step: the damaged database is kept under another name.

1. **Decide which backup.** The newest one before the damage. If it is only off-host, copy it and its `.sha256` into `BACKUP_DIR` first, then drill it: `ops/restore_drill.sh /var/backups/realtron/<file>`. Do not restore a file that fails the drill.
2. **Stop the application** so nothing writes (the database stays up), in `DEPLOY_DIR`:
   ```sh
   C="docker compose -p realtron --env-file .env -f docker/docker-compose.prod.yml"
   $C stop web worker worker-hr beat
   ```
3. **Keep what is there:** if the database still answers, run `ops/backup.sh` once more (the damaged state, for later investigation).
4. **Restore into a new database** next to the live one:
   ```sh
   . ./backup.env
   $C exec -T db sh -c 'createdb -U "$POSTGRES_USER" realtron_restore'
   gpg --batch --pinentry-mode loopback --passphrase-file "$BACKUP_PASSPHRASE_FILE" --decrypt /var/backups/realtron/<file> \
     | $C exec -T db sh -c 'pg_restore -U "$POSTGRES_USER" -d realtron_restore --no-owner --exit-on-error'
   ```
5. **Swap the names** (the old database is kept as `realtron_before_restore`):
   ```sh
   $C exec -T db sh -c 'psql -U "$POSTGRES_USER" -d postgres -v ON_ERROR_STOP=1 \
     -c "ALTER DATABASE \"$POSTGRES_DB\" RENAME TO realtron_before_restore" \
     -c "ALTER DATABASE realtron_restore RENAME TO \"$POSTGRES_DB\""'
   ```
6. **Start the current build** the usual way, which migrates the restored schema forward and checks health: `IMAGE_TAG=$(cat .deployed_tag) WEB_IMAGE=… WORKER_IMAGE=… sh remote_deploy.sh` (deploy.md, "Rolling back by hand").
7. **Check:** sign in, open recent listings and leads; compare counts with the drill line. Tell users what period was lost (from the backup's time to the incident). Follow [data-incident.md](data-incident.md) if personal data was affected.
8. After a few days without problems: `$C exec -T db sh -c 'dropdb -U "$POSTGRES_USER" realtron_before_restore'`.

**Lost server:** provision a new one (deploy.md "One-time setup"), restore `.env`, `FIELD_ENCRYPTION_KEY`/`FIELD_HASH_KEY` and the backup passphrase from where they are kept off the server, deploy once (empty database), then steps 1–7 with the off-host backup.

## Notes
- **Erasure (KI‑105):** a person deleted today stays in backups until they age out: 14 days on the server, plus whatever retention the off-host copy has. Say so in the privacy notice; after a restore, re-apply erasures done since the backup's time (`compliance` RTBF requests list them).
- **Changing the passphrase:** write the new one, keep the old one with the backups made under it until they have aged out everywhere.
- **Staging** runs the same timers, so its alerts behave like production's.
