# Security review and pentest scope (T‑6030)

MVP_SCOPE §6 criterion 10 (no plaintext secrets or national IDs), criterion 12 (pentest on the public API and chat with no open high/critical findings), TR‑19 "Security". First pass: session 37, 2026‑11‑27, on the working tree after T‑6040. Repeat §1–§3 before each release candidate; §4 is for the external tester.

## 1. Static scans

| Scan | Command (as CI runs it) | Result |
|---|---|---|
| Bandit | `bandit -r apps/ -ll` (`bandit[toml]==1.8.*`) | Before: 0 high, 20 medium. After triage: **0** (2 fixed, 18 false positives annotated with a reason) |
| pip-audit | `pip-audit -r requirements/prod.txt` (`pip-audit==2.*`) | **No known vulnerabilities** (123 packages) |
| TruffleHog | CI only (verified secrets in git history) | Not run locally; runs on every push |

### Bandit triage
| Finding | Where | Verdict |
|---|---|---|
| B310 `urlopen` on the org logo URL | `apps/audit/pdf.py` | **Fixed.** `urlopen` accepts `file://` and internal addresses; the PDF worker now gets the logo as bytes read through the storage backend (2 MB cap) and never fetches a URL. Side effect: logos now also appear when media is on local disk (the old relative URL never loaded) |
| B310 `urlopen` on the stored report | `apps/audit/tasks.py` | **Fixed.** The URL is ours (Cloudinary), but the download now refuses anything but `https://` and caps the size at 20 MB |
| B310 `urlopen(OLLAMA_BASE_URL)` ×2 | `apps/core/views.py`, `check_system_health` | False positive: operator configuration, not user input |
| B314 `ElementTree.fromstring` | `sync_sanctions` | Accepted: fixed https government sources; stdlib ElementTree does not resolve external entities, and expat 2.9.0 in the image caps entity expansion |
| B608 / B611 SQL built with f-strings ×15 | `apps/core/crypto.py`, `apps/geo/{search,sql}.py`, `apps/properties/listing_contract.py`, three migrations | False positives: only module constants or `quote_name`d identifiers are interpolated; every value is a `%s` parameter |
| B104 `'0.0.0.0'` | `apps/core/views.py` | False positive: compared, not bound |

Each false positive carries `# nosec <id> - <reason>` on the flagged line, so a new finding stands out.

## 2. Permission matrix

`tests/test_endpoint_matrix.py` calls **every DRF endpoint under `/api/` (427 endpoint × method pairs on 338 routes, including views hidden from the OpenAPI schema)**:

- **Anonymous:** must answer 401/403 unless it is in `PUBLIC` with a reason. 30 entries: sign-in and sign-up steps, provider webhooks (each verifies a signature or reads the result back, and fails closed), the public listing/search/price-context/calculator reads, the CSRF cookie, the alert unsubscribe link and the login-page theme. A new endpoint that is public by accident fails the test, and a stale `PUBLIC` entry fails too.
- **Roles:** the declared permission classes give each endpoint an audience (`apps/security/permission_matrix.py`): staff 56, moderator 11, org admin 55, dashboard 40, API key 4, signed-in 224, public 37 (7 of the declared-public ones refuse in the view). A consumer, an org agent and an org owner must be refused outside their audience. A **control** test checks that the right role gets past the check on every such `GET`, so a broken test user cannot make the refusals pass.
- Outbound network is blocked during the test; refusals happen before any handler runs.
- `manage.py permission_matrix` prints the table (Markdown or `--json`) for reviewers and the tester.

**Not covered by the matrix:** whether a signed-in user can reach *another organization's* records through the 224 signed-in endpoints (scoping lives in querysets and object checks). 47 test files cover cross-organization isolation module by module (for example `test_whatsapp_multitenant.py`, `test_syndication_isolation.py`, `test_privacy_breach_regression.py`, `test_security_pentest.py`); the external pentest should still focus there (§4).

**Webhooks:** the subscription webhooks (`/payments/webhook/{safepay,bsecure}/`) answer **200 `activated: false`** to an unsigned call; they verify the signature in `PaymentService.handle_webhook` and refuse when the secret is unset. Not a bypass, but a tester will see 200.

## 3. Data scan

