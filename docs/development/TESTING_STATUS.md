# Testing Status

## How to run

```bash
scripts/dev/test.sh                         # full suite: pytest tests/ apps/
scripts/dev/test.sh tests/test_foo.py -x    # any pytest args
REBUILD=1 scripts/dev/test.sh               # rebuild image after requirements change
```

- Requires Docker. The script uses the project `Dockerfile` (image `pakpropai-test:dev`) plus throwaway `postgres:15-alpine` and `redis:7-alpine` on the network `pakpropai-test-net` (ADR‑001).
- Env values in the script are CI‑only placeholders; no real secrets are used.
- pytest, pytest‑django and pytest‑cov are installed only inside the throwaway container.
- Migration drift check:
  `docker run --rm --network pakpropai-test-net -v "$PWD:/app" -w /app -e DATABASE_URL=postgres://realtron:realtronpass@pakpropai-test-pg:5432/realtron_test -e SECRET_KEY=ci-only -e REDIS_URL=redis://pakpropai-test-redis:6379/0 -e DJANGO_SETTINGS_MODULE=config.settings.test pakpropai-test:dev python manage.py makemigrations --check --dry-run`
- Clean up afterwards: `docker rm -f pakpropai-test-pg pakpropai-test-redis && docker network rm pakpropai-test-net`

## Results (all executed — nothing estimated)

