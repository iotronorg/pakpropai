# Runbook: WhatsApp token expiry

**Alerts:** `WhatsAppTokenInvalid` (6-hourly check refused, label `scope` = `global` or `org`), `WhatsAppAuthErrors` (sends fail with 401/403), `EnquiryNotificationBacklog`, the `enquiry_notification` SLO on the `whatsapp` channel. Admins also get an email and an in-app notice from the token check. The notice never goes by WhatsApp, because WhatsApp is what has stopped.

**What breaks:** nothing goes out on that number. That includes replies, OTP codes, agent notifications and saved-search alerts. Inbound webhooks are still accepted and processed.

## 1. Confirm
1. Which token? `scope="global"` is `wa_access_token` in System Setup (SystemConfig). `scope="org"` is an organization's own number; the admin email names the organizations.
2. Prometheus: `sum by (status) (rate(realtron_whatsapp_api_errors_total[5m]))`. Status 401/403 means the token; `5xx`/`network` means Meta or the network (check the Meta status page, and do not change tokens).
3. Run the check now: `python manage.py shell -c "from apps.whatsapp.tasks import check_whatsapp_token_health as t; print(t())"`.

## 2. Replace the token
1. Meta Business Manager → System users → generate a token with `whatsapp_business_messaging` and `whatsapp_business_management`. Use a **system-user token that does not expire**; personal user tokens expire within hours or days.
2. **Global:** System Setup → WhatsApp API → `wa_access_token`. **Organization:** the organization's WhatsApp configuration (provisioning), or its admin re-connects the number.
3. Stored tokens are encrypted, and the config cache is refreshed on save. Run the check from step 1.3 again: it must print `ok`.
4. If `WhatsAppAuthErrors` keeps firing, the Meta circuit may be open: `POST /api/v1/sla/circuits/meta_cloud_api/reset/`.

## 3. What comes back by itself, and what does not
- **Agent and user notifications** that failed are re-queued every 30 minutes for 48 hours (`retry_failed_notifications`). The 24-hour customer-service window still applies.
- **OTP codes** that failed are not resent; the person asks for a new code.
- **Alerts and reminders** sent as plain text (`whatsapp.text`) that failed are not resent; their text is wiped after the last attempt.
- **Chat replies** that failed were buffered in Redis (`wa_buffer:<org>`, 24 hours) and are **not resent** (KI‑114). Agents see the conversations in the workspace and can follow up.
- Check `python manage.py notification_delivery_report --hours 6` once it is fixed.

## 4. Prevent
- Use system-user tokens only, and write the token's owner and permissions in the operations notes.
- After any Meta Business Manager change (people leaving, password resets, app review), run the check from step 1.3.
