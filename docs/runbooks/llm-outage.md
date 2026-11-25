# Runbook: LLM outage

**Alerts:** `LLMUnavailable` (critical: no provider answered), `LLMProviderFailing` (warning: one provider failing), `AIHighLatency`, `AIRequestsDrop`, the `ai_reply_latency` SLO.

**What users see:** while every provider fails, the assistant answers *"I'm having a bit of trouble right now — please try again in a moment."* Nothing is booked or sent. Direct answers (simple lookups) and cached FAQ answers keep working. A turn that already ran a committing tool is never retried on another provider.

## 1. Confirm (5 minutes)
1. Grafana → *RealTron — SLOs* → *AI reply*; Prometheus: `sum by (provider, error) (rate(realtron_llm_provider_failures_total[5m]))`.
2. The `error` label tells the cause: `Timeout`/`ConnectionError` → provider or network; `PermissionDenied`, `Unauthenticated`, `ResourceExhausted` → key or quota.
3. Check the provider's status page.
4. Check the assistant is not simply stopped: `python manage.py ai_kill_switch list`.

## 2. Mitigate
- **Quota or key** (`ResourceExhausted`, `PermissionDenied`): fix the key (`GEMINI_API_KEY`, or `AI_OPENAI_API_KEY` for the OpenAI-compatible provider) or the quota in the provider console, then restart web and workers.
- **Provider down, a second provider is contracted:** set `AI_PROVIDER_ORDER`, e.g. `gemini,openai`, with `AI_OPENAI_BASE_URL`, `AI_OPENAI_API_KEY` and `AI_OPENAI_MODEL` (GO_LIVE_CHECKLIST §28), and restart. The router then fails over by itself. Each provider has a breaker (`AI_BREAKER_FAILURES` failures within `AI_BREAKER_SECONDS`); a provider with an open breaker is tried last.
- **Slow, not down:** lower `AI_TIMEOUT_S` (default 30) so users get the fallback reply sooner.
- **Long outage:** stop the assistant so people get the honest paused reply instead of retries: `python manage.py ai_kill_switch stop --platform --reason "LLM provider outage"` (or `--market PK`, `--org <uuid>`). The stop works on the next call everywhere and is audited.

## 3. Recover
1. Provider answers again → reset the service breaker: `POST /api/v1/sla/circuits/llm_provider/reset/` (platform admin). `GET /api/v1/sla/status/` shows the state.
2. Release any stop: `python manage.py ai_kill_switch list`, then `python manage.py ai_kill_switch release <id>`.
3. Watch `LLMUnavailable` resolve and the `ai_reply_latency` ratio return under budget.
4. Check cost: a failover provider has its own price. Enter its `ModelPrice` row so `GET /api/v1/ai/costs/` stays right.

## 4. Afterwards
- Run the deterministic evals against the release: `python manage.py run_ai_evals`.
- Record the outage window: it counts against the AI reply objective.