| Date | Code state | Command | Result | Duration |
|---|---|---|---|---|
| 2026-11-21 | `HEAD` 0b574a7 (exported via `git archive`, no local changes) | `pytest tests/ apps/` | **1361 passed, 1 skipped, 1 failed** (`test_compliance.py::test_export_endpoint_queues_task` — Cloudinary API key required under eager Celery) | 4m45s |
| 2026-11-21 | Working tree after T‑0004/5/6 | `pytest tests/ apps/` | 1395 passed, 1 skipped, 1 failed (same pre‑existing failure) | 4m37s |
| 2026-11-21 | Working tree, end of session 2 | `pytest tests/ apps/` | **1425 passed, 1 skipped, 0 failed**, 98 subtests passed | 4m37s |
| 2026-11-21 | Working tree, end of session 2 | `makemigrations --check --dry-run` | No changes detected | — |
| 2026-11-21 | Working tree, end of session 2 | `bandit -r apps/ -ll` (CI gate) | 6 findings, **0 HIGH** (baseline had 1 HIGH: B324) | — |
| 2026-11-21 | Working tree, session 3 | `pytest tests/ apps/` | **1452 passed, 1 skipped, 0 failed**, 98 subtests | 4m35s |
| 2026-11-21 | Working tree, session 3 | `makemigrations --check` / `manage.py check` | No changes / no issues | — |
| 2026-11-21 | Working tree, session 3 | `bandit -r apps/ -ll` | 8 findings, **0 HIGH** | — |
| 2026-11-21 | Working tree, session 3 | Prod settings `manage.py check` without / with `FIELD_ENCRYPTION_KEYS` | `core.E002` error / no issues | — |
| 2026-11-21 | Scratch DB `rehearsal` | `migrate users` forward → reverse → forward with a plaintext CNIC row | varchar plaintext → text `enc1:` → varchar plaintext → text `enc1:` | — |
| 2026-11-21 | Working tree, session 4 start | `pytest tests/ apps/` | **1452 passed, 1 skipped, 0 failed** (matches handoff) | 4m44s |
| 2026-11-21 | Working tree, session 4 end | `pytest tests/ apps/` | **1477 passed, 1 skipped, 0 failed**, 98 subtests | 4m41s |
| 2026-11-21 | Working tree, session 4 | `makemigrations --check` / `manage.py check` / `bandit -r apps/ -ll` | No changes / no issues / 8 findings, 0 HIGH | — |
| 2026-11-21 | `HEAD` b9ac8a7, session 5 start | `pytest tests/ apps/` | **1477 passed, 1 skipped, 0 failed** (matches handoff) | 4m35s |
| 2026-11-21 | Working tree, after T‑1030 | `pytest tests/ apps/` | **1502 passed, 1 skipped, 0 failed**, 98 subtests | 4m35s |
| 2026-11-21 | Working tree, after T‑1021 (first run) | `pytest tests/ apps/` | 1527 passed, 1 skipped, **1 failed**: `test_api_key_security::test_raw_token_not_stored`, a flaky pre‑existing test (KI‑034), fixed and re‑run 3× green | 4m39s |
| 2026-11-21 | Working tree, session 5 end | `pytest tests/ apps/` | **1528 passed, 1 skipped, 0 failed**, 98 subtests | 4m31s |
| 2026-11-21 | Working tree, session 5 | `makemigrations --check` / `manage.py check` / `bandit -r apps/ -ll` | No changes / no issues / 8 findings, 0 HIGH | — |
| 2026-11-21 | Working tree, session 5 | `migrate` → `migrate organizations 0023` → `migrate`; `reconcile_memberships [--fail-on-drift]` | OK both ways; all categories 0, exit 0 | — |
| 2026-11-21 | Working tree, session 5 | `makemigrations --check`, `check`, `migrate core 0001` → `migrate` | No changes / no issues / OK | — |
| 2026-11-21 | `HEAD` 63c3cd6, session 6 start | `pytest tests/ apps/` | **1528 passed, 1 skipped, 0 failed** (matches handoff) | 4m53s |
| 2026-11-21 | Working tree, after T‑1100 | `pytest tests/ apps/` | **1556 passed, 1 skipped, 0 failed**, 108 subtests | 4m48s |
| 2026-11-21 | Working tree, session 6 end (after T‑1090a; image rebuilt with drf‑spectacular) | `pytest tests/ apps/` | **1567 passed, 1 skipped, 0 failed**, 111 subtests | 4m37s |
| 2026-11-21 | Working tree, after T‑1090b | `pytest tests/ apps/` | **1587 passed, 1 skipped, 0 failed**, 116 subtests | 4m45s |
| 2026-11-21 | Working tree, after T‑1090b | `check` / `makemigrations --check` / `spectacular --validate` / `bandit -ll` | no issues / no changes / valid (154/32 unchanged) / 0 HIGH | — |
| 2026-11-21 | Working tree, after T‑1090c (first run) | `pytest tests/ apps/` | 1601 passed, 1 skipped, **1 failed**: `test_verification_input::test_bad_limit_returns_400` asserted the old ad‑hoc `detail` string; updated to the standard field error (see below) | 4m39s |
| 2026-11-21 | Working tree, session 6 end (after T‑1090c) | `pytest tests/ apps/` | **1602 passed, 1 skipped, 0 failed**, 126 subtests | 5m19s |
| 2026-11-21 | Working tree, after T‑1090c | `check` / `makemigrations --check` / `spectacular --validate` / `bandit -ll` | no issues / no changes / valid (154/32 unchanged) / 0 HIGH | — |
| 2026-11-21 | Working tree, session 6 end (after T‑1090d) | `pytest tests/ apps/` | **1617 passed, 1 skipped, 0 failed**, 143 subtests | 5m05s |
| 2026-11-21 | Working tree, after T‑1090d | `check` / `makemigrations --check` / `bandit -ll` | no issues / no changes / 8 Medium, 0 HIGH | — |
| 2026-11-21 | `HEAD` db23c9a, session 7 start | — | Tree clean; the owner had committed T‑1090d. Session‑6 end state 1617/1/0 | — |
| 2026-11-21 | Working tree, session 7 (after T‑1050a) | `pytest tests/ apps/` | **1641 passed, 1 skipped, 0 failed**, 156 subtests | 4m50s |
| 2026-11-21 | Scratch DB `rehearse` | `migrate` (properties ≤0022, organizations ≤0024) + 2 raw rows (`ae`, `ZZ`) → `migrate` → report → back to `properties 0022`/`organizations 0024`/`markets zero` → `migrate` | Backfill `ae`→AE, `ZZ` unmatched (reported); reverse and re‑forward OK; `makemigrations --check` clean; `check` clean | — |
| 2026-11-21 | Working tree, session 7 | `bandit -r apps/ -ll` | 8 Medium, **0 HIGH** | — |
| 2026-11-21 | Working tree, session 6 | `manage.py spectacular --validate --file openapi.yaml` | exit 0; valid OpenAPI 3.1; 220 paths; 154 unique errors / 32 unique warnings (KI‑037) | — |
| 2026-11-21 | Working tree, session 6 end | `makemigrations --check` / `manage.py check` / `bandit -r apps/ -ll` / CI YAML parse | No changes / no issues / 8 Medium, **0 HIGH** / OK | — |
| 2026-11-21 | Scratch DB `bench` | `migrate` → `migrate properties 0020` → `migrate` | OK both ways; indexes dropped on reverse, `pg_trgm` kept | — |
| 2026-11-21 | Scratch DB `bench`, 200k synthetic listings, `ANALYZE` | `EXPLAIN ANALYZE` of 7 hot query shapes (scratchpad script) | No seq scans. City icontains + rank 2.7 ms; city+location 4.7 ms; price band 0.3 ms; viewer‑org OR 1.6 ms; API city iexact 0.07 ms; FTS old→new: selective 631→3 ms, common 757→238 ms, "penthouse" 720→92 ms | — |
| 2026-11-21 | Container | `infra/scripts/health_check.py` against dead port / missing URL / local 200 server | exit 1 / exit 1 / exit 0 | — |

