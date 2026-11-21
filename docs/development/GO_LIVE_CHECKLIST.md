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
