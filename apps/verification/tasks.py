import logging
from celery import shared_task
from django.utils import timezone

logger = logging.getLogger(__name__)


@shared_task
def notify_verification_status_change(verification_id: str):
    """
    Called after an admin reviews a verification (approve / reject / dispute).
    Notifies the property owner and assigned agent of the outcome.
    """
    from .models import Verification
    from apps.notifications.services import notify_user

    try:
        v = (
            Verification.objects
            .select_related('property__owner', 'property__assigned_agent__user')
            .get(pk=verification_id)
        )
    except Verification.DoesNotExist:
        logger.warning(f"notify_verification_status_change: verification {verification_id} not found")
        return

    prop = v.property
    if not prop:
        return

    messages = {
        'passed': (
            'Property Verification Approved',
            f'✅ Your property *"{prop.title}"* has been verified successfully.',
        ),
        'failed': (
            'Property Verification Failed',
            f'❌ Your property *"{prop.title}"* could not be verified. '
            'Please review the feedback and re-submit.',
        ),
        'disputed': (
            'Property Verification Disputed',
            f'⚠️ Your property *"{prop.title}"* has been flagged as disputed. '
            'Contact support for further details.',
        ),
    }
    title, message = messages.get(
        v.status,
        ('Verification Update', f'Verification status for "{prop.title}" changed to {v.status}.'),
    )

    recipients = []
    if prop.owner:
        recipients.append(prop.owner)
    if prop.assigned_agent and prop.assigned_agent.user and prop.assigned_agent.user != prop.owner:
        recipients.append(prop.assigned_agent.user)

    for user in recipients:
        try:
            notify_user(user, title=title, message=message, event_type='report_ready')
        except Exception as exc:
            logger.warning(
                f"notify_verification_status_change: failed to notify user {user.pk}: {exc}"
            )


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def run_verification_task(self, verification_id: str, image_bytes: bytes = None,
                          mime_type: str = 'image/jpeg'):
    from .models import Verification
    from apps.ai.scoring import ocr_document as _ai_ocr_document
    import json

    try:
        v = Verification.objects.get(id=verification_id)
    except Verification.DoesNotExist:
        logger.warning(f"Verification {verification_id} not found")
        return

    if not image_bytes:
        v.status = Verification.Status.FAILED
        v.notes  = 'No document image provided.'
        v.save()
        return

    try:
        raw = _ai_ocr_document(image_bytes, mime_type=mime_type)
        try:
            ocr_data = json.loads(raw)
        except Exception:
            ocr_data = {'raw': raw}

        flags = ocr_data.get('tamper_flags', []) if isinstance(ocr_data, dict) else []
        v.ocr_data    = ocr_data if isinstance(ocr_data, dict) else {'raw': raw}
        v.fraud_flags = flags
        v.status      = Verification.Status.FAILED if flags else Verification.Status.PASSED
        v.verified_at = timezone.now()
        v.save()

        from .services import VerificationSignalService
        VerificationSignalService.refresh(v)
        logger.info(f"Verification {verification_id} → {v.status} ({len(flags)} flags)")
    except Exception as exc:
        logger.error(f"Verification {verification_id} failed: {exc}")
        raise self.retry(exc=exc)
    