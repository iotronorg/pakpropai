import logging
from celery import shared_task
from django.utils import timezone

logger = logging.getLogger(__name__)


@shared_task
def cleanup_expired_otps():
    """Delete OTPCode rows past their expiry. Run daily."""
    from .models import OTPCode
    deleted, _ = OTPCode.objects.filter(expires_at__lt=timezone.now()).delete()
    logger.info('cleanup_expired_otps: deleted %d expired OTP records', deleted)
    return deleted