## Tests added in sessions 2–7

| File | Tests | Covers |
|---|---|---|
| `tests/test_sandbox_visibility.py` | 21 | T‑0004: derive‑on‑insert, `visible_to()`, search service, AI tool, client API, recommendations, backfill migrations |
| `tests/test_ai_usage_and_cache_policy.py` | 13 | T‑0005 cache policy; T‑0006 usage capture and recording through `AIServiceManager.process` |
| `tests/test_m0_stabilisation.py` | 19 | T‑0008 scraper default; T‑0012 routes/params; T‑0015 frozen voice/syndication |
| `tests/test_ai_side_effect_guards.py` | 20 | T‑0007 confirmation guard, deal‑lock freeze (tool, tool list, endpoint); T‑0013/T‑0017 audit guard + disclaimer |
| `tests/test_field_encryption.py` | 13 | T‑0010 primitives, rotation, at‑rest ciphertext, webhook lookup, legacy plaintext, config cache, migration helpers |
| `tests/test_compliance_wiring.py` | 4 | T‑0009 end‑to‑end grounding check |

| `tests/test_otp_hashing.py` | 9 | T‑1010: no plaintext column/value, keyed + bound hash, API send→verify, migration forward/reverse (`MigrationExecutor`) |
| `tests/test_whatsapp_inbound_resilience.py` | 16 | T‑1020: retry on transient DB errors, backoff, dead‑letter, no retry after a send, org‑lookup retry, replay + command, webhook key release |
| `tests/test_membership_consolidation.py` | 25 | T‑1030: agent/owner projection sync (join, move, unlink, rejoin, owner kept, unrelated saves), admin transfer, API writer paths (team add/remove revokes access, members delete, JSON registration + invite — KI‑032), primary org choice, drift report/repair/command, backfill migration idempotency |
| `tests/test_outbox.py` | 26 | T‑1021: rollback leaves no row, on‑commit dispatch with id only, idempotent key, encrypted at rest, broker‑down fallback to relay, sent/permanent/transient/backoff/exhausted/expired, lease skip + stale‑lease reclaim, payload wipe, relay/purge; end‑to‑end OTP endpoint and `notify_user` through the real client (HTTP patched), notification idempotency/24 h window/final attempt/preferences; KI‑033 static guard + hot‑lead alert |
| `tests/test_search_indexes.py` | 28 | T‑1100: indexes + `pg_trgm` exist and are valid; planner uses each index for the exact SQL the code emits (`enable_seqscan=off`); tsquery builder (prefix/AND, operator stripping, unicode, caps, always‑valid tsquery); API `?search=` (plural prefix, AND, fields, ref‑no exact + partial, sandbox hidden, city combo, junk → empty); migration is non‑atomic + concurrent |
| `tests/test_openapi_schema.py` | 11 | T‑1090a: valid 3.1 schema, core paths, auth schemes, list params, error/warning budget ratchet, access (anon/user refused, admin/staff allowed, public flag), `ErrorEnvelope` component (+1) |
| `tests/test_error_envelope.py` | 20 | T‑1090b: request id generated/kept/replaced (injection), header on success, context cleared, log field, CORS; DRF exceptions (401 code, validation fields, 404, custom code, list body, key collision); hand‑built `error` string kept; security‑middleware 403; unrouted `/api/` 404 JSON; unhandled 500 JSON without internals; non‑`/api/` untouched |
| `tests/test_query_param_validation.py` | 18 | T‑1090c: helpers (int/clamp/bounds/limit‑offset/choice); audit‑log, chain‑verify, notifications, fraud‑alerts bad input → field 400 (negative limit was a 500); property enums, price range, `min_score` bound, `ref_no` exact (no `UPPER`), list params + enums in OpenAPI |
| `tests/test_idempotency.py` | 15 | T‑1090d: appointment booking replay (one row, same id, replay header), no key unchanged, 422 reuse, per‑user scope, bad key 400, failed request frees key for corrected retry, body encrypted at rest; probe view: 4xx/5xx/exceptions not stored, 409 in flight, stale lease takeover, expiry frees key, anonymous untracked, purge; OpenAPI header on all 5 targets |
| `tests/test_markets.py` | 24 | T‑1050a: seed = registry, only PK active, seed idempotent/keeps edits; `Market.clean`; PROTECT; admin no delete; `get_market` row/cache invalidation/registry fallback/unknown; defaults follow `DEFAULT_MARKET` (PK, AE override, unknown raises); market derived on create (explicit kept, not recomputed); backfill migrations; report + `--apply` + `--max-unmatched-pct`; static check of literal defaults + ratchet |

