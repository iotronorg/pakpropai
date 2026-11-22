# Deploy / Go‑Live Checklist for the M0 changes

Run this in order for **each** environment (staging first).

## 1. Before deploying
- [ ] **Back up the database.** The release contains data migrations: `is_sandbox` backfills, and in‑place encryption of secrets and national IDs.
- [ ] Generate a Fernet key:
  `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`
- [ ] Store it in the secret manager as `FIELD_ENCRYPTION_KEYS`, **and back it up**. Losing the key makes the encrypted columns unrecoverable.
- [ ] Optional: set `FIELD_HASH_KEY`. If you set it, never change it: it keys the webhook verify‑token lookup index.
- [ ] Add both to the web, worker and beat services. The web image runs `migrate` on start, and migration fails closed without the key (`core.E002`).

## 2. Deploy and migrate
- [ ] Run the migrations. The new ones are:
  - `properties 0020`, `leads 0019`, `whatsapp 0011`: sandbox backfill.
  - `sysconfig 0002`, `whatsapp 0012`, `organizations 0023`, `voice 0002`, `users 0012`, `agents 0013`, `verification 0008`: encryption.
- [ ] Recovery path: reversing a migration decrypts back to plaintext. It needs the same key, e.g. `python manage.py migrate users 0011`.

## 3. Verify the safe defaults
An existing row in `system_config` overrides the code defaults. Check each environment:

```sql
SELECT key, value FROM system_config
WHERE key IN ('scraper_search_enabled','feature_deal_lock','feature_voice_calls',
              'feature_syndication','semantic_cache_enabled');
```
- Values are encrypted now, so read them via the admin/API or `SystemConfigService.get()`, not raw SQL.
- Expected: all `false`, or no row at all.

## 4. Smoke checks
- [ ] Re‑verify the WhatsApp webhook from the Meta dashboard: the per‑org verify token now uses a lookup index.
- [ ] Consumer search no longer shows demo/platform listings or other orgs' sandbox listings.
- [ ] Listing a property, the agent hand‑off and audit generation via WhatsApp each ask for **YES** first.
- [ ] Audit summaries/PDFs show the "Not a valuation" notice.
- [ ] `/health/` returns 200. The CI deploy job uses `infra/scripts/health_check.py`.

## 5. CI secrets used by the deploy jobs
`AWS_GITHUB_ACTIONS_ROLE_ARN`, `AWS_REGION`, `ECR_API_REPO`, `ECR_WORKER_REPO`, `ECS_CLUSTER_NAME`, `ECS_PRIVATE_SUBNET_IDS`, `ECS_TASKS_SG_ID`, `BASE_URL`, `FRONTEND_URL`, `STAGING_URL`, `STAGING_DEPLOY_HOOK`, `STAGING_WA_VERIFY_TOKEN`.
- `ECR_FRONTEND_REPO` and `NEXT_PUBLIC_API_URL` are no longer used here: the frontend builds in its own repo.

## 6. Session‑4 changes (M1: OTP hashing, inbound resilience)
- [ ] Migration `users 0013_otp_code_hash` hashes outstanding OTPs and drops the plaintext column. It needs the same key as the encryption migrations. Reversing it invalidates outstanding OTPs; users just request a new one.
- [ ] Changing `FIELD_HASH_KEY` also invalidates OTPs issued in the previous 5 minutes.
- [ ] Workers: the inbound task now uses `acks_late`. Keep the Redis broker `visibility_timeout` above the longest retry countdown (default 1 h is fine).
- [ ] After any DB outage, check `python manage.py replay_whatsapp_dead_letters --count` and replay once the DB is healthy. Alert on the log line `WA inbound message dead-lettered`, and on `message LOST` (Redis was down too).

## 7. Session‑5 changes (M1: membership consolidation, ADR‑013)
- [ ] **Before** migrating, record the drift for the release notes: `python manage.py reconcile_memberships --show 100`. Expect non‑zero `missing_agent` (agents registered or added to a team since migration 0010) and possibly `orphan_agent` (agents removed from a team who **kept org access** — KI‑010).
- [ ] Migration `organizations 0024_backfill_missing_memberships` creates the missing owner/agent memberships (create‑only; reverse is a no‑op).
- [ ] After migrating, run `python manage.py reconcile_memberships` again: `missing_owner`/`missing_agent` should be 0.
- [ ] **Decide each `orphan_agent` and `extra_owner` row by hand.** These are never auto‑revoked: an orphan can be a legitimate invite to a second org, or an ex‑agent who must lose access. Revoke via `DELETE /api/v1/organizations/me/members/<id>/` or the admin.
- [ ] Optional: schedule `reconcile_memberships --fail-on-drift` as a daily job and alert on a non‑zero exit.
- [ ] JSON clients can now self‑register agents and invite members by phone. `PIIMaskingMiddleware` exemptions were added for `/api/v1/agents/register/` and `/api/v1/organizations/me/members/` (KI‑032).

