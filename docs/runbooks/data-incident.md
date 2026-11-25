# Runbook: data incident

A **data incident** is personal data that is exposed, lost, changed or used by someone who should not have it. Examples: a leaked key, an API answering another tenant's data, a stolen admin account, a wrong export, or messages sent to the wrong person.

**Signals:** a person reports it; `WebhookSignatureFailures` (forged provider callbacks); the security engine's threat log; `scan_plaintext` finding values; an unexpected admin action in the audit log.

**Roles:** the incident lead (on call) runs this list. The privacy owner decides about notifying people and authorities. Keep the list of people who know small until the cause is understood.

## 1. Contain (first hour)
1. **Stop the leak first.** Roll back the deploy, turn off the feature (`system_config` flags), or block the endpoint at the load balancer.
2. **Leaked secret:** rotate it at the provider *before* anything else. Stripe, Safepay, bSecure, Meta (app secret and tokens: [whatsapp-token-expiry.md](whatsapp-token-expiry.md)), Cloudinary, Google Maps, model keys and SMTP. Organization API keys are revoked in the organization settings.
3. **Stolen account or session:** `python manage.py revoke_sessions --user +92300…` (repeatable) or `--all`. Access tokens still last up to 15 minutes; to end them at once, rotate `SECRET_KEY` and restart. That signs everyone out.
4. **Encryption key exposed** (`FIELD_ENCRYPTION_KEYS`):
   1. Make sure `FIELD_HASH_KEY` is set to a fixed value. Without it the command refuses, because the lookup hashes would change.
   2. Generate a key: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`.
   3. Put the new key **first** and keep the old one after it; deploy.
   4. `python manage.py rotate_field_keys`, then `python manage.py rotate_field_keys --dry-run`; it must report 0.
   5. Remove the old key and deploy.
   6. Back up first, and keep the old key with the backups taken before the rotation.
5. **Assistant leaking data** (one person's details shown to another): stop it at once (`python manage.py ai_kill_switch stop --platform --reason "data incident"`) and follow [harmful-ai-output.md](harmful-ai-output.md).

## 2. Find out what happened
- **Who and when:** the admin audit log (`AuditLog`: configuration, member and kill-switch changes), `PrivacyAuditLog` (exports, erasures), `SecurityTrace` (blocked requests), the web logs by `request_id` (every response has `X-Request-ID`).
- **Which records and whose:** query by organization, and count; do not copy data out.
- **Plain-text check:** `python manage.py scan_plaintext` (counts only).
- **Write down:** categories of data, number of people, markets, start and end time, cause.

## 3. Notify
- **GDPR markets (UK, EU):** the supervisory authority within **72 hours** of becoming aware, unless the breach is unlikely to put people at risk. Tell the affected people without undue delay when the risk is high. The privacy owner decides and keeps the record either way.
- **Other launch markets:** follow the local rules the privacy owner keeps; if in doubt, notify.
- **Organizations** whose customers are affected: tell their owner, who may have duties of their own.
- People who ask for their data or its deletion: `POST /api/v1/compliance/export/` (anyone signed in) and the erasure flow (ADR‑049; 30-day deadline).

## 4. Close
- Fix the cause and add a test for it.
- Record the incident and its decisions in `KNOWN_ISSUES.md` (no personal data).
- Review who has access to production and to the secrets that were involved.
