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
- [ ] `/health/` returns 200 with the deployed `version`. The deploy script (`scripts/deploy/remote_deploy.sh`) waits for it and rolls back otherwise.

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

## 14. Session‑8 change: size rename (T‑1070b, ADR‑023)
- **Migrations:**
  - `properties.0027`: nullable columns.
  - `properties.0028`: batched backfill, non‑atomic, reverse no‑op.
- **After every pre‑v2 pod has stopped:** run `python manage.py sync_listing_size --dry-run`, then `python manage.py sync_listing_size`. This copies rows that old code wrote only to `area_*`.
- **API:** `size_value`/`size_unit`/`size_m2`/`plot_size_m2` added. `area_marla`/`area_unit`/`area_sqm` still work, are marked deprecated, and are removed only in T‑1070g. Sending conflicting `size_value` and `area_marla` returns 400.
- **Behaviour change:** search size matching and price verdicts now use real size. Kanal and sqft listings may get different `ai_score`/price verdicts on rescoring (they were wrong before, KI‑049).

## 15. Session‑8 change: price history (T‑1070c, ADR‑023)
- **Migrations:**
  - `properties.0029`: new table.
  - `properties.0030`: one baseline row per priced listing, idempotent; reverse deletes only backfill rows.

  The backfill is one `INSERT … SELECT`. On a very large `properties` table, run it in a quiet window.
- **New endpoint:** `GET /api/v1/properties/{id}/price-history/`, read‑only, with the listing's visibility.
- Price changes made by pre‑v2 pods during the rollout are not in the history.

## 16. Session‑9 change: listing taxonomy and amenities (T‑1070d, ADR‑023)
- **Migrations:** all additive and rolling‑safe.
  - `properties.0031`: nullable `category`/`subtype` columns, plus the new `amenities` and `property_amenities` tables.
  - `properties.0032`: batched backfill of `category` from `property_type`. Non‑atomic; reverse is a no‑op.
  - `properties.0033`: seeds 35 amenities. Idempotent, so admin edits survive a re‑run. Reverse removes only seeded keys that no listing uses.
  - `properties.0034`: `CREATE INDEX CONCURRENTLY` on `(category, is_active)`. Non‑atomic; run it on a direct DB connection, not through PgBouncer in transaction mode.
- **After every pre‑v2 pod has stopped:** run `python manage.py sync_listing_taxonomy --dry-run`, then `python manage.py sync_listing_taxonomy`. Rows that old code wrote with a new `property_type` get their category re‑derived and their subtype cleared.
- **API (additive):**
  - `category`, `subtype` and `amenities` (a list of keys) on listing create, update and read;
  - list filters `category`, `subtype` and `amenities=a,b` (all must match);
  - `GET /api/v1/properties/taxonomy/?market=&lang=`.

  `property_type` and `?type=` still work and are marked deprecated. Sending `property_type` and `category` that disagree returns 400.
- **Admin:** amenities are curated under *Properties → Amenities*. Retire one with `is_active=False`; an amenity in use cannot be deleted.

## 17. Session‑9 change: market attribute schema (T‑1070e, ADR‑023)
- **Migrations:**
  - `markets.0003`: adds the `listing_schema` column with `db_default {}`.
  - `markets.0004`: seeds PK/AE/GB schemas, and only fills empty ones.
- **Behaviour change:** when listing `attributes` (or `country`) are written in PK, AE or GB, unknown keys and invalid values now return 400. Before this, any object up to 50 keys/8 KB was accepted. **Check before deploy:** `SELECT market_id, count(*) FROM properties WHERE attributes <> '{}' GROUP BY 1`. Stored values are not rewritten. They are re‑checked only when a client sends `attributes` or changes `country`.
- **Admin:** edit schemas under *Markets*. Malformed schemas are refused by `Market.clean()`. The market cache key prefix changed to `market:v2:`, and the first read after deploy repopulates it.

## 18. Session‑9 change: stored search document (T‑1070f, ADR‑015 addendum)
- **Migrations:**
  - `properties.0035`: nullable `search_tsv` column, plus the trigger function `properties_search_tsv_refresh` and trigger `properties_search_tsv_trg`.
  - `properties.0036`: batched backfill, non‑atomic; each batch takes short row locks.
  - `properties.0037`: `CREATE INDEX CONCURRENTLY prop_search_tsv_gin`. Run it on a direct DB connection.
- **Ordering:** run all three before new pods serve traffic. New code queries the column, so rows not yet backfilled would be missed.
- The legacy `prop_fts_gin` index stays for old pods and is dropped in T‑1070g. Disk: expect roughly the size of the existing FTS index again, plus the column.