## 8. Session‑5 changes (M1: transactional outbox, ADR‑014; KI‑033)
- [ ] Migration `core 0002_outbox_message` creates `outbox_messages` (schema only).
- [ ] **Beat** must be running: `relay-outbox` (30 s) and `purge-outbox` (daily) come from `CELERY_BEAT_SCHEDULE`, which `django_celery_beat` syncs on start. Check that both show in the admin's periodic tasks.
- [ ] Keep `OUTBOX_CELERY_QUEUE=default` (the default) unless **every** environment has a worker consuming `outbound`. The prod compose `worker` now consumes `default,outbound`; ECS task definitions outside this repo must be checked before switching.
- [ ] **Behaviour change:** WhatsApp OTPs, notifications, hot‑lead alerts, verification/listing alerts and deal notices will now actually be **sent** (KI‑033: they all failed silently before). Confirm the WhatsApp templates and credentials, and warn support about the sudden increase in outbound messages.
- [ ] Purge or restrict historical logs containing `[OTP FALLBACK]`: they hold plaintext OTP codes.
- [ ] Monitor: `outbox_messages` rows with `status='pending'` and `available_at` older than 5 min (relay not running), and `status='failed'` growth. Log lines: `outbox.dead`, `outbox.failed_permanent`.

## 9. Session‑6 changes (M1: search indexes, ADR‑015)
- [ ] Migration `properties 0021_pg_trgm_extension` runs `CREATE EXTENSION IF NOT EXISTS pg_trgm`. pg_trgm is a trusted extension (Postgres ≥ 13), so the DB owner can create it; on a locked‑down managed DB, have an admin run it first. The reverse keeps the extension.
- [ ] Migration `properties 0022_search_indexes` is **non‑atomic** and builds 4 indexes with `CREATE INDEX CONCURRENTLY`. Writes are not blocked, but the build takes time and I/O on a large `properties` table. Run it off‑peak. It cannot run inside a wrapping transaction (e.g. a pgbouncer *transaction*‑pooling connection used for migrations); use a direct connection.
- [ ] If the build is interrupted, Postgres leaves an `INVALID` index. Check with `SELECT c.relname FROM pg_index i JOIN pg_class c ON c.oid=i.indexrelid WHERE NOT i.indisvalid;`, then `DROP INDEX CONCURRENTLY <name>;` and re‑run `migrate`.
- [ ] **Behaviour change:** `GET /api/v1/properties/?search=` now matches every word as a prefix (`apart` finds "apartments") with the `simple` dictionary (no English stemming or stop‑word removal). A full reference number (`RT-LHR-2026-000000001`) is an exact match. Input with no letters or digits returns an empty list.
- [ ] New Python dependency `drf-spectacular==0.29.0`: rebuild web/worker images. `GET /api/v1/schema/` serves the OpenAPI 3.1 schema to staff/platform admins; set `OPENAPI_SCHEMA_PUBLIC=True` only where publishing the endpoint map is acceptable (e.g. staging). CI uploads `openapi.yaml` as the `openapi-schema` artefact (ADR‑016).
- [ ] Every response now carries `X-Request-ID` (an inbound id from the load balancer is kept when it is 8–64 chars of `[A-Za-z0-9._-]`). JSON log lines gain a `request_id` field; update log‑pipeline parsers or dashboards if they use a fixed field list. `/api/` JSON errors gain `error_info` and `request_id` (existing keys unchanged), and unrouted `/api/` URLs and 500s now return JSON instead of HTML (ADR‑017). Tell the frontend team.
- [ ] **Stricter query params (T‑1090c):** malformed or negative `limit`/`offset`/`top_n`/`hours` now return 400 with a field error. Before, they gave a 500 or a silent default. Unknown `type`/`legal_status` on `/api/v1/properties/` return 400 (before: an empty list); `min_price > max_price` and `min_score > 100` return 400; `?ref_no=` matches only a full reference number. The notifications and fraud‑alerts 400 bodies changed from `{"detail": "limit … must be integers."}` to the standard `{"limit": "Must be a whole number.", "error_info": …}`. Tell the frontend team.
- [ ] Migration `core 0003_idempotency_record` creates `idempotency_records` (schema only). Beat must run `purge-idempotency-records` (hourly). Optional `IDEMPOTENCY_TTL_HOURS` (default 24). Clients may now send `Idempotency-Key` on lead/appointment create, checkout, SEPA and deal‑lock initiate; a replay carries `Idempotent-Replayed: true` (ADR‑018). Tell the frontend team that retries of these POSTs should reuse the key.

## 10. Session‑7 changes (M1: Market entity, ADR‑019)
- [ ] Migrations:
  - `markets 0001/0002` create and seed `markets` (PK active; AE/GB/US beta; CA inactive). Existing rows are not overwritten.
  - `properties 0023` and `organizations 0025` add a nullable `market_id` column with two indexes. Index builds take a short write lock on large tables, so run off‑peak.
  - `properties 0024` and `organizations 0026` backfill `market_id` from `UPPER(country)`, in one `UPDATE` per market.
