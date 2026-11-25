# Runbook: harmful AI output

**Harmful output** is an assistant answer that is offensive, discriminatory, dangerous or misleading. It includes invented listing facts, prices or legal advice; another person's data; or text injected by a lister and repeated to a user.

**Signals:** a user or agent report (moderation queue), `AIGuardrailBlocksSpike`, grounding fallbacks in the AI traces, a screenshot on social media.

## 1. Stop it (minutes)
Stop the assistant as narrowly as the problem allows. A stop works on the next call everywhere and is audited; users get the paused reply.
- One organization: `python manage.py ai_kill_switch stop --org <uuid> --reason "harmful output, ticket …"`
- One market: `--market PK`
- Everywhere: `--platform`
- The API does the same: `POST /api/v1/ai/kill-switches/` (super_admin, ai_admin or ops_admin).

If it was caused by one listing's text (injection), suspend that listing in the moderation queue. Lister text is passed to the model as delimited data (ADR‑044), but report the case so the eval suite gets it.

## 2. Find the turn
1. Django admin → *AI traces*: filter by time and organization. A trace has the route, prompt version, provider and model, tool calls, guardrail and grounding results, and any fallback. It holds no message text.
2. The conversation (*Conversations* in the admin) holds the encrypted messages. Open only the one conversation you need, and note in the incident that you did.
3. Decide the cause:
   - **Prompt:** look at the prompt version in the trace.
   - **Model:** did the provider change? Was a failover used?
   - **Retrieved data:** a listing's text, or another person's data.
   - **Missing guardrail.**

## 3. Fix
- **Prompt:** prompts are versioned in `apps/ai/prompts.py`. Roll back to the previous version, or fix it, through a normal deploy.
- **Model:** pin `AI_PROVIDER_ORDER` to the provider that behaved, and look for a model update at the provider.
- **Data:** correct or suspend the listing; for personal data, follow [data-incident.md](data-incident.md).
- Add the conversation pattern, without personal data, as an eval case in `apps/ai/evals/`. Then run `python manage.py run_ai_evals`; it must pass before the stop is released.

## 4. Release and follow up
1. `python manage.py ai_kill_switch list`, then `python manage.py ai_kill_switch release <id>`.
2. Answer the person who reported it.
3. If people were misled about a property, tell the organization that owns the listing.
4. Record it in `KNOWN_ISSUES.md`: cause, fix and the eval case added.