## 19. Session‑9 change: org invitations (T‑1032c, KI‑041)
- **Migration:** `organizations.0027` (new `organization_invites` table).
- **API change:** `POST /api/v1/organizations/me/members/` now returns a **pending invite**, and the user joins only after `POST /api/v1/organizations/invites/{id}/accept/`. **Frontend:** show pending invites (`GET /organizations/invites/`) with accept/decline, and an org admin view (`GET/DELETE /organizations/me/invites/`).
- The invitee gets a WhatsApp notice through the outbox (the outbox worker must be running).
- **Optional:** `ORG_INVITE_TTL_DAYS` (default 7).

## 20. Session‑9 change: PII masking allow‑list and market follows country (T‑1033, KI‑050)
- **PII masking:** `/api/v1/agents/register/` and `/api/v1/organizations/me/members/` are no longer exempt as whole paths. Only the listed structured fields pass unmasked (`PRIVACY_MASK_FIELD_ALLOWLIST` in settings), so free text in those bodies (e.g. agent `bio`) is now masked. Agent profile edits (`/agents/me/`, `/agents/{id}/`) and org contact edits now keep real phone/email/ID values; before, they were masked.
- **Listings:** changing a listing's `country` now moves it to that market (`market_id`). No migration is needed. Run `market_backfill_report` if you want to check existing rows.

## 21. Session‑9 change: location hierarchy (T‑1060a, ADR‑024) and branding
- **Migrations:**
  - `locations.0001/0002`: new table plus seed.
  - `properties.0038`: nullable FK plus a precision column with a DB default. The FK check scans the table once, while every value is NULL.
  - `properties.0039`: concurrent index; run it on a direct DB connection.
- **After rollout:** run `python manage.py match_listing_locations` (batched; prints coverage). Then work the review queue in *Admin → Locations* (filter "verified: No"): verify the real places and merge the misspellings. Restart celery beat so it picks up the hourly `link-unmatched-listings` job.
- **Branding:** user‑facing text is now RealTron. The WhatsApp OTP template is approved by Meta, so **edit and re‑submit its body text ("Your RealTron AI code is …") in Meta Business Manager**. The code only sends parameters.

## 22. Session‑9 change: listing schema contract, part 1 (T‑1070g‑1)
- **Order matters:** run `migrate` (`properties 0040` trigger, `0041` non‑atomic repair) **before** rolling out the new pods. Pods of the previous release keep working with these migrations applied. New pods without the trigger would fail inserts (NOT NULL `area_unit`/`property_type`).
- `sync_listing_size` and `sync_listing_taxonomy` no longer exist. 0041 performs their final repair, and the trigger prevents new drift. The earlier steps in §11/§13 that call them apply only to releases before this one.
- After the rollout, run `python manage.py check_listing_contract`. It must print `OK`.
- **Next release (T‑1070g‑2):** follow `apps/properties/contract_migrations/README.md`. Re‑run the check; move the staged 0049/0050 into `migrations/`; migrate on a direct DB connection (0050 is CONCURRENTLY). Rollback must use the g‑2 build: `migrate properties 0041`.
- **API:** no breaking change. `area_marla`/`area_unit`/`area_sqm`/`property_type` are still returned and accepted (deprecated aliases). Tell API clients to move to `size_*`/`category`.

## 23. Session‑9 change: geo search and coordinate privacy (T‑1060b)
- **Migrations:**
  - `properties 0042`: two nullable columns plus a trigger; no rewrite.
  - `properties 0043`: non‑atomic batched backfill.
  - `properties 0044`: `CREATE INDEX CONCURRENTLY`; use a direct DB connection.
- **No PostGIS needed.** With `GEO_POSTGIS_ENABLED=false` (the default), radius search is plain SQL using the btree index.
- **When PostGIS exists in a DB:**
  1. Run `python manage.py geo_setup` (creates the extension and the GiST index concurrently; needs a role that may `CREATE EXTENSION`).
  2. Run `geo_setup --check`.
  3. Set `GEO_POSTGIS_ENABLED=true`.
  4. If 0042 is ever rolled back and re‑applied, re‑run `geo_setup`.
- **Privacy:** private‑seller and approximate listings expose only coordinates snapped to ~1 km, and only those are searchable. Exact coordinates are shown to the owner, the listing's organisation and staff.
- **Infra (dev/CI only):** test runner, CI and the dev `docker-compose.yml` now use `postgis/postgis:15-3.4-alpine` (amd64; emulated on Apple Silicon). `docker/docker-compose.prod.yml` is unchanged; switching production to a PostGIS image is your decision.