## Modified existing tests (behaviour change, not weakening)

- Session 6 (T‑1090c): `test_verification_input.py::test_bad_limit_returns_400` asserts the standard field‑level error (`limit` key + `error_info.fields`) instead of the old ad‑hoc `detail` string. Stricter than before.

- `test_compliance.py::test_export_endpoint_queues_task`: it now patches `.delay` and asserts the task is enqueued with a real `DataExportRequest` id. This is stricter than before, and it no longer calls Cloudinary.
- Deal‑creation test classes in `test_deal_lock.py`, `test_sepa_payments.py`, `test_stripe_deal_lock.py` and `test_compliance_views.py` now call `enable_feature('feature_deal_lock')` in `setUp`, because deal lock is frozen by default (ADR‑006). `DealLockBlockedEntityTest` previously could pass from any 403; it now reaches the sanctions check.

- Session 5 (T‑1021): `test_celery_tasks.py` general‑error test now expects an outbox retry (notification stays `pending`) instead of immediate `failed`; the requeue test and `test_otp_hashing`/`test_service_layer`/`test_org_registration` read the outbox instead of a mocked `.delay`. `test_api_key_security` flake fixed (KI‑034).
- Session 5: `test_org_membership.py` (4 setUps) and `test_lead_scoring.py` seed the owner membership with `get_or_create`, because creating an org with `admin_user` now creates it (ADR‑013). Assertions unchanged.
- Session 4: `test_celery_tasks.py::CleanupExpiredOTPsTest` creates rows with `code_hash` (the `code` column no longer exists).
- Session 3: syndication isolation tests (8 classes) and `VoiceInboundWebhookTest` call `enable_feature(...)`, because those modules are frozen by default.

## Coverage
Not measured yet (no coverage run executed). CI is configured with `--cov=apps`.

## Not covered / gaps
- Outbound WhatsApp is tested with `requests.post` patched; no test hits Meta. The tests used to patch `WhatsAppClient` methods at class level, which hid KI‑033; new tests go through the real client.
- No test runs against a real LLM provider (all tests mock or use local backend paths).
- Gemini AFC multi‑round token counting is not verifiable without the provider (KI‑006 residual).
- Inbound retries are tested by simulating broker redelivery (`apply(retries=n)`): Celery 5.4 eager mode does not execute retries (KI‑031). The DB‑starvation chaos run has not been repeated.
- CI workflow changes were validated as YAML and the health script locally; the workflow has not been run on GitHub Actions.