- [ ] After migrating, run `python manage.py market_backfill_report`. Investigate any `unmatched country` lines (typos, or countries without a market). `--apply` re‑fills NULLs after fixing countries; `--max-unmatched-pct 1` can gate a pipeline.
- [ ] `DEFAULT_MARKET` (env, default `PK`) sets the default country, currency and area unit for new records. Leave it as `PK` until a second market launches; an unknown code fails at the first model instantiation.
- [ ] Markets are edited in the admin (no delete; retire with `status`). Cached for 5 min, invalidated on save.
- [ ] `TIME_ZONE` is still `Asia/Karachi`: the UTC switch is T‑1050c and ships separately.
- [ ] **RBAC (ADR‑020):** org‑admin checks now read memberships only.
  - **Before** deploying, run `python manage.py reconcile_memberships --show 100`. Every developer who owns an org needs an active `owner` membership (`missing_owner` must be 0), or that developer loses admin access.
  - A `system_config` row `use_membership_rbac` is now ignored.
  - Members with `org_admin` membership and `user.role=agent` gain org‑admin access (intended).
  - The members API no longer grants `owner`, and only owners grant or remove `org_admin`.
  - Clients may send `X-Organization-ID` to act for another org they belong to. A foreign org gives 403 and a `cross_tenant` security event; alert on those events.
  - `/auth/otp/send/` rejects `purpose` other than `otp_login`/`registration_verify` (KI‑030).

## 11. Session‑8 changes (M1: membership contract, ADR‑021)
- No migrations or new settings.
- **New endpoints:**
  - `PATCH /api/v1/organizations/me/members/{id}/` changes a member's role;
  - `POST /api/v1/organizations/me/ownership/transfer/` (owner) and `POST /api/v1/organizations/{id}/ownership/transfer/` (platform admin) transfer ownership. The target must be an active `org_admin` with a developer account who owns no other org. The previous owner stays as `org_admin`.
- **Behaviour change:** every member invite, reactivation, role change, removal and transfer writes an `audit_logs` row. Expect a few rows per member change.
- **Prefer the transfer endpoint to editing `admin_user` in the Django admin.** The admin edit still works, but it deactivates the previous owner's membership (ADR‑013) and writes no audit row.
- High‑risk deal‑lock audit rows (`detail='high_risk_deal_lock'`) are now actually written (KI‑040).
- **Outbox (T‑1022a):** these now go through `outbox_messages` (kind `whatsapp.text`), so the `relay-outbox` beat job and a worker on `OUTBOX_CELERY_QUEUE` must be running: hot‑lead alerts, verification notices, new‑listing alerts, appointment reminders, stale‑lead reminders and client re‑engagement. `reminder_sent_at`/`follow_up_sent_at` now mean "queued". Failed rows show up as `status='failed'` with `last_error`.
- Property owners no longer get a repeat “Verification Approved/Rejected” message whenever a reviewed verification is saved again (KI‑042).
- **T‑1022b:** escrow expiry notices, the seller deal‑lock code (now only after AML screening, KI‑043), the audit companion text and the scraped‑listing follow‑up also go through the outbox. Campaigns and AI re‑engagement stay direct (rate‑limited).

## 12. Session‑8 change: UTC switch (T‑1050c, ADR‑022)
- `TIME_ZONE` and `CELERY_TIMEZONE` are now UTC. No migration.
- **Restart celery beat after deploy**, so the settings schedule re‑syncs into UTC crontab rows.
- In Django admin → Periodic tasks, check each crontab task:
  - `monthly-report-generation`: minute 5, every hour, day 1, UTC;
  - `run-reengagement-worker`: hours 1,7,13,19 UTC;
  - `sync-aml-sanctions`: Sunday 21:00 UTC.

  Delete stale duplicates only if beat created new rows alongside old PKT ones. Hand‑made tasks keep their stored zone.
- Django admin timestamps now display in UTC.
- Monthly reports are per‑org local months. Pakistan reports appear from about 05:05 PKT on the 1st (was 06:00).

## 13. Session‑8 change: listing schema v2 expand (T‑1070a, ADR‑023)
- **Migrations:**
  - `properties.0025`: additive columns. The NOT NULL ones have DB defaults, so there is no table rewrite and old code keeps inserting during a rolling deploy.
  - `properties.0026`: index built `CONCURRENTLY`, non‑atomic.

  Run `migrate` before or during the rollout.
- Existing listings become `purpose=sale`, `price_type=total`. Agents should correct any rental listings that were stored as sales.
- API additions are backward compatible: new optional fields, plus the list filters `purpose`, `min_bedrooms` and `min_bathrooms`.