## 24. Session‑9 change: geocoding (T‑1060c)
- **Migrations:** `locations 0003` (additive) and `geo 0001` (trigger v2, plus a non‑atomic re‑fire; no rows qualify until locations are geocoded).
- **Off by default.** To enable:
  1. Create a Google Cloud key restricted to the Geocoding API (and to the servers' egress IPs).
  2. Set `GOOGLE_MAPS_API_KEY` as a secret, `GEOCODING_ENABLED=true`, and `GEOCODING_DAILY_BUDGET` (default 500 requests/day).
  3. Run `python manage.py geocode_locations --dry-run`, then `geocode_locations --limit 200`.
  4. Review *Admin → Locations*, filtered on geocode status partial/rejected/zero. Enter coordinates by hand where needed; these are kept as `manual`.
  5. Restart celery beat (daily `geocode-locations`).
- **Owner/legal:** confirm Google Maps Platform terms for the 30‑day coordinate cache (handled by re‑geocoding) and for display on non‑Google maps (a frontend constraint).

## 25. Session‑9 change: listing lifecycle (T‑1080, ADR‑025)
- **Migrations:**
  - `properties 0045`: additive, with the trigger and a CHECK constraint added NOT VALID.
  - `0046`: non‑atomic batched backfill (inactive → archived; `published_at` = `created_at` for active listings), then `VALIDATE CONSTRAINT`.
  - `0047`: `CREATE INDEX CONCURRENTLY`; use a direct, non‑pooled DB connection.
- **Migrate before rollout.** Old pods keep working, because the trigger maps their `is_active` writes.
- **Behaviour change:** owners and listing agents see their own drafts and archived listings through detail, `?status=` and `mine/`. Public lists are unchanged.
- **Optional, per market** (*Admin → Markets → settings*):
  - `"listing_review_required": true` sends new listings to `pending_review`; approve them with the admin action.
  - `"listing_ttl_days": 90` makes listings published after that point expire, with a reminder 7 days before.
  - Restart celery beat (hourly `expire-listings`).
- **Staged T‑1070g‑2** is now `contract_migrations/0049`/`0050` (renumbered again in T‑1110), depending on the latest real migration.

## 26. Session‑9 change: MFA (T‑1011, ADR‑026)
- **Migration:** `users 0014`, additive (`users.mfa_enabled`, `user_mfa_devices`, `user_mfa_recovery_codes`).
- **Frontend contract:** a login response may now be `{mfa_required, mfa_token}` (show a code prompt, then `POST /auth/mfa/verify/`) or, when enforced, `{mfa_enrollment_required, mfa_token}` (setup → QR → confirm, then show the recovery codes once). The header `X‑MFA‑Enrollment‑Required: true` means "prompt to enrol".
- **Rollout:**
  1. Deploy with `MFA_ENFORCE=false`.
  2. All platform admins and org owners/admins enrol (*Admin → Users*, filter on `mfa_enabled`).
  3. Set `MFA_ENFORCE=true`.
  Staff who haven't enrolled by then cannot log in to the Django admin until they enrol in the app.
- **Support:** lost device → recovery code; no codes left → *Admin → Users → Reset MFA* (audited), after verifying the person's identity out of band.
- `MFA_ISSUER` (default `RealTron`) is the name shown in authenticator apps.

## 27. Session‑9 change: image upload hardening (T‑1110, ADR‑027)
- **Migrations:** `properties 0048` and `agents 0014`, both additive or `upload_to` only.
- **API:**
  - Upload responses now include `thumbnail_url`, `alt_text`, `width`, `height`.
  - New `PATCH /properties/{id}/images/{image_id}/` (caption, alt text) and `POST /properties/{id}/images/reorder/`.
  - The per‑listing limit is 20 (was 10); input files may be up to 10 MB (was 5) and are stored downscaled.
  - A request with one bad file now rejects all files, with errors keyed `images[i]`.
- **After deploy:** run `python manage.py sanitize_images --dry-run`, then `sanitize_images --limit 500 --include-profiles` (repeat until 0 remain). Until then, older listing photos may still contain GPS metadata.
- **Reverse proxy:** make sure the request body limit (e.g. nginx `client_max_body_size`) allows a multi‑image upload (20 × 10 MB) or a lower, deliberate cap.

## 28. Session‑9 change: LLM router and prompt registry (T‑1120, ADR‑028)
- **Migration:** `ai 0003` adds four columns to `ai_aiinteraction`, with DB defaults and no backfill.
- **Behaviour:** unchanged by default (`AI_PROVIDER_ORDER` = `AI_BACKEND`).
  - Prompts no longer use Pakistan‑specific wording for non‑PK markets; for PK organisations they say "Pakistan" through the market name.
  - The lead summary no longer sends the lead's phone number to the model.
- **Optional failover:**
  - Set `AI_PROVIDER_ORDER=gemini,openai` (or `…,local`).
  - For `openai`, also set `AI_OPENAI_BASE_URL`, `AI_OPENAI_API_KEY` (in the secret store) and `AI_OPENAI_MODEL`.
  - **This is a paid provider: the owner must decide before it is enabled.** Check its data‑processing terms, because prompts contain lead conversations.
- **Tuning:** `AI_TIMEOUT_S` (30), `AI_BREAKER_FAILURES` (5), `AI_BREAKER_SECONDS` (120).
- **Dashboards:** group `AIInteraction` by `prompt_name, prompt_version` to compare prompt versions, and by `provider` to watch failover.

## 29. Session‑9 change: membership phase C, release 1 (T‑1032b‑1, ADR‑013 addendum)
- **Migrations:**
  - `organizations 0028` (help text only).
  - `agents 0015`: adds the nullable `agent_org_memberships.membership_id` FK with a one‑UPDATE backfill (admin‑written, small table), removes `AgentOrganizationMembership` from the state, and drops that legacy table's two FK constraints. No table or row is dropped.
- **Behaviour:**
  - The org API's `admin_user`, `admin_phone` and `admin_name` now show the owner membership.
  - A developer's `GET /organizations/` lists every org they own.
  - `/organizations/me/` uses the active org (`X-Organization-ID`) when the caller owns it, else their first owned org.
  - Ownership can be transferred to a user who already owns another org.
  - A freelancer removed from an org stops receiving its leads even if their collaboration terms are still `active`.
- **After deploy:** run `python manage.py reconcile_memberships`, then `--apply`, and resolve the report‑only categories. This is needed before T‑1032b‑2 (see `apps/organizations/contract_migrations/README.md`).
- **Next release (T‑1032b‑2):** move the staged files and make the listed code removals in the same change. Rehearse the rollback (`migrate organizations 0028`, `migrate agents 0015`) on staging.

## 30. Session‑9 change: lead/session sandbox flag (T‑1040, ADR‑002 addendum)
- **Migrations:** `leads 0021` and `whatsapp 0013` are data‑only backfills (batched by 5 000, non‑atomic, safe to re‑run). They only flip `is_sandbox` to `False`.
- **Behaviour:** leads and sessions with no organization are now `is_sandbox=False`, and moving a lead or session into an org re‑derives the flag. No query filters leads or sessions on this flag today, so nothing becomes newly visible.

## 31. Session‑9 change: explicit tool loop (T‑2010, ADR‑029)
- **No migration.**
- **Behaviour:**
  - The model no longer runs tools inside the Gemini SDK; the app does, for at most 5 model calls per turn (`AI_MAX_TOOL_STEPS`).
  - Agent‑contact and audit replies are verbatim tool output on every provider.
  - Token usage now includes every round of a multi‑tool turn, so budgets fill faster than before: this is the true cost.
- **Watch after deploy:** `AIInteraction.output_data.tool_calls` (statuses `rejected` or `error`) and the `ai.tool` log lines. A rise in `rejected` / `invalid_args` points to a schema or prompt problem.

## 32. Session‑10 change: durable confirmations (T‑2012, ADR‑030)
- **Migration:** `ai 0004` (new table). `FIELD_ENCRYPTION_KEYS` must be set.
- **Behaviour:** agent hand‑off and literal audit requests now ask for YES; a bare YES counts only right after the summary. Proposals pending in the old cache are dropped at deploy.
- **Settings:** `AI_PENDING_ACTION_TTL_MIN` (30), `AI_PENDING_ACTION_RETENTION_DAYS` (90). Restart celery beat (`purge-pending-actions`).
- **API:** `GET /api/v1/pending-actions/`, `POST …/{id}/confirm/`, `POST …/{id}/decline/`.

## 33. Session‑10 change: AI traces and cost (T‑2011, ADR‑031)
- **Migration:** `ai 0005` (new tables).
- **Before relying on costs:** add a `ModelPrice` row in the admin for every model in use (`GEMINI_MODEL`, `AI_OPENAI_MODEL`, the local model at 0), from the provider's current price list.
- **API:** `GET /api/v1/ai/costs/`. In `/ai/token-usage/`, `estimated_usd_cost` and `estimated_savings_usd` can now be null.
- **Budgets:** Gemini thinking tokens now count as output, so budgets may fill faster.
- **Settings:** `AI_TRACE_RETENTION_DAYS` (400). Restart celery beat (`purge-ai-traces`).

## 34. Session‑10 change: grounding checker v2 (T‑2013, ADR‑032)
- **Migration:** `ai 0006` (two columns on `ai_traces`).
- **Behaviour:** a reply with wrong listing facts costs one extra model call; after a second failure the user gets our records only. Own inventory in the prompt no longer includes prices.
- **Watch after deploy:** `AITrace.grounding_result` (`corrected`, `fallback`, `error`) and `grounding_codes`; `ComplianceViolation` type `ungrounded_reply`. A high `fallback` rate points to false positives.
- **AML (KI‑060):** review WhatsApp sessions of buyers blocked earlier; the block may not have applied.

## 35. Session‑11 change: durable conversations (T‑2020, ADR‑033)
- **Migrations:** `conversations 0001`, `ai 0007` (additive). Existing Redis history is not copied: open chats lose their last 24 h of context once.
- **Settings:** `CONVERSATION_RETENTION_DAYS` (365; confirm per market with legal), `AI_DAILY_MESSAGE_LIMIT` (`200/day`). Restart celery beat (`purge-conversations`).
- **Encryption:** message text uses `FIELD_ENCRYPTION_KEYS`; losing the keys loses the chats.
- **Workers (KI‑065):** choose threaded or ASGI workers before exposing web chat; disable proxy buffering for `/api/v1/conversations/*/messages/`.
- **Privacy notice:** chats are now kept in the database; update the notice before launch.

## 36. Session‑12 change: search v2 (T‑2030, ADR‑034)
- **Migration:** `conversations 0002` (one column with a database default).
- **Behaviour:** the assistant uses the new search tools (system prompt v3); portal scraping no longer runs from chat even when enabled. Review replies with a real provider before launch.
- **API:** `POST /api/v1/search/` is public, 30/min per IP or user.
- **Optional:** set `Market.settings["ranking_weights"]` per market.
- **Security (KI‑067):** array JSON bodies no longer crash the security middleware.

## 37. Session‑13 change: public listing API (T‑2031, ADR‑035)
- **No migration.** New public endpoints under `/api/v1/public/listings/`.
- **Setting:** `PUBLIC_LISTINGS_RATE` (`120/min` per IP). A server‑rendered frontend shares one IP: raise it or cache in front.
- **Proxy:** per‑IP limits need the real client IP behind the proxy.
- **Before launch:** run `manage.py sanitize_images` for images from before T‑1110 (KI‑071) and have the field list reviewed for privacy.

## 38. Session‑14 change: enquiries, threads, email (T‑2050, ADR‑036)
- **Migrations:** `enquiries/0001`, `leads/0022` (adds a nullable column and index to `conversation_messages`; plan for a short lock on a large table).
- **Email:** set `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `EMAIL_USE_TLS`, `DEFAULT_FROM_EMAIL`; configure SPF/DKIM/DMARC. Without `EMAIL_HOST` no email is sent. Port 587 with TLS is assumed.
- **`FRONTEND_URL` is now a real setting.** Before, code read it but no settings file defined it, so payment return links fell back to localhost. Check it in production.
- **Deploy workers with the web tier:** older workers do not know the `notification.email` outbox kind.
- **Frontend:** show the consent text and send `contact_consent: true`; pages for `/threads/<id>`.
- **Before launch:** audit old CRM messages (KI‑073), decide KI‑074, approve a WhatsApp template (KI‑075), have the consent text reviewed for each market.
- **Prompt:** `agent.system` is version 4; run the eval suite with a live model.

## 39. Session‑15 change: viewings (T‑2060, ADR‑037)
- **Migrations:** `leads/0023` (columns with database defaults, so old workers keep working), `viewings/0001`.
- **Behaviour change:** dashboard bookings that overlap another active appointment of the agent are refused with 409; durations are limited to 5–480 minutes.
- **Agents must enter their hours** (`PUT /api/v1/availability/`), or their listings show no slots.
- **Organization managers** now see visits by the lead's organization, not by the agent's current one.
- **Beat:** the existing 15‑minute `appointment-reminders` job also sends day‑before reminders and feedback requests.
- **Prompt:** `agent.system` is version 5; run the eval suite with a live model.

## 40. Session‑16 change: favourites, saved searches, preferences (T‑2040, ADR‑038)
- **Migrations:** `preferences/0001`, `notifications/0004` (column with a database default), `compliance/0004` (choices only).
- **Beat:** restart it: `saved-search-alerts` (15 min) and `purge-stale-preferences` (daily). Deploy workers before beat.
- **Settings:** `PREFERENCE_RETENTION_DAYS` (180). `FRONTEND_URL` is used in the stop link.
- **Frontend:** `/saved-searches/<id>?stop=<token>` must POST the token to `/api/v1/saved-search-stop/` (no sign‑in); send `alert_consent: true`; show the memory consent text.
- **Before launch:** legal review of the memory and alert consent texts per market; WhatsApp template (KI‑078).
- **Prompt:** `agent.system` is version 6; run the eval suite with a live model.

## 41. Session‑17 change: comparison and FX (T‑2041, ADR‑039)
- **Migration:** `markets/0005` (new table).
- **FX:** choose a source (KI‑081), then import daily: `manage.py import_fx_rates rates.csv --as-of YYYY-MM-DD --source NAME`. `FX_MAX_AGE_DAYS` is 7.
- **Frontend:** show "approximate" with the `fx` date and source next to converted prices.
- **Prompt:** `agent.system` is version 7; run the eval suite with a live model.

## 42. Session‑18 change: moderation, exports, CRM privacy (T‑2014, ADR‑040/041)
- **Migrations:** `moderation/0001`–`0002` (queues listings already waiting), `compliance/0005` (new table).
- **Staffing:** name moderators (super, ops or compliance admins) and agree the deadline (`MODERATION_SLA_HOURS`, 24). Private listings are not public without them.
- **Beat:** restart it: `moderation-overdue`, `purge-data-exports`.
- **Web tier:** `Procfile` now runs threaded gunicorn (2×8, 120 s); size the database connections for 16 web threads and load‑test before launch. The platform's own start command must match.
- **Cloudinary:** delete old `gdpr_exports/` files (KI‑084).
- **Frontend:** moderation screens, report button (`/api/v1/listing-reports/`), appeal form, export download; a lead's `phone` can be null (`contact_withheld`).
- **Prompt:** `agent.system` is version 8; run the eval suite with a live model.

## 43. Session‑19 change: FAQ answers (T‑2016, ADR‑042)
- No migration. `semantic_cache_enabled` stays off until the answers were reviewed with a live model per market; then it may be set to `true`.
- Market knowledge must be current: FAQ answers quote its rates.

## 44. Session-20 change: kill switch and evals (T‑2070, ADR‑043)
- **Migration:** `ai 0008` (new table; additive).
- **Runbook:** name who may stop the assistant (super, AI or ops admin). `POST /api/v1/ai/kill-switches/` or `python manage.py ai_kill_switch stop --market PK --reason "…"`; release with the id. Rehearse it on staging.
- **Before release:** grow and review the golden set, create the GitHub environment `ai-evals` with `EVAL_GEMINI_API_KEY`, run the "AI evals (live model)" workflow, review and commit `apps/ai/evals/baseline.json`, archive the reports.
- **Review:** have Urdu speakers check the paused notices (`apps/ai/kill_switch.py`) and the seed cases.
- **Frontend:** confirming an action returns 409 "This action is paused." during a stop.

## 45. Session-21 change: third-party text (T‑2071, ADR‑044)
- No migration. Nine prompts have new versions; run the live evals (T‑2072) before release.
- **Behaviour:** the assistant no longer reads out phone numbers or links found in a listing's text, including an organization's own. Listings that address the assistant wait for moderation.
- Watch traces for guard reason `indirect_injection` and grounding code `contact` during the beta.

## 46. Session-22 change: thread photos, block, report (T‑2051, ADR‑045)
- Migration `enquiries 0002` (three new tables, no data change).
- Proxy body limit: allow 16 MB on `/api/v1/threads/` (3 photos of 5 MB).
- Moderators: new queue `moderation/threads/`, same deadline (`MODERATION_SLA_HOURS`). Tell users in the privacy notice that reporting a thread lets a moderator read it.
- Decide a retention period for photos and reports (KI‑095).

## 47. Session-23 change: end-to-end journey (ADR‑046)
- **Worker queues (KI‑096):** confirm in the hosting console that the worker start command reads `default,high-resource,outbound` (or that separate workers do).
- Leave `WA_API_URL` unset in production: access tokens are sent to it.
- **Staging run (MVP 1):** `E2E_SEED_DATABASE=<db> E2E_AGENT_PHONE=<your phone> python scripts/e2e/seed.py`, then `scripts/e2e/consumer_journey.py` with the variables in its header, then `seed.py --remove`. The seeded listings are public while they exist.

## 48. Session-24 change: form masking and provider callbacks (ADR‑047)
- No migration. Text sent as a form is masked like JSON: check any client that posts forms with contact fields outside the allow-list.
- Stripe billing callbacks need `stripe_webhook_secret` in the system configuration; without it they are refused.
- Before enabling Stripe, Jumio or Onfido, wire their settings (KI‑098) and send a test callback from the provider's console.

## 49. Session-25 change: listing visibility (ADR‑048)
- No migration. Expired listings disappear at once. Set a market to `inactive` to hide its listings everywhere.
- `GET /properties/{id}/`: `owner_phone`, `raw_docs` and `ai_analysis` are `null` for consumers. Check the frontend does not need them (contact goes through enquiries).
- Alert on the log `saved_search.backlog`.

## 50. Session-26 change: data protection (ADR‑049)
- **Back up the database and the encryption keys first.** Six migrations encrypt stored values and re-key hashes (the re-keying cannot be undone). Stop the old web and worker processes while they run: old code cannot read the new columns.
- `FIELD_ENCRYPTION_KEYS` is required. Do not change `FIELD_HASH_KEY` on a system that already has data: WhatsApp verify tokens, recovery codes, the blacklist and sanction matching depend on it. If it is unset, it is derived from the first encryption key, so set it (to a fixed value) only on a new system, or before any key rotation with a plan to re-create those hashes.
- Afterwards run `python manage.py scan_plaintext` on production; it prints counts only and must find nothing. If the cache was unreachable, save each blacklist entry once.
- Deploy beat (new hourly `process_data_requests`). Alert on `data_request.overdue`.
- Confirm `DATA_REQUEST_SLA_DAYS` (30).

## 51. Session-27 change: SLO metrics, alerts and dashboards (ADR‑050)
- Migrations `notifications 0005` (column with a database default; additive) and `0006` (`CREATE INDEX CONCURRENTLY`, `atomic = False`: run it on a direct database connection, not through PgBouncer in transaction mode).
- **Workers serve metrics.** The worker image now sets `PROMETHEUS_MULTIPROC_DIR=/tmp/prometheus-multiproc` and `WORKER_METRICS_PORT=9808`. Prometheus must scrape every worker container on port 9808 (job `realtron_worker`) as well as `web:8000/metrics/` (job `realtron_api`). On ECS, add the port to the task definitions and a scrape target per task (service discovery or a managed Prometheus). Until this is done `WorkerMetricsAbsent` fires, and the AI reply, webhook and notification SLOs read nothing.
- **Several web processes** (gunicorn in the `Procfile`) need `PROMETHEUS_MULTIPROC_DIR` too; the `Procfile` sets one and empties it at start. Daphne (`entrypoint.web.sh`) is a single process and needs none.
- `PROMETHEUS_ALLOWED_IPS` now accepts CIDR ranges (`10.0.0.0/8`), as its comment always said.
- Load `ops/prometheus-alerts.yaml` (55 rules) and keep **at least 35 days** of Prometheus data: the SLO dashboard's 30-day figures need it.
- **Choose where alerts go** (KI‑108): `ops/alertmanager.yml` has no receiver yet, so alerts are only visible in Alertmanager and Grafana.
- Import `ops/grafana/dashboards/realtron-slo.json` and `realtron-overview.json`, or mount `ops/grafana/provisioning` as `docker-compose.yml` does. The dashboards use the data-source uid `prometheus`.
- Weekly, or before a release: `python manage.py notification_delivery_report --hours 168 --fail-under-objective` (MVP criterion 4; outbox rows are kept 7 days).

## 52. Session-28 change: monitoring stack, email alerts, export, file deletion (ADR‑051)
- **Monitoring stack:** copy `.env.monitoring.example` to `.env.monitoring` and fill it; create `ops/secrets/metrics_token` (a random value of at least 16 characters, the same as the app's `PROMETHEUS_METRICS_TOKEN`) and `ops/secrets/smtp_password`. Same host as the app: `docker compose --env-file .env --env-file .env.monitoring -f docker/docker-compose.prod.yml -f docker/docker-compose.monitoring.yml up -d`. Another host: list private `host:port` targets in `ops/targets.prod/*.yml` (add `labels: {__scheme__: https}` for HTTPS) and run the monitoring file alone. Publish Grafana only, behind HTTPS (`GRAFANA_COOKIE_SECURE=true`).
- **Worker port 9808 has no login:** reachable from the monitoring host only (same Docker network, VPN or firewall rule).
- **Check:** in Prometheus, *Status → Targets* shows `realtron_api` and `realtron_worker` up; send a test alert (`docker compose … exec alertmanager amtool alert add Test severity=critical --alertmanager.url=http://localhost:9093`) and confirm the email arrives.
- **Export:** consumers in every market can now request their data (`POST /api/v1/compliance/export/`); the frontend can show the button to everyone.
- **Erasure** now deletes Cloudinary files; the Cloudinary credentials must be set wherever the erasure worker runs, or erasures of people with files stay failed (and are reported overdue).

## 53. Session-29 change: runbooks (ADR‑052)
- No migration. Reload the Prometheus rules (alerts now carry `runbook`) and restart web and workers.
- **Before launch:** fill the roles in the runbooks: who is on call, who is the privacy owner (data incidents, 72-hour notification), who may stop the assistant. Rehearse on staging: an AI stop and release, a token check (`check_whatsapp_token_health`), and `rotate_field_keys --dry-run`.
- **Set `FIELD_HASH_KEY`** to a fixed value now, so a key rotation in an incident does not change the lookup hashes.

## 54. Session-30 change: deploys over SSH, production approval (ADR‑053)
- **Before the first deploy:** set up both servers and the GitHub environments as in [docs/runbooks/deploy.md](../runbooks/deploy.md): environment secrets, `PUBLIC_URL`, and **required reviewers on `production`**. CI refuses production without them.
- **Dependencies upgraded** (KI‑118): Pillow 13, Django 5.2.17, DRF 3.18.2, PyJWT 2.15, simplejwt 5.5.1, requests 2.33, urllib3 2.8, lxml 6.1 and others. The full suite passes; check image upload and PDF reports on staging.
- **`/health/` now reports `version`** and hides error details outside DEBUG. Add `localhost` to `ALLOWED_HOSTS`.
- **First run on GitHub:** the pipeline has been linted and rehearsed locally, not yet run on Actions. Watch the first `main` run, especially the production gate's access to the environment API.

## 55. Session-31/32 changes: chat viewing changes, stream masking, image without torch, agent performance, lead tasks (ADR‑054…057)
- **Image rebuild:** `sentence-transformers` and `numpy` were removed (KI‑087); the image no longer installs torch.
- **Prompt** `agent.system` v10 (viewing tools). Visit reminders say "reply here" while `feature_talk_to_agent` is on.
- **Migration `leads 0024`** (new table `lead_tasks`, additive, rolling-safe).
- **Beat job `lead-task-reminders`** (every 5 minutes): restart celery beat after deploy so it is scheduled.
- **New endpoints for the frontend:** `GET /api/v1/reports/agent-performance/`, `/api/v1/leads/tasks/`. Agent-room stream events may carry `phone: null`.

## 56. Session-33 change: listing analytics (ADR‑058)
- **Migration `properties 0050`** (new table `listing_daily_stats`, additive, rolling-safe). No backfill: counts start at deploy.
- **Staged contract migrations renumbered** to `0051_drop_listing_v1_columns` / `0052_drop_prop_fts_gin` (now on top of 0050); follow `apps/properties/contract_migrations/README.md` with the new names.
- **New endpoint for the frontend:** `GET /api/v1/reports/listing-performance/`.
- **Load:** one extra small write per search page, listing view, save and enquiry. If a CDN is put in front of `/api/v1/public/`, read KI‑122 first.

## 57. Session-33 change: AI listing assistant (ADR‑059)
- **Migration `ai 0009`** (choices only, no SQL change).
- **New endpoints for the frontend:** `GET /api/v1/properties/{id}/ai/missing-info/`, `POST …/ai/describe/`, `POST …/ai/extract/`. Drafts are never saved; the frontend applies the accepted parts with `PATCH /properties/{id}/`.
- **Paid model calls:** each describe or extract is one call. Rate `LISTING_ASSISTANT_RATE` (default 30/hour per user); turn it off with `feature_listing_assistant=false`. New prompts `listing.describe` v1 and `listing.extract` v1.

## 58. Session-33 change: unified audit trail (ADR‑060)
- **Migration `core 0004`**: new table `audit_events` with a trigger that refuses UPDATE (except erasure's redaction) and DELETE. Additive. History before the deploy is not copied; the trail starts at deploy.
- **Set a fixed `AUDIT_CHAIN_KEY`** (default `SECRET_KEY`): if the key changes, every older row fails verification.
- **Beat job `verify-audit-chain`** (daily 02:30 UTC): restart celery beat. Its log line `audit.chain.ok … head_hash=…` should reach the log store outside the database; an `audit.chain.broken` line is an incident ([data incident runbook](../runbooks/data-incident.md)).
- **Run once after deploy:** `python manage.py verify_audit_chain`.
- **New endpoint for the frontend:** `GET /api/v1/audit-events/` (organization owners and admins see their organization's trail).
