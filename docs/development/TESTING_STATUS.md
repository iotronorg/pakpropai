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
| 2026-11-21 | Working tree, session 7 (after T‑1031a) | `pytest tests/ apps/` | **1661 passed, 1 skipped, 0 failed**, 173 subtests | 5m06s |
| 2026-11-21 | Working tree, after T‑1031a | `check` / `makemigrations --check` / `bandit -ll` | no issues / no changes / 0 HIGH | — |
| 2026-11-21 | Working tree, after T‑1031b | `pytest tests/ apps/ -v` | **1665 passed, 1 skipped, 0 failed**, 178 subtests | 5m17s |
| 2026-11-21 | Working tree, after T‑1031b | `check` / `makemigrations --check` / `bandit -ll` | no issues / no changes / 8 Medium, 0 HIGH | — |
| 2026-11-21 | Working tree, after T‑1031b (earlier attempt) | `pytest tests/ apps/ -q` | **Hung** at ~73% for 25 min; Postgres idle (one connection idle in transaction), no lock waits; killed before a stack dump. Verbose rerun on the same code passed. Recorded as KI‑039 | — |
| 2026-11-21 | Working tree, session 6 | `manage.py spectacular --validate --file openapi.yaml` | exit 0; valid OpenAPI 3.1; 220 paths; 154 unique errors / 32 unique warnings (KI‑037) | — |
| 2026-11-21 | Working tree, session 6 end | `makemigrations --check` / `manage.py check` / `bandit -r apps/ -ll` / CI YAML parse | No changes / no issues / 8 Medium, **0 HIGH** / OK | — |
| 2026-11-21 | Scratch DB `bench` | `migrate` → `migrate properties 0020` → `migrate` | OK both ways; indexes dropped on reverse, `pg_trgm` kept | — |
| 2026-11-21 | Scratch DB `bench`, 200k synthetic listings, `ANALYZE` | `EXPLAIN ANALYZE` of 7 hot query shapes (scratchpad script) | No seq scans. City icontains + rank 2.7 ms; city+location 4.7 ms; price band 0.3 ms; viewer‑org OR 1.6 ms; API city iexact 0.07 ms; FTS old→new: selective 631→3 ms, common 757→238 ms, "penthouse" 720→92 ms | — |
| 2026-11-21 | Container | `infra/scripts/health_check.py` against dead port / missing URL / local 200 server | exit 1 / exit 1 / exit 0 | — |
| 2026-11-22 | Working tree, session 8 start (`2fd8ee0`, clean) | — | Not re‑run at start; the session‑7 handoff count was 1665/1/0 and the tree matched `HEAD` | — |
| 2026-11-22 | Working tree, after T‑1032a | `pytest tests/ apps/ -q` | 1700 passed, 1 skipped, **1 failed**: `test_openapi_schema` error budget (new schema‑less view). Fixed by annotating the members/ownership views | 9m48s |
| 2026-11-22 | Working tree, session 8 end | `pytest tests/ apps/ -q` | **1713 passed, 1 skipped, 0 failed**, 178 subtests | 6m28s |
| 2026-11-22 | Working tree, after T‑1022b | `pytest tests/ apps/ -q` | **1718 passed, 1 skipped, 0 failed** | 6m47s |
| 2026-11-22 | Working tree, after T‑1050b‑1 (session 8 end) | `pytest tests/ apps/ -q` | **1723 passed, 1 skipped, 0 failed**, 178 subtests | 6m36s |
| 2026-11-22 | Working tree, session 8 continued, after T‑1050b‑2 | `pytest tests/ apps/ -q` + checks | **1728 passed, 1 skipped, 0 failed**; check/drift/schema clean; Bandit 0 HIGH | 6m31s |
| 2026-11-22 | Working tree, session 8 continued, after T‑1050c | `pytest tests/ apps/ -q` + checks | **1738 passed, 1 skipped, 0 failed**, 178 subtests; check/drift/schema clean; Bandit 0 HIGH | 6m20s |
| 2026-11-22 | Working tree, session 8 continued, after T‑1070a | `pytest tests/ apps/ -q` + checks | **1754 passed, 1 skipped, 0 failed**; check/drift/schema clean; Bandit 0 HIGH | 6m50s |
| 2026-11-22 | Working tree, session 8 continued, after T‑1070b | `pytest tests/ apps/ -q` + checks | **1770 passed, 1 skipped, 0 failed**; check/drift/schema clean; Bandit 0 HIGH | 8m36s |
| 2026-11-22 | Working tree, session 8 continued, after T‑1070c | `pytest tests/ apps/ -q` + checks | **1781 passed, 1 skipped, 0 failed**; check/drift/schema clean; Bandit 0 HIGH | 7m54s |
| 2026-11-22 | Working tree, session 8 end | `check` / `makemigrations --check` / `spectacular --validate` / `bandit -r apps/ -ll` | no issues / no changes / exit 0 (153 errors / 28 warnings unique) / **0 HIGH** | — |
| 2026-11-23 | Working tree, session 9, after T‑1070d | `pytest tests/ apps/ -q` | **1804 passed, 1 skipped, 0 failed**, 188 subtests | 5m40s |
| 2026-11-23 | Working tree, session 9, after T‑1070e (first run) | `pytest tests/ apps/ -q` | 1812 passed, 1 skipped, **1 failed**: `test_listing_schema_v2::test_create_and_read_v2_fields` sent `possession: 'ready'`, which the new PK schema rejects as intended. Fixture changed to `'available'` | 5m55s |
| 2026-11-23 | Working tree, session 9 end | `pytest tests/ apps/ -q` | **1813 passed, 1 skipped, 0 failed**, 201 subtests | 5m51s |
| 2026-11-23 | Working tree, session 9 | `check` / `makemigrations --check` / OpenAPI budget test / `bandit -r apps/ -ll` | no issues / no changes / within budget (taxonomy view has an inline schema) / **0 HIGH** (Medium 14: 3 new B608 on constant‑only SQL, same pattern as `size_sync.py`) | — |
| 2026-11-23 | Test DB `realtron_test` | `migrate properties 0030` → `migrate`; `migrate markets 0002` → `migrate`; `sync_listing_taxonomy --dry-run`; `Market.full_clean()` on every row | OK both ways; 35 amenities seeded; 0 drift; all seeded schemas valid | — |
| 2026-11-23 | Working tree, session 9 continued (mid‑work) | `pytest tests/ apps/ -q` | 60 failed, 747 errors: **invalid run**. Source was edited (a new model without its migration) while the suite ran against the bind mount. Discarded and re‑run clean | 4m43s |
| 2026-11-23 | Working tree, session 9 continued end (KI‑050, T‑1033, T‑1070f, T‑1032c) | `pytest tests/ apps/ -q` | **1839 passed, 1 skipped, 0 failed**, 206 subtests | 5m35s |
| 2026-11-23 | Working tree, session 9 third part (branding, T‑1060a) | `pytest tests/ apps/ -q` | **1856 passed, 1 skipped, 0 failed**, 206 subtests (first run: 1 failure, `test_markets` deleted a Market the new seed references; test fixed) | 5m54s |
| 2026-11-23 | Working tree, session 9 fourth part (T‑1070g‑1) | `pytest tests/ apps/ -q` | **1862 passed, 1 skipped, 0 failed**, 206 subtests. Plus a migration rehearsal with data: drift → gate refuses → g‑1 repairs (v1 wins, `flat` → residential/apartment) → staged g‑2 applied → rollback to 0041 restores the v1 columns | 5m42s |
| 2026-11-23 | Working tree, session 9 fifth part (T‑1060b), DB = `postgis/postgis:15-3.4-alpine` (emulated amd64) | `pytest tests/ apps/ -q` | **1874 passed, 1 skipped, 0 failed**, 214 subtests. Plain‑SQL and PostGIS radius parity tested; migrations 0041↔0044 rehearsed; 200k‑row benchmark: both paths index‑backed, 1–5 ms | 6m19s |
| 2026-11-23 | Working tree, session 9 sixth part (T‑1060c) | `pytest tests/ apps/ -q` | **1884 passed, 1 skipped, 0 failed**, 220 subtests; Google HTTP mocked; migrations `geo 0001`/`locations 0003` rehearsed back and forward | 6m07s |
| 2026-11-23 | Working tree, session 9 seventh part (T‑1080) | `pytest tests/ apps/ -q` | **1899 passed, 1 skipped, 0 failed**, 220 subtests; migrations 0045–0047 rehearsed back to 0044 and forward | 6m17s |
| 2026-11-23 | Working tree, session 9 eighth part (T‑1011) | `pytest tests/ apps/ -q` | **1913 passed, 1 skipped, 0 failed**, 220 subtests; RFC 6238 vectors; `users 0014` rehearsed back to 0013 and forward | 6m35s |
| 2026-11-23 | Working tree, session 9 ninth part (T‑1110) | `pytest tests/ apps/ -q` | **1924 passed, 1 skipped, 0 failed**, 224 subtests; crafted files (EXIF/GPS, polyglot, forged header, truncated, HTML/SVG/GIF); `properties 0048`/`agents 0014` rehearsed back and forward | 6m54s |
| 2026-11-23 | Working tree, session 9 tenth part (T‑1120) | `pytest tests/ apps/ -q` | **1934 passed, 1 skipped, 0 failed**, 224 subtests; fake providers (failover, breaker, no failover after a tool ran, cache, provenance, market variables, pinned prompt versions); `ai 0003` rehearsed back to 0002 and forward; `check` and `makemigrations --check` clean | 7m19s |
| 2026-11-23 | Working tree, session 9 eleventh part (T‑1032b‑1) | `pytest tests/ apps/ -q` | **1946 passed, 1 skipped, 0 failed**, 224 subtests. `organizations 0028`/`agents 0015` rehearsed back to 0027/0014 and forward. Staged b‑2 (`organizations 0029`, `agents 0016`) applied and unapplied on a scratch copy. `reconcile_memberships --fail-on-drift` clean; `check`/`makemigrations --check` clean. The first run had 16 TransactionTestCase flush failures, caused by FKs on the now‑unmanaged legacy table; fixed by dropping them in 0015 | 6m38s |
| 2026-11-23 | Working tree, session 9 twelfth part (T‑1040) | `pytest tests/ apps/ -q` | **1954 passed, 1 skipped, 0 failed**, 224 subtests; `leads 0021`/`whatsapp 0013` rehearsed back and forward; `check`/`makemigrations --check` clean | 6m43s |
| 2026-11-23 | Working tree, session 9 thirteenth part (T‑2010) | `pytest tests/ apps/ -q` | **1966 passed, 1 skipped, 0 failed**, 224 subtests; scripted fake providers for the loop/failover; Gemini/OpenAI `step()` against SDK‑shaped fakes (real `google.genai.types`); `check`/`makemigrations --check` clean | 8m43s |
| 2026-11-24 | Working tree, session 10 (T‑2012, T‑2011) | `pytest tests/ apps/ -q` | **2014 passed, 1 skipped, 0 failed**, 224 subtests; `ai 0004`/`0005` applied, rolled back to 0003 and re‑applied on a scratch DB; `check`/`makemigrations --check` clean; Bandit HIGH 0 on `apps/ai`. An earlier run the same day was stopped and discarded (source edited while it ran) | 7m12s |
| 2026-11-24 | Working tree, session 10 (T‑2013, KI‑060) | `pytest tests/ apps/ -q` | **2035 passed, 1 skipped, 0 failed**, 224 subtests; `ai 0006` rolled back and re‑applied; Bandit HIGH 0 on `apps/ai`; 4 checker mutations all caught. Earlier runs: one invalid (a second test run shared the database), one real failure (PK literal ratchet, fixed) | 6m48s |
| 2026-11-24 | Working tree, session 11 (T‑2020, KI‑062, KI‑064) | `pytest tests/ apps/ -q` | **2066 passed, 1 skipped, 0 failed**, 224 subtests; `conversations 0001`/`ai 0007` reversed and re‑applied on an empty database; Bandit HIGH 0 on `apps/ai`, `apps/conversations`; 3 mutations (ownership, window filter, key scope) all caught. Not tested: a real provider, a real SSE client, concurrent requests on separate connections | 7m12s |
| 2026-11-24 | Working tree, session 12 (T‑2030, KI‑067) | `pytest tests/ apps/ -q` | **2108 passed, 1 skipped, 0 failed**, 224 subtests; `conversations 0002` reversed and re‑applied; Bandit HIGH 0 on `apps/ai`, `apps/conversations`, `apps/properties`; 4 mutations (hidden price, cursor binding, policy text, purpose asked once) all caught. Local benchmark, 100k synthetic listings, 7 runs each, median: broad 271 ms, city 161 ms, narrow 37 ms, style 103 ms, with facets 433 ms (not a p95 under load). Not tested: extraction by a real model | 7m19s |
| 2026-11-24 | Working tree, session 13 (T‑2031, KI‑070) | `pytest tests/ apps/ -q` | **2130 passed, 1 skipped, 0 failed**, 224 subtests; no migrations; Bandit HIGH 0 on `apps/properties`; 4 mutations (unsanitised images, free‑form attributes, visibility, hidden deposit) all caught. One earlier full run was discarded: it overlapped a mutation loop. Not tested: a real CDN | 7m01s |
| 2026-11-24 | Working tree, session 14 (T‑2050) | `pytest tests/ apps/ -q` | **2170 passed, 1 skipped, 0 failed**, 224 subtests; `makemigrations --check` clean; Bandit HIGH 0 on enquiries, notifications, leads; 5 mutations (agent access, revoked consent, WhatsApp lead scope, stored listing id, CRM thread isolation) all caught. Not tested: a real SMTP provider, parallel requests (the per‑user lock and the unique constraint were not raced), migration on production‑size data | 7m37s |
| 2026-11-24 | Working tree, session 15 (T‑2060) | `pytest tests/ apps/ -q` | **2207 passed, 1 skipped, 0 failed**, 224 subtests; `makemigrations --check` clean; Bandit HIGH 0 on viewings, leads, ai; 5 mutations (agent lock, viewing ownership, freelancer lead scope, reminder reset, invented slot) all caught; a real three‑thread race books one slot once. One existing test was changed: it posted the same agent and time twice and expected two bookings. Not tested: live WhatsApp/email, migration on production‑size data | 8m42s |
| 2026-11-24 | Working tree, session 16 (T‑2040) | `pytest tests/ apps/ -q` | **2242 passed, 1 skipped, 0 failed**, 224 subtests; `makemigrations --check` clean; OpenAPI budgets hold; Bandit and pyflakes clean on the new files; 12 mutations (dedupe, saved/own exclusions, repricing, notification failure, pause window, consent gate, withdrawal, two ownership checks, atomic remember, frozen criteria) all caught. After the run only `tests/test_preferences.py` changed (export and erasure run end to end); that file was run again and passes. Not tested: live WhatsApp/email, concurrent beat runs, alert load on production‑size data | 8m27s |
| 2026-11-24 | Working tree, session 17 (T‑2041) | `pytest tests/ apps/ -q` | **2270 passed, 1 skipped, 0 failed**, 224 subtests; `makemigrations --check` clean; OpenAPI budgets hold; Bandit HIGH 0 on markets and comparison; 11 mutations (hidden price ×3, visibility, comparability, place in another market, preferences on request, public answer, future rate, amount currency, amount of another listing) all caught. Not tested: a live model using the tool, real FX rates | 8m25s |
| 2026-11-24 | Working tree, session 18 (T‑2014, KI‑066, KI‑074) | `pytest tests/ apps/ -q` | **2318 passed, 1 skipped, 0 failed**, 224 subtests; `makemigrations --check` clean; OpenAPI budgets hold; Bandit HIGH 0 on moderation, leads, compliance; 10 mutations (suspension escape, approval reuse, stale decision, own listing, moderator role, flagged edit, own report, draft ownership, export ownership, second consent) all caught. Changed existing tests: "listed successfully" became "Draft saved" (three), the export test reads the stored file. Not tested: the new gunicorn settings under load, voice and WhatsApp session views with a withheld number, live sends | 9m00s |
| 2026-11-24 | Working tree, session 19 (T‑2016) | `pytest tests/ apps/ -q` | **2324 passed, 1 skipped, 0 failed**, 224 subtests; `makemigrations --check` clean; 9 mutations all caught. Two earlier full runs the same day had one failure each: `test_mfa::test_disable_and_regenerate` (cause found and fixed, KI‑089) and `test_deal_lock::test_happy_path_returns_201_with_deal_id` (400; not reproduced in 4 file runs, 1500 repeats and the next full run; KI‑088). Removed: 8 tests of the old `semantic_cache_scope` policy, replaced by `tests/test_faq.py`. Not tested: answer quality with a live model | 9m39s |
| 2026-11-25 | `a33c7fa` clean (baseline) | `pytest -q` | 2324 passed, 1 skipped, 0 failed | 8m26s |
| 2026-11-25 | Working tree, session 20 (T‑2070) | `pytest tests/ apps/ -q` | **2355 passed, 1 skipped, 0 failed**, 228 subtests; an earlier full run had 2 failures, both the eval gate reporting a guard defect (fixed). `run_ai_evals`: safety 70/70, grounding 59/59, fairness 62/62, dataset 54/54. `makemigrations --check`, `check`, schema validation clean; `ai 0008` forward/back/forward; Bandit 0 HIGH; 11 mutations all caught. **Live evals not run** (no key, paid); workflows only parsed as YAML | 8m49s |
| 2026-11-25 | Working tree, session 21 (T‑2071) | `pytest tests/ apps/ -q` | **2364 passed, 1 skipped, 0 failed**, 229 subtests. Earlier full runs the same day: one failure in `test_geo` (real boot-time bug, fixed), one in a new test (line-break gap, fixed). `run_ai_evals`: safety 70, grounding 68, fairness 62, indirect 30, dataset 54, all passed. `makemigrations --check`, `check` clean; Bandit 0 HIGH; 12 mutations all caught after tests were added for 4 first survivors. Live evals not run | 9m16s |
| 2026-11-25 | Working tree, session 22 (T‑2051) | `pytest tests/ apps/ -q` | **2386 passed, 1 skipped, 0 failed**, 229 subtests. One earlier full run failed the OpenAPI warning ratchet (duplicate enum, fixed). Eval gate passed; migration `enquiries 0002` reversed and re-applied; `makemigrations --check`, `check` clean; Bandit 0 HIGH; 19 of 19 mutations caught. Live evals not run | 10m02s |
| 2026-11-25 | Working tree, session 23 (E2E journey) | `pytest tests/ apps/ -q`; `scripts/dev/e2e.sh` | **2388 passed, 1 skipped, 0 failed**; journey **69 checks passed** (twice from clean or existing data). Negative runs: AI worker stopped, wrong signature: both fail. `makemigrations --check`, `check` clean; Bandit 0 HIGH. Not run: staging, real model, the GitHub workflow | 9m17s |
| 2026-11-25 | Working tree, session 24 (KI‑094, KI‑097) | `pytest tests/ apps/ -q`; `scripts/dev/e2e.sh` | **2402 passed, 1 skipped, 0 failed**; journey 69 checks. `makemigrations --check`, `check` clean; Bandit 0 HIGH; 16 of 17 mutations caught, the other is equivalent (Stripe's library also refuses an empty secret). Real provider callbacks not exercised | 13m56s |
| 2026-11-25 | Working tree, session 25 (MVP 3, 5) | `pytest tests/ apps/ -q`; `scripts/dev/e2e.sh` | **2417 passed, 1 skipped, 0 failed**; journey 69 checks. `makemigrations --check`, `check` clean; Bandit 0 HIGH; 14 of 14 mutations caught. Alert timing proven with simulated time, not real days | 9m20s |
| 2026-11-25 | Working tree, session 26 (MVP 10, 11) | `pytest tests/ apps/ -q`; `scripts/dev/e2e.sh` | **2434 passed, 1 skipped, 0 failed**. Journey 69 checks. `makemigrations --check`, `check` clean; Bandit 0 HIGH; 17 mutations, all caught (one after strengthening its test). Users and blacklist migrations tried forward and back on sample rows | 9m35s |
| 2026-11-23 | Same | `check` / `makemigrations --check` / OpenAPI budget test / `bandit -r apps/ -ll` | no issues / no changes / within budget / **0 HIGH** (Medium 14) | — |
| 2026-11-23 | Test DB `realtron_test` | `migrate properties 0034` → `migrate`; `migrate organizations 0026` → `migrate` | OK both ways | — |
| 2026-11-23 | Test DB, 200k synthetic listings, `ANALYZE` | `EXPLAIN ANALYZE` legacy expression vs stored `search_tsv` (3 broad terms, 119k–169k matches) | 828→124 ms, 2020→134 ms, 1741→147 ms; new plan uses `prop_search_tsv_gin` | — |

## Tests added in sessions 2–8

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
| `tests/test_markets.py` | 34 | T‑1050a: seed = registry, only PK active, seed idempotent/keeps edits; `Market.clean`; PROTECT; admin no delete; `get_market` row/cache invalidation/registry fallback/unknown; defaults follow `DEFAULT_MARKET` (PK, AE override, unknown raises); market derived on create (explicit kept, not recomputed); backfill migrations; report + `--apply` + `--max-unmatched-pct`; static check of literal defaults + ratchet |
| `tests/test_permission_matrix.py` | 26 | T‑1031a: members list matrix (8 actors), per‑org isolation; grant policy (owner never, org_admin owner‑only, reactivation, agents/viewers refused, removal rules, foreign member 404); `X-Organization-ID` (primary default, member org, foreign → 403 + one security event, malformed → 403, role from selected org, lead scoping follows, context cleared, deactivation immediate); OTP purpose whitelist (KI‑030) |
| `tests/test_membership_contract.py` | 37 | T‑1032a: invite audited (no phone in audit), reactivation, owner never grantable, org_admin owner‑only, no phone enumeration past a denial, non‑manager refused, duplicate 409 + concurrent invite 409; role change (audit, employment type, no‑op, owner immutable, org_admin rules, validation, foreign/unknown 404, viewer refused, inactive); removal (audit, agent projection cleared, owner, org_admin owner‑only, system actor); transfer (owner→org_admin, previous owner kept as org_admin, drift clean, audit, rights move, stale instance save, only owner, target must be org_admin/developer/active/owning no other org, self 409, foreign/inactive 404, bad id, platform‑admin path, selector, anonymous); KI‑040 |
| `tests/test_outbox_adoption.py` | 16 | T‑1022a: `whatsapp.text` through the real client, payload wiped, encrypted at rest, org credentials at delivery, closed window permanent, skip‑window passthrough, transient retry, idempotent key / no recipient, rollback; hot‑lead once per crossing; verification once per status (KI‑042); listing alert once per (listing, lead). T‑1022b: seller code queued only after AML screening (blocked buyer → none; verified against the old ordering), deal expiry buyer/seller/agent once, audit companion once per audit, scraped follow‑up with expiry |
| `tests/test_timezones.py` | 10 | T‑1050c: UTC settings and crontab instants; market/org zones, invalid zone → UTC, local day bounds (incl. a 25 h DST day), `format_local`; appointment reminder in PKT (KI‑047); "new today" uses the local day; monthly trend buckets locally; monthly report per‑org local month (PK vs New York at 03:00/05:00 UTC, no duplicates) |
| `tests/test_listing_schema_v2.py` | 16 | T‑1070a: defaults and DB defaults for pre‑v2 inserts; cross‑field rules; API create/read/list, partial update validated against stored values, list filters + 400s; search purpose aliases, DB filters, rent skips scrapers and no live‑pending; WhatsApp line (period, beds, rent tag, price on request, old cached dicts); AI extractor bedrooms/purpose and tool forwarding |
| `tests/test_size_v2.py` | 17 | T‑1070b: dual‑write both directions, changed pair wins, `update_fields` extension, deferred load keeps newer v1, backfill migration + `sync_listing_size` (dry‑run and repair); KI‑049 size helpers, kanal = 20 marla price signal, m² search band, listed unit in results; API v2 create, conflicting alias 400, deprecated alias still writes, `size_m2` read‑only, OpenAPI deprecation text |
| `tests/test_price_history.py` | 10 | T‑1070c: initial row on create; unpriced → none until priced; change vs no‑op; `update_fields` without price skips (regression for the snapshot bug); deferred loads compare with the last row; context attribution vs `system`; rollback keeps listing and history consistent; backfill idempotent and dated at creation; API attribution, newest‑first read without `changed_by`; visibility 404 |

## Modified existing tests (behaviour change, not weakening)

- Session 8 (T‑1022a): `test_follow_up_automation.py` (13 tests) and `test_celery_tasks.py::test_sends_reminder_for_appointment_within_window` assert the queued outbox row (key, recipient, org, expiry, same‑day rerun dedup) instead of a mocked `get_wa_client`. `test_outbox.py::test_hot_lead_alert_reaches_meta` now delivers through the outbox dispatch and still asserts the real HTTP call. `test_openapi_schema.py` budgets lowered (154/32 → 153/28).

- Session 6 (T‑1090c): `test_verification_input.py::test_bad_limit_returns_400` asserts the standard field‑level error (`limit` key + `error_info.fields`) instead of the old ad‑hoc `detail` string. Stricter than before.

- `test_compliance.py::test_export_endpoint_queues_task`: it now patches `.delay` and asserts the task is enqueued with a real `DataExportRequest` id. This is stricter than before, and it no longer calls Cloudinary.
- Deal‑creation test classes in `test_deal_lock.py`, `test_sepa_payments.py`, `test_stripe_deal_lock.py` and `test_compliance_views.py` now call `enable_feature('feature_deal_lock')` in `setUp`, because deal lock is frozen by default (ADR‑006). `DealLockBlockedEntityTest` previously could pass from any 403; it now reaches the sanctions check.

- Session 5 (T‑1021): `test_celery_tasks.py` general‑error test now expects an outbox retry (notification stays `pending`) instead of immediate `failed`; the requeue test and `test_otp_hashing`/`test_service_layer`/`test_org_registration` read the outbox instead of a mocked `.delay`. `test_api_key_security` flake fixed (KI‑034).
- Session 5: `test_org_membership.py` (4 setUps) and `test_lead_scoring.py` seed the owner membership with `get_or_create`, because creating an org with `admin_user` now creates it (ADR‑013). Assertions unchanged.
- Session 4: `test_celery_tasks.py::CleanupExpiredOTPsTest` creates rows with `code_hash` (the `code` column no longer exists).
- Session 3: syndication isolation tests (8 classes) and `VoiceInboundWebhookTest` call `enable_feature(...)`, because those modules are frozen by default.

### Session 27

| Date | Code state | Command | Result | Duration |
|---|---|---|---|---|
| 2026-11-25 | Working tree, session 27 | `scripts/dev/test.sh` | **2464 passed, 1 skipped, 0 failed**, 229 subtests | 10m23s |
| 2026-11-25 | Working tree, session 27 | `promtool check config` + `test rules ops/tests/slo-rules.test.yaml` (v3.16.0); `amtool check-config` (v0.28.1) | 55 rules; all alert tests pass; config valid | — |
| 2026-11-25 | Working tree, session 27 | Real prefork worker (2 processes) + `WORKER_METRICS_PORT`, 4 tasks sent | Exporter reported all 4 (`webhook_processed_total ok 4`) | — |
| 2026-11-25 | Working tree, session 27 | Grafana (latest) with `ops/grafana/provisioning` | Both dashboards provisioned (SLO: 35 panels) | — |
| 2026-11-25 | Working tree, session 27 | `makemigrations --check`; `notifications` 0006 → 0004 → 0006; `spectacular --validate`; Bandit on changed modules | No changes; reverse/forward OK; valid; 0 HIGH | — |

### Session 28

| Date | Code state | Command | Result | Duration |
|---|---|---|---|---|
| 2026-11-25 | Working tree, session 28 | `scripts/dev/test.sh` | **2477 passed, 1 skipped, 0 failed**, 229 subtests | 14m17s |
| 2026-11-25 | Working tree, session 28 | Monitoring stack (`docker-compose.monitoring.yml`) + real web and prefork worker + Mailpit | Both targets up (web reached by bearer token from another container); test alert email received with subject and recipient; Grafana datasource healthy, both dashboards provisioned. Found KI‑111 | — |
| 2026-11-25 | Working tree, session 28 | `promtool check config` (file targets, token file); `start.sh` render-only with sample values; bad value rejected | Valid; rendered config passes `amtool`; exit 1 on a quote | — |
| 2026-11-25 | Working tree, session 28 | `makemigrations --check` | No changes | — |

### Session 29

| Date | Code state | Command | Result | Duration |
|---|---|---|---|---|
| 2026-11-25 | Working tree, session 29 | `scripts/dev/test.sh` | **2491 passed, 1 skipped, 0 failed**, 287 subtests | 10m24s |
| 2026-11-25 | Working tree, session 29 | `promtool test rules ops/tests/slo-rules.test.yaml`; `promtool check rules` | Pass; 60 rules | — |
| 2026-11-25 | Working tree, session 29 | `bandit -ll` on changed files | No high; two B608 on quoted identifiers marked `nosec` | — |

### Session 30

| Date | Code state | Command | Result | Duration |
|---|---|---|---|---|
| 2026-11-26 | Working tree, session 30, upgraded dependencies | `REBUILD=1 scripts/dev/test.sh` | First run: 1 failed (DRF 3.18 made `market` required on the kill-switch serializer) → fixed → **2506 passed, 1 skipped, 0 failed** | 19m06s |
| 2026-11-26 | Working tree, session 32 (T‑3010, T‑3020) | `scripts/dev/test.sh -q`; `makemigrations --check` | **2523 passed, 1 skipped, 0 failed** (7 agent-performance and 9 lead-task tests; data-protection map extended). No drift after `leads 0024`. OpenAPI warning budget unchanged (enum overrides for the task choices) | 9m55s |
| 2026-11-26 | Working tree, session 31, continued (KI‑083, KI‑121, KI‑087; image rebuilt without `sentence-transformers`/`numpy`) | `REBUILD=1 scripts/dev/test.sh -q`; `scripts/dev/e2e.sh`; `pip check`; `pip-audit -r requirements/prod.txt` | **2507 passed, 1 skipped, 0 failed** (3 new tests; the 10 cache tests deleted). Journey 94 checks. No broken requirements; no known vulnerabilities; torch absent from the image (1.01 GB) | 13m58s |
| 2026-11-26 | Working tree, session 31 (KI‑076, KI‑088) | `scripts/dev/test.sh -q`; `tests/test_deal_lock.py`; `scripts/dev/e2e.sh`; `makemigrations --check` | **2514 passed, 1 skipped, 0 failed** (full run; the KI‑088 test was added during it and then passed alone: `test_deal_lock.py` 27 passed). Journey **94 checks** passed, now including move and cancel by chat on the web assistant and WhatsApp. No migration drift | 10m25s |
| 2026-11-26 | Working tree | `tests/test_deploy_pipeline.py tests/test_runbooks.py` (after the last script and entrypoint changes) | 17 + 14 passed | — |
| 2026-11-26 | Working tree | `pip-audit -r requirements/prod.txt` | No known vulnerabilities (was 146 in 14 packages) | — |
| 2026-11-26 | Working tree | `actionlint`; `shellcheck scripts/deploy/remote_deploy.sh` | Clean (one existing, intentional note in `ai-evals-live.yml`) | — |
| 2026-11-26 | Working tree | Deploy rehearsal: local registry, real prod images, `docker-compose.prod.yml`, `remote_deploy.sh` | Runs 1–2 found KI‑120 and KI‑119. Run 3: good release migrated, healthy with its version, live; `/metrics/` 200 over plain HTTP; bad release not healthy → rolled back, all services on the good tag | — |
| 2026-11-26 | Working tree | GitHub Actions | **Not run**: needs the owner's servers, environments and secrets | — |
| 2026-11-26 | Working tree, session 33 (T‑3060), first run | `scripts/dev/test.sh` | 2541 passed, 1 skipped, **1 failed**: `test_listing_contract.py::…::test_staged_files_are_outside_the_loaded_migrations` (staged contract migrations had to be renumbered on top of `properties 0050`); fixed | 9m27s |
| 2026-11-26 | Working tree, session 33 end | `scripts/dev/test.sh` | **2542 passed, 1 skipped, 0 failed**, 294 subtests (19 new listing-analytics tests) | 10m09s |
| 2026-11-26 | Working tree, session 33 | `makemigrations --check`; `manage.py check`; `spectacular --validate` | No changes; no issues; schema valid (exit 0) | — |
| 2026-11-26 | Working tree, session 33 + T‑3050, first run | `scripts/dev/test.sh` | 2557 passed, 1 skipped, **1 failed** (subtest): `test_markets.py::…::test_remaining_pk_literals_do_not_grow`, a `'PKR …'` literal in the new eval fixture; now taken from the PK market pack | 14m09s |
| 2026-11-26 | Working tree, session 33 end (T‑3060, T‑3050) | `scripts/dev/test.sh -rf` | **2557 passed, 1 skipped, 0 failed**, 295 subtests (15 listing-assistant tests) | 9m48s |
| 2026-11-26 | Working tree, session 33 end | `makemigrations --check`; `manage.py check`; `spectacular --validate`; `run_ai_evals` | No changes after `ai 0009`; no issues; no new schema warnings; eval gate passed (`listing_copy` 35/35) | — |
| 2026-11-26 | Working tree, T‑3070 | `scripts/dev/test.sh -rf` | **2571 passed, 1 skipped, 0 failed**, 299 subtests (14 audit-trail tests, including six concurrent writers) | 10m31s |
| 2026-11-26 | Working tree, T‑3070 | `makemigrations --check`; `manage.py check`; `spectacular --validate` | No changes after `core 0004`; no issues; no warnings from the new views | — |
| 2026-11-26 | Working tree, T‑3090 | `scripts/dev/test.sh -rf` | 2581 passed, 1 failed (`test_openapi_schema` warning budget: three operationId collisions from the new list/detail routes), 1 skipped | 10m15s |
| 2026-11-26 | Working tree, T‑3090 fix | explicit operation ids on the three list views; `tests/test_openapi_schema.py` + `tests/test_ai_ops.py` | **23 passed**; full suite therefore 2582 passed, 1 skipped, 0 failed | — |
| 2026-11-26 | Working tree, T‑3090 | `makemigrations --check`; `manage.py check` | No changes after `ai 0010`; no issues | — |
| 2026-11-26 | Working tree, T‑3030 | `scripts/dev/test.sh -rf` | **2593 passed, 1 skipped, 0 failed**, 303 subtests (11 pipeline-stage tests) | 9m40s |
| 2026-11-26 | Working tree, T‑3030 | `makemigrations --check`; `manage.py check` | No changes after `leads 0025`; no issues | — |
| 2026-11-26 | Working tree, T‑3040 | `scripts/dev/test.sh` | **2607 passed, 1 skipped, 0 failed**, 303 subtests (14 assignment-rule tests) | 14m50s |
| 2026-11-26 | Working tree, T‑3040 | `makemigrations --check`; `manage.py check`; `spectacular --validate` | No changes after `leads 0026`; no issues; no warnings from the new views | — |
| 2026-11-26 | Working tree, T‑3100a | `scripts/dev/test.sh tests/ apps/ -rf` | **2616 passed, 1 skipped, 0 failed**, 303 subtests (9 unified-contact tests; erasure/export tests extended) | 15m12s |
| 2026-11-26 | Working tree, T‑3100a | `makemigrations --check`; `manage.py check`; `spectacular --validate` | No changes after `leads 0027`; no issues; only the existing `LeadViewSet` path-id warning (budget test passes) | — |
| 2026-11-26 | Working tree, T‑3100b | `scripts/dev/test.sh tests/ apps/ -rf` | **2624 passed, 1 skipped, 0 failed**, 303 subtests (8 message-log tests) | 10m03s |
| 2026-11-26 | Working tree, T‑3100b | `makemigrations --check`; `manage.py check`; `migrate` → `migrate leads 0026` → `migrate leads` on Postgres; `backfill_contact_messages --check` | No changes after `leads 0028`; no issues; forward, reverse and forward again succeed (view dropped and re-created each way); check prints `pending=0` | — |
| 2026-11-26 | Working tree, T‑4010 | `scripts/dev/test.sh tests/ apps/ -rf` | **2633 passed, 1 skipped, 0 failed**, 303 subtests (9 investment tests) | 9m59s |
| 2026-11-26 | Working tree, T‑4010 | `makemigrations --check`; `manage.py check`; `spectacular --validate` | No changes after `ai 0011`; no issues; schema error count within the `tests/test_openapi_schema.py` budget | — |
| 2026-11-26 | Working tree, T‑4020 | `scripts/dev/test.sh tests/ apps/ -rf` | **2638 passed, 1 skipped, 1 failed** (`test_viewings.py::VisitorTest::test_cancel_frees_the_slot`, KI‑125; the file passes 43/43 when rerun twice), 303 subtests (6 price-context tests) | 10m37s |
| 2026-11-26 | Working tree, T‑4020 | `makemigrations --check`; `manage.py check` | No changes; no issues | — |
| 2026-11-26 | Working tree, T‑4030 | `scripts/dev/test.sh tests/ apps/ -rf --tb=long` | **2645 passed, 1 skipped, 0 failed**, 303 subtests (6 narrative tests); KI‑125 test passed | 9m60s |
| 2026-11-26 | Working tree, T‑4030 | `makemigrations --check`; `manage.py check` | No changes after `ai 0012`; no issues | — |
| 2026-11-26 | Working tree, T‑4060 | `scripts/dev/test.sh tests/ apps/ -rf --tb=long` | 2650 passed, 1 skipped, **1 failed**: `test_openapi_schema` warning budget (29 > 28) — the new `DecisionSerializer` clashed by name with moderation's. Renamed `OfferDecisionSerializer`; `test_openapi_schema.py` + `test_lead_offers.py` then **18 passed** (rename only, no other change) | 10m11s |
| 2026-11-26 | Working tree, T‑4060 | `makemigrations --check`; `manage.py check` | No changes after `leads 0029`; no issues | — |
| 2026-11-26 | Working tree, T‑4050 (also confirms T‑4060) | `scripts/dev/test.sh tests/ apps/ -rf --tb=long` (run alone) | 2656 passed, 1 skipped, **2 failed**, both guard tests hit by T‑4050: staged contract migrations had to be renumbered on top of `0051_duplicate_clusters`, and `Property.duplicate_key` needed a `PLAIN` allow-list entry. Fixed; `test_listing_contract.py` + `test_mvp_data_protection.py` + `test_duplicates.py` **42 passed**. The T‑4060 schema-budget fix held | 10m18s |
| 2026-11-26 | Note | A targeted run started while a full run was in progress shares the test database and corrupts both (deadlocks, mass failures); the aborted confirmation run was discarded. Run one suite at a time | — |
| 2026-11-26 | Working tree, T‑4050 | `makemigrations --check`; `manage.py check` | No changes after `properties 0051`; no issues | — |
| 2026-11-27 | `b1b5dd0` (clean tree), session start | `scripts/dev/test.sh tests/ apps/ --tb=long` (run alone) | **2658 passed, 1 skipped, 0 failed**, 303 subtests | 10m31s |
| 2026-11-27 | Working tree, T‑4051 | `scripts/dev/test.sh tests/ apps/ -rf --tb=long` | 2667 passed, 1 skipped, **1 failed**: `test_viewings.py::ChangeToolTest::test_lists_only_own_upcoming_viewings` (`Unknown slot.`), passes alone (43/43). Root cause KI‑125 (PII masking of slot ids), fixed | 10m11s |
| 2026-11-27 | Working tree, T‑4051 + KI‑125 fix | `scripts/dev/test.sh tests/ apps/ -rf --tb=long` (run alone) | **2669 passed, 1 skipped, 0 failed**, 303 subtests (10 duplicate-search tests, 1 slot-masking test) | 10m22s |
| 2026-11-27 | Working tree, after the `_also_listed` ordering fix | `test_search_duplicates.py` + `test_listing_search.py` + `test_public_listings.py` | **75 passed** (11 duplicate-search tests) | 20s |
| 2026-11-27 | Working tree | `makemigrations --check --dry-run`; `manage.py check` | No changes; no issues | — |
| 2026-11-27 | `8586a35` + working tree: KI‑126 alerts, T‑1092 | `scripts/dev/test.sh tests/ apps/ -rf --tb=long` (run alone) | **2678 passed, 1 skipped, 0 failed**, 303 subtests (+4 alert, +4 `ApiErrorTest`); covers the earlier `_also_listed` ordering fix | 10m22s |
| 2026-11-27 | Working tree | `makemigrations --check --dry-run`; `manage.py check` | No changes; no issues (first attempt hit a Docker `ENOMEM` on the bind mount; rerun clean) | — |
| 2026-11-27 | `61fb555` + working tree: T‑1091 batch 1 | `scripts/dev/test.sh tests/ apps/ -rf --tb=long` (run alone) | **2682 passed, 1 skipped, 0 failed**, 303 subtests (+3 user routes, +1 schema) | 11m12s |
| 2026-11-27 | Working tree | `makemigrations --check --dry-run`; `manage.py check`; `spectacular --validate` | No changes; no issues; schema valid (generator errors 98, warnings 22) | — |
| 2026-11-27 | `d0b6d15` + working tree: T‑1091 batch 2 | `scripts/dev/test.sh tests/ apps/ -rf` (run alone) | 2684 passed, 1 skipped, **1 failed**: `test_membership_phase_c::AdminUserReadersTest::test_no_new_readers` — a schema description in `organizations/openapi.py` named `admin_user`; reworded (guard kept as is), affected suites rerun: 30 passed | 10m42s |
| 2026-11-27 | Working tree | `makemigrations --check --dry-run`; `manage.py check`; `spectacular --validate` | No changes; no issues; schema valid (generator errors 29 unique, warnings 11) | — |
| 2026-11-27 | Working tree: T‑1091 batch 2 (after the fix) | `scripts/dev/test.sh tests/ apps/ -rf` (run alone) | **2685 passed, 1 skipped, 0 failed**, 303 subtests (+2 config routes, +1 schema) | 15m31s |
| 2026-11-27 | `e9782ac` + working tree: T‑1091 batch 3 | `scripts/dev/test.sh tests/ apps/ -rf` (run alone) | **2686 passed, 1 skipped, 0 failed**, 303 subtests (+1 schema) | 10m04s |
| 2026-11-27 | Working tree | `makemigrations --check --dry-run`; `manage.py check`; `spectacular --validate --fail-on-warn` | No changes; no issues; schema valid with **0 errors, 0 warnings** | — |

### Session 35, continued (T‑6010)

| Date | Code state | Command | Result | Duration |
|---|---|---|---|---|
| 2026-11-27 | Working tree, T‑6010 | `scripts/dev/test.sh tests/ apps/` (alone) | **2704 passed, 1 skipped, 0 failed** | 10 min 32 s |
| 2026-11-27 | Working tree | `tests/test_backups.py` (+ `test_slo`, `test_runbooks`, `test_deploy_pipeline`, `test_mvp_data_protection`) | 18 passed; affected suites passed | ~45 s |
| 2026-11-27 | Working tree | `promtool test rules tests/slo-rules.test.yaml`; `promtool check config` | SUCCESS; 64 rules | — |
| 2026-11-27 | Working tree | `shellcheck -x scripts/ops/*.sh` | Clean | — |
| 2026-11-27 | Working tree | Real drill: throwaway compose project (`postgres:15-alpine` db, web from the test image), `backup.sh` then `restore_drill.sh` | Backup ~554 KB, OpenPGP output (no `PGDMP`/SQL visible), modes 600/700; drill restored in 3–4 s, migrations=311, users=4, organizations=3. Tampered file → checksum mismatch, exit 1, recorded failed; wrong key → exit 1, one failed record; key file 644 → refused; db down → partial removed; retention removed a >14-day file; upload hook copied dump + `.sha256`; no drill containers left | — |
| 2026-11-27 | — | On a real server with systemd timers and an off-host destination | **Not run**: needs the owner's servers and storage (checklist §71) | — |

| 2026-11-27 | Working tree (session 36, T‑6040) | Full suite `tests/ apps/` (Docker, alone) | **2719 passed, 1 skipped, 0 failed** (+15: `tests/test_release_check.py` 13, `tests/test_deploy_pipeline.py` +2) | — |
| 2026-11-27 | Working tree | `tests/test_release_check.py tests/test_deploy_pipeline.py` rerun after a comment-only edit made during the full run | 32 passed | — |
| 2026-11-27 | Working tree | `makemigrations --check`; `manage.py check` | No changes; no issues | — |
| 2026-11-27 | Working tree | `shellcheck scripts/deploy/remote_deploy.sh` (koalaman/shellcheck:stable); `bandit -ll` (CI level) on the new modules | Clean; clean (two `nosec B104` on compared, not bound, `0.0.0.0` strings) | — |
| 2026-11-27 | Working tree | `check_release` with `config.settings.prod` and placeholder env in the test image | 10 FAIL / 5 WARN by setting name, exit 1, no values printed | — |
| 2026-11-27 | — | `check_release` on the real staging/production servers | **Not run**: needs the owner's servers and `.env` (checklist §72) | — |

## Coverage
Not measured yet (no coverage run executed). CI is configured with `--cov=apps`.

## Not covered / gaps
- SLO alerts are proven with promtool on synthetic series; no Prometheus has scraped a running stack end to end, and production scrape targets do not exist yet (KI‑109).
- Outbound WhatsApp is tested with `requests.post` patched; no test hits Meta. The tests used to patch `WhatsAppClient` methods at class level, which hid KI‑033; new tests go through the real client.
- Pending‑action claims are tested with a stale second instance, not with two concurrent threads.
- No test runs against a real LLM provider (all tests mock or use local backend paths).
- Gemini AFC multi‑round token counting is not verifiable without the provider (KI‑006 residual).
- Inbound retries are tested by simulating broker redelivery (`apply(retries=n)`): Celery 5.4 eager mode does not execute retries (KI‑031). The DB‑starvation chaos run has not been repeated.
- CI workflow changes were validated as YAML and the health script locally; the workflow has not been run on GitHub Actions.
- Never edit source while a full suite is running: the container bind‑mounts the tree (see the invalid run on 2026‑11‑23).
