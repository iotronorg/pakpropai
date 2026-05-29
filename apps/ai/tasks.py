"""
Async Celery tasks for the AI app — security alerting, admin notifications,
and fire-and-forget token usage recording.
"""
import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(
    ignore_result=True,
    max_retries=3,
    default_retry_delay=10,
    queue='default',
)
def record_token_usage(
    org_id: str,
    tokens_in: int,
    tokens_out: int,
    model: str = 'llm',
    intent: str | None = None,
    cache_hit: bool = False,
) -> None:
    """Fire-and-forget: write TokenUsageRecord without blocking the WA response path."""
    try:
        from apps.ai.models import TokenUsageRecord
        from apps.organizations.models import Organization
        org = Organization.objects.get(id=org_id)
        TokenUsageRecord.objects.create(
            org=org,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            model=model,
            intent=intent,
            cache_hit=cache_hit,
        )
    except Exception as exc:
        logger.error('record_token_usage failed org=%s: %s', org_id, exc)


@shared_task(ignore_result=True, max_retries=2, default_retry_delay=10)
def alert_security_violation(phone: str, organization_id: str, violation_type: str) -> None:
    """
    Dispatched by GuardrailEngine on HIGH/CRITICAL violations.
    Notifies all active admin users via the standard notify_user pipeline
    (WhatsApp + in-app notification record).
    """
    from django.contrib.auth import get_user_model
    from apps.notifications.services import notify_user

    User = get_user_model()
    title = f"\U0001f6a8 Security Alert: {violation_type.replace('_', ' ').title()}"
    message = (
        f"\U0001f6a8 *Guardrail blocked a {violation_type} attempt.*\n\n"
        f"Phone: `{phone or 'unknown'}`\n"
        f"Organization: `{organization_id or 'none'}`\n"
        f"Violation: `{violation_type}`\n\n"
        "Check the Admin → Security Traces panel for full details."
    )

    admins = list(User.objects.filter(role='admin', is_active=True).values_list('id', flat=True)[:10])
    if not admins:
        logger.warning("alert_security_violation: no active admin users found")
        return

    for admin_id in admins:
        try:
            admin = User.objects.get(id=admin_id)
            notify_user(admin, title=title, message=message, event_type='lead_updates')
        except Exception as exc:
            logger.error("alert_security_violation: notify admin %s failed: %s", admin_id, exc)
