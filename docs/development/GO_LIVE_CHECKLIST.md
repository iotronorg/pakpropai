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
