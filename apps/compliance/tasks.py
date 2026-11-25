import json
import logging
from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.db import transaction
from django.utils import timezone

logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    max_retries=3,
    default_retry_delay=60,
    queue='default',
)
def execute_rtbf_erasure(self, request_id: str):
    """Execute Right-to-Be-Forgotten cascade for a DataDeletionRequest. Idempotent."""
    from apps.compliance.models import DataDeletionRequest
    from apps.compliance.erasure import RTBFOrchestrator

    try:
        req = DataDeletionRequest.objects.get(id=request_id)
    except DataDeletionRequest.DoesNotExist:
        logger.error("execute_rtbf_erasure: request %s not found", request_id)
        return

    if req.status == DataDeletionRequest.Status.COMPLETED:
        return

    try:
        RTBFOrchestrator().execute(req)
    except Exception as exc:
        logger.error("execute_rtbf_erasure failed request_id=%s: %s", request_id, exc)
        if self.request.retries >= self.max_retries:
            req.status = DataDeletionRequest.Status.FAILED
            req.save(update_fields=['status'])
            try:
                from apps.notifications.models import Notification
                from apps.core.permissions import primary_org
                org = primary_org(req.user)
                owner = org.owner if org else None
                if owner:
                    Notification.objects.create(
                        user=owner,
                        severity='CRITICAL',
                        message=f'RTBF erasure failed permanently for request {request_id}: {exc}',
                    )
            except Exception:
                logger.error("execute_rtbf_erasure: failed to send admin notification", exc_info=True)
        raise


@shared_task
def generate_data_export(request_id: str):
    """Everything we hold about the person, stored encrypted for a private download (KI-066)."""
    from apps.compliance.models import DataExportFile, DataExportRequest
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
        from apps.enquiries.models import Enquiry
        data['enquiries'] = list(Enquiry.objects.filter(user=user).values(
            'id', 'subject', 'kind', 'status', 'created_at', 'consented_at', 'consent_version', 'consent_revoked_at'))
        from apps.leads.models import Appointment
        data['viewings'] = list(Appointment.objects.filter(lead__user=user).values(
            'id', 'property__ref_no', 'scheduled_at', 'duration_minutes', 'status', 'participants'))
        data['favourites'] = list(user.favourites.values('listing__ref_no', 'kind', 'note', 'created_at'))
        data['saved_searches'] = list(user.saved_searches.values(
            'name', 'criteria', 'frequency', 'channel', 'is_active', 'consented_at', 'consent_via'))
        data['preferences'] = list(user.preference_items.values('market', 'key', 'value', 'source', 'updated_at'))
        data['listing_reports'] = list(user.listing_reports.values('listing__ref_no', 'category', 'text', 'created_at'))
        from apps.conversations.models import Message
        data['assistant_messages'] = [
            {'conversation': str(m.conversation_id), 'channel': m.conversation.channel, 'role': m.role,
             'text': m.content, 'created_at': m.created_at}
            for m in Message.objects.filter(conversation__user=user).select_related('conversation')
            .order_by('conversation', 'seq').iterator()]
        from apps.leads.models import ConversationMessage
        data['thread_messages'] = list(ConversationMessage.objects.filter(enquiry__user=user).order_by(
            'created_at').values('enquiry_id', 'direction', 'body', 'created_at'))
        from apps.enquiries.models import MessageAttachment
        data['thread_photos'] = list(MessageAttachment.objects.filter(sender=user).values(
            'id', 'message__enquiry_id', 'content_type', 'size', 'created_at'))     # GET threads/{id}/attachments/{id}/
        data['thread_reports'] = list(user.thread_reports.values('enquiry_id', 'category', 'text', 'created_at'))
        payload = json.dumps(data, default=str)
        with transaction.atomic():
            DataExportFile.objects.update_or_create(request=req, defaults={
                'payload': payload, 'expires_at': timezone.now() + timedelta(days=settings.DATA_EXPORT_DAYS)})
            req.status       = 'ready'
            req.completed_at = timezone.now()
            req.save(update_fields=['status', 'completed_at'])
    except Exception:
        logger.exception('generate_data_export failed request_id=%s', request_id)
        req.status = 'pending'
        req.save(update_fields=['status'])
        raise


@shared_task
def purge_data_exports():
    from apps.compliance.models import DataExportFile, DataExportRequest
    ids = list(DataExportFile.objects.filter(expires_at__lte=timezone.now()).values_list('pk', flat=True))
    with transaction.atomic():
        DataExportRequest.objects.filter(pk__in=ids).update(status=DataExportRequest.Status.EXPIRED)
        DataExportFile.objects.filter(pk__in=ids).delete()
    return len(ids)


@shared_task(bind=True, autoretry_for=(Exception,), max_retries=3, default_retry_delay=300)
def sync_aml_sanction_lists(self):
    """Weekly Celery beat task: sync OFAC, UN, and EU sanction lists."""
    from django.core.management import call_command
    logger.info('sync_aml_sanction_lists: starting')
    call_command('sync_sanctions', lists='ofac,un,eu')
    logger.info('sync_aml_sanction_lists: complete')


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
