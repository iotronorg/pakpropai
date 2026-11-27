# Runbooks

MVP criterion 15. Every alert in `ops/prometheus-alerts.yaml` has a `runbook` annotation that points here; `tests/test_runbooks.py` checks that each one exists.

| Situation | Runbook | Alerts |
|---|---|---|
| No AI provider answers, or replies are slow | [llm-outage.md](llm-outage.md) | `LLMUnavailable`, `LLMProviderFailing`, `AIHighLatency`, `AIRequestsDrop` |
| WhatsApp token expired or revoked | [whatsapp-token-expiry.md](whatsapp-token-expiry.md) | `WhatsAppTokenInvalid`, `WhatsAppAuthErrors`, `EnquiryNotificationBacklog` |
| Personal data exposed, lost or changed | [data-incident.md](data-incident.md) | `WebhookSignatureFailures`; usually reported by a person |
| The assistant said something harmful or false | [harmful-ai-output.md](harmful-ai-output.md) | `AIGuardrailBlocksSpike`; usually reported by a person |
| Setting up, approving or rolling back a deploy | [deploy.md](deploy.md) | A failed CI deploy job |
| Backups, restore drills, restoring production | [backups.md](backups.md) | `BackupMissing`, `BackupFailed`, `RestoreDrillOverdue`, `BackupStatusAbsent` |

**Commands** run inside a web or worker container: `docker compose -p realtron --env-file .env -f docker/docker-compose.prod.yml exec web python manage.py …`, from the deploy directory. **Admin endpoints** need a platform admin; the kill switch takes `super_admin`, `ai_admin` or `ops_admin`.

Write down in the incident notes: when it started, what you saw (alert, graph, report), each action and its time, and when service came back. Do not paste message text, phone numbers or tokens into tickets or chat.

## Other alerts

### sloerrorbudgetburnfast
### sloerrorbudgetburnslow
An objective (label `slo`, and `channel` for notifications) is missing its target. Open Grafana → *RealTron — SLOs* and find the row for that objective.
- `api_availability`: look at *HTTP Error Rate* on the overview dashboard and at the web logs by `request_id`. A recent deploy is the usual cause: roll back.
- `api_latency`, `search_latency`: look at the database (slow queries, connections) and at the latest migration. Search uses the indexes from T‑1100 and T‑1070f.
- `ai_reply_latency`: see [llm-outage.md](llm-outage.md).
- `webhook_processing`, `webhook_queue_delay`: the `high-resource` worker is down or behind. Check `docker compose ps`, the worker logs, and the Redis broker. Inbound messages that failed on database errors are in the dead-letter list: `python manage.py replay_whatsapp_dead_letters --count`, then without `--count` to replay them.
- `enquiry_notification`: run `python manage.py notification_delivery_report --hours 2`. WhatsApp late or failing → [whatsapp-token-expiry.md](whatsapp-token-expiry.md). Email failing → the SMTP provider (`EMAIL_HOST`). Everything late → the `outbound` worker or beat (`relay-outbox`) is not running.

### outboxbacklogstale
Undelivered outbox messages older than 15 minutes. Check that beat runs (`relay-outbox` every minute) and that a worker reads the outbox queue (`OUTBOX_CELERY_QUEUE`, default `default`). If WhatsApp is failing, see [whatsapp-token-expiry.md](whatsapp-token-expiry.md).

### metricstargetdown
### workermetricsabsent
Prometheus cannot read a web or worker container, so the objectives it measures are blind. Check the container is running, that `ops/targets.prod/*.yml` lists it, that the worker has `WORKER_METRICS_PORT=9808` and `PROMETHEUS_MULTIPROC_DIR`, and that `ops/secrets/metrics_token` matches the app's `PROMETHEUS_METRICS_TOKEN`. In Prometheus, *Status → Targets* shows the error.

### higherrorrate
### highp95latency
### databasedown
### highcachemissrate
Platform health. Check `GET /health/`, the database and Redis containers, and the latest deploy. Roll back a bad deploy first, investigate after.

### highdeallockcancellationrate
Deal lock is frozen in the MVP (`feature_deal_lock` off). If this fires, someone enabled it: check the `system_config` table.

### whatsappmessagevolumespike
More than 100 inbound messages per second. Usually a campaign reply wave or abuse. The webhook drops floods per phone (`WA_RATE_LIMIT_PER_MINUTE`). If it is abuse, stop the assistant for the affected organization (see [harmful-ai-output.md](harmful-ai-output.md), step 1) to protect the AI budget.
