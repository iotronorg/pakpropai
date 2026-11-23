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
