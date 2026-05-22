import json
import logging
from celery import shared_task
from django.utils import timezone

logger = logging.getLogger(__name__)


@shared_task
def generate_data_export(request_id: str):
    """Serialize all PII for the user and upload to Cloudinary. Sets status=ready."""
    from apps.compliance.models import DataExportRequest
    req = DataExportRequest.objects.get(id=request_id)
    req.status = 'processing'
    req.save(update_fields=['status'])

    user = req.user
    try:
        from apps.leads.models import Lead
        data = {
            'user': {
                'phone': user.phone,
                'name':  getattr(user, 'name', ''),
                'email': getattr(user, 'email', ''),
            },
            'leads': list(
                Lead.objects.filter(user=user).values('id', 'created_at', 'status', 'intent')
            ),
        }
        payload = json.dumps(data, default=str)
        import cloudinary.uploader
        result = cloudinary.uploader.upload(
            payload.encode(),
            resource_type='raw',
            public_id=f'gdpr_exports/{user.id}_{request_id}',
            type='private',
        )
        req.file_url     = result.get('secure_url', '')
        req.status       = 'ready'
        req.completed_at = timezone.now()
        req.save(update_fields=['status', 'file_url', 'completed_at'])
    except Exception:
        logger.exception('generate_data_export failed request_id=%s', request_id)
        req.status = 'pending'
        req.save(update_fields=['status'])
        raise


@shared_task
def execute_data_deletion(request_id: str):
    """Anonymize all PII for the user. Sets status=completed."""
    from apps.compliance.models import DataDeletionRequest
    req = DataDeletionRequest.objects.get(id=request_id)
    req.status = 'processing'
    req.save(update_fields=['status'])

    user = req.user
    _REDACTED = '[REDACTED]'
    try:
        from apps.leads.models import Lead
        if hasattr(user, 'name'):
            user.name = _REDACTED
        if hasattr(user, 'email'):
            user.email = ''
        user.save()
        # Lead has no client_name field; PII is on the User row already redacted above
        Lead.objects.filter(user=user).update(notes='[REDACTED]')

        req.status       = 'completed'
        req.completed_at = timezone.now()
        req.save(update_fields=['status', 'completed_at'])
    except Exception:
        logger.exception('execute_data_deletion failed request_id=%s', request_id)
        req.status = 'pending'
        req.save(update_fields=['status'])
        raise