`manage.py scan_plaintext` (every text column: unencrypted values in encrypted columns, and CNIC, Emirates ID, private keys, Stripe/Google/Meta/Twilio keys, JWTs in plain columns).

Run on a throwaway database: migrate, `seed_data`, `scripts/e2e/seed.py`, then secrets written through `SystemConfigService` (Meta, Gemini, Stripe), CNIC and Emirates ID on user and agent records, and a CNIC in chat text. **Result: none found.** Positive control: a CNIC planted in `properties.description` and a Stripe-style key in `leads.notes` were both reported (exit 1).

Found while preparing it: `seed_data` had been failing on its first listing (`property_ownership_consistency`: owner set, owner type left at `organization`). Fixed; the seed listings are now client listings.

**Production:** run `manage.py scan_plaintext` on staging and production after the session‑26 encryption migrations; it prints counts only. Free text a person types (a CNIC in a listing description) is not encrypted by design; it is masked on input by `PIIMaskingMiddleware` for its per-endpoint allow-list (owner decision 2026‑11‑23, T‑1033; ADR‑047).

## 4. External pentest scope (MVP §6.12)

**For the owner to fill in:** vendor, dates, tester source IPs, contacts (`<on-call>`, `<privacy owner>`), and the report channel.

### Target
- **Environment:** staging only, on the release-candidate build, with `check_release --environment staging` passing. Production is out of scope.
- **Surfaces:**
  - REST API `https://<staging-api>/api/v1/` (`manage.py permission_matrix` lists every route and who may call it; `/api/v1/schema/` with a staff account).
  - Web chat: `/api/v1/conversations/` (streaming replies).
  - WebSockets: `/ws/agent/org/{org_id}/`, `/ws/agent/copilot/session/{session_id}/` (JWT, allowed-hosts origin check).
  - Inbound webhooks: WhatsApp `/api/v1/whatsapp/webhook/` (Meta signature), payments, billing, verification.
  - The AI assistant through chat (and WhatsApp, if a test number is available).
- **Accounts provided:** a consumer; an agent, an org admin and an owner in organization A, and the same in organization B; one platform admin per platform role (super, ops, AI, compliance, billing, moderator); an organization API key per scope.

### Focus
1. **Cross-organization access (IDOR):** org B reading or changing org A's leads, enquiries, viewings, listings, messages, members, exports, reports and API keys, including through `X-Organization-ID`.
2. **Authentication:** OTP (rate limits, replay, enumeration), password reset, TOTP MFA, JWT cookie and refresh rotation, logout, session fixation.
3. **The AI assistant:**
   - Prompt injection through chat, listing text and documents.
   - Side-effect tools (enquiry, booking, saved search, listing draft) running without a confirmation record (MVP §6.8).
   - Data from another user or organization in replies.
   - Ungrounded listing facts.
   - Cost abuse (daily limits).
4. **Public endpoints (§2):** scraping limits, sandbox or unpublished listings leaking, injection in search criteria.
5. **Uploads:** images and verification documents (type confusion, decompression bombs, paths, stored XSS through file names).
6. **Privacy:** data export and erasure reach only the requester's data; the PII masking middleware; error responses do not leak details.
7. **Webhooks:** forged or replayed provider calls.
8. **Frozen modules** (deal lock, voice calls, syndication, portal scraping) are off: confirm that they refuse, not how they behave when on.

### Out of scope
- Denial of service and load tests.
- Social engineering and physical access.
- The providers' own systems (Meta, Stripe, Safepay, bSecure, Google, Cloudinary, the LLM vendors).
- The Docker hosts and monitoring stack beyond what the public URLs expose. A separate host review is the owner's choice.

### Rules
- Test data only.
- Stop and report at once on any access to real personal data or a critical finding.
- Rate limits stay on (raise them per tester IP only if agreed).
- Severity by CVSS 3.1.
- Every high/critical is retested after the fix. The criterion is met when none is open.

### Before the test
- [ ] Staging runs the release candidate, and `check_release` passes.
- [ ] Test organizations, accounts and API keys are created and listed for the tester.
- [ ] The tester IPs are known to on-call, and alerts are expected during the window.
- [ ] Backups of staging have run (checklist §71).
