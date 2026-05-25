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


@shared_task
def generate_certificate_task(verification_id: str):
    """
    Generate and store a trust certificate PDF for a passed Verification.
    Non-critical: failures are logged but do not affect the verification result.
    """
    from .models import Verification
    from .certificate import generate_trust_certificate

    try:
        verification = (
            Verification.objects
            .select_related('property__organization', 'reviewer')
            .get(pk=verification_id)
        )
    except Verification.DoesNotExist:
        logger.warning(f"generate_certificate_task: verification {verification_id} not found")
        return

    if verification.status != Verification.Status.PASSED:
        logger.info(
            f"generate_certificate_task: skipping {verification_id} — "
            f"status is {verification.status}, not passed"
        )
        return

    try:
        import cloudinary.uploader

        public_id = f'trust_certificates/{verification_id}'

        # First pass: generate PDF without QR code, upload to get the stable URL
        pdf_bytes_v1 = generate_trust_certificate(verification, qr_url='')
        result = cloudinary.uploader.upload(
            pdf_bytes_v1,
            resource_type='raw',
            public_id=public_id,
            format='pdf',
            overwrite=True,
        )
        certificate_url = result['secure_url']

        # Second pass: regenerate with QR code pointing to the now-known URL
        pdf_bytes_v2 = generate_trust_certificate(verification, qr_url=certificate_url)
        cloudinary.uploader.upload(
            pdf_bytes_v2,
            resource_type='raw',
            public_id=public_id,
            format='pdf',
            overwrite=True,
        )

        verification.certificate_url = certificate_url
        verification.save(update_fields=['certificate_url'])
        logger.info(f"generate_certificate_task: certificate ready for {verification_id}")

    except Exception as exc:
        logger.warning(f"generate_certificate_task: failed for {verification_id}: {exc}")


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
    