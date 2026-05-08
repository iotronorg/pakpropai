import logging
from celery import shared_task
from django.utils import timezone

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def run_verification_task(self, verification_id: str, image_bytes: bytes = None,
                          mime_type: str = 'image/jpeg'):
    from .models import Verification
    from services.ai_orchestrator import AIOrchestrator
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
        raw = AIOrchestrator.ocr_document(image_bytes, mime_type=mime_type)
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
    