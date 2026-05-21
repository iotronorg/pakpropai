from rest_framework.throttling import AnonRateThrottle, UserRateThrottle


class OtpSendThrottle(AnonRateThrottle):
    scope = 'otp_send'

    def get_cache_key(self, request, view):
        phone = request.data.get('phone', '')
        if phone:
            return f'throttle_otp_{phone}'
        return super().get_cache_key(request, view)


class OtpDailyThrottle(AnonRateThrottle):
    """Hard cap: 10 OTP requests per phone number per day."""
    scope = 'otp_daily'

    def get_cache_key(self, request, view):
        phone = request.data.get('phone', '')
        if phone:
            return f'throttle_otp_daily_{phone}'
        return super().get_cache_key(request, view)


class AiQueryThrottle(UserRateThrottle):
    scope = 'ai_query'


class FraudCheckThrottle(UserRateThrottle):
    scope = 'fraud_check'


# ── Role-aware throttles (admins bypass, agents/developers are rate-limited) ──

class RoleAwareUserThrottle(UserRateThrottle):
    """
    Base throttle that exempts admins entirely.
    Admins operate the platform and must not be locked out by their own tooling.
    All other authenticated roles are subject to the configured rate.
    """
    def get_cache_key(self, request, view):
        user = request.user
        if user and user.is_authenticated and getattr(user, 'role', '') == 'admin':
            return None  # returning None disables throttling for this request
        return super().get_cache_key(request, view)


class PropertySearchThrottle(RoleAwareUserThrottle):
    """30 searches/min — prevents hammering DB + scraper trigger spam."""
    scope = 'property_search'


class ReportGenerateThrottle(RoleAwareUserThrottle):
    """5 reports/hour — each report runs AI + Celery + PDF generation."""
    scope = 'report_generate'


class BulkOperationThrottle(RoleAwareUserThrottle):
    """10 bulk ops/min — bulk assign/reject touch many rows at once."""
    scope = 'bulk_operation'


class ScorePropertyThrottle(RoleAwareUserThrottle):
    """15 rescores/min — each rescore queues an AI Celery task."""
    scope = 'score_property'


class WhatsAppWebhookThrottle(AnonRateThrottle):
    """
    Per-IP throttle on the WhatsApp inbound webhook.
    Limits DoS amplification and runaway Gemini API costs from spoofed traffic.
    Signature verification is the primary guard; this is a cost-cap backstop.
    """
    scope = 'whatsapp_webhook'

    def get_cache_key(self, request, view):
        # Key by the phone_number_id in the payload so legitimate high-volume
        # orgs on separate numbers don't share a bucket.
        try:
            import json
            body    = json.loads(request.body.decode())
            pn_id   = (
                body.get('entry', [{}])[0]
                    .get('changes', [{}])[0]
                    .get('value', {})
                    .get('metadata', {})
                    .get('phone_number_id', '')
            )
            if pn_id:
                return f'throttle_wa_webhook_{pn_id}'
        except Exception:
            pass
        return super().get_cache_key(request, view)
