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


def _more(user) -> dict:
    """Records outside the discovery journey."""
    from django.db.models import Q

    from apps.agents.models import Agent
    from apps.ai.models import PendingAction
    from apps.compliance.models import ConsentRecord
    from apps.escrow.models import EscrowDeal
    from apps.leads.models import ConversationMessage, CRMContact, LeadActivity
    from apps.notifications.models import Notification
    from apps.organizations.models import OrganizationMembership
    from apps.payments.models import Payment
    from apps.properties.models import Property
    from apps.reports.models import Report
    from apps.verification.models import DocumentScan
    from apps.viewings.models import ViewingFeedback
    from apps.voice.models import VoiceCallSession
    from apps.whatsapp.models import WhatsAppMessage

    def rows(qs, *fields):
        return list(qs.values(*fields))
    phones = [user.phone, user.phone.lstrip('+')]
    return {
        'whatsapp_messages': rows(WhatsAppMessage.objects.filter(
            Q(session__user=user) | Q(session__user__isnull=True, session__phone__in=phones)),
            'direction', 'msg_type', 'body', 'created_at'),
        'agency_messages': rows(ConversationMessage.objects.filter(lead__user=user, enquiry__isnull=True).exclude(
            channel=ConversationMessage.Channel.DASHBOARD, sender__isnull=False),
            'direction', 'channel', 'body', 'created_at'),
        'lead_history': rows(LeadActivity.objects.filter(lead__user=user), 'action', 'notes', 'created_at'),
        'agency_contact_notes': rows(CRMContact.objects.filter(user=user), 'organization__name', 'name', 'tags'),  # T-3100
        'voice_calls': rows(VoiceCallSession.objects.filter(lead__user=user), 'direction', 'started_at',
                            'duration_seconds', 'transcript'),
        'confirmations': rows(PendingAction.objects.filter(user=user), 'tool', 'prompt', 'status', 'created_at'),
        'viewing_feedback': rows(ViewingFeedback.objects.filter(author=user), 'appointment_id', 'notes'),
        'notifications': rows(Notification.objects.filter(user=user), 'title', 'message', 'created_at'),
        'consents': rows(ConsentRecord.objects.filter(user=user), 'purpose', 'is_active', 'revoked_at'),
        'documents': rows(DocumentScan.objects.filter(user=user), 'document_type', 'owner_name', 'cnic_number',
                          'property_address', 'extracted_fields', 'status', 'created_at'),
        'listings': rows(Property.objects.filter(owner=user), 'ref_no', 'title', 'description', 'city', 'location',
                         'price', 'currency', 'status', 'created_at'),
        'payments': rows(Payment.objects.filter(user=user), 'amount', 'currency', 'purpose', 'status', 'created_at'),
        'reservations': rows(EscrowDeal.objects.filter(Q(buyer=user) | Q(seller=user)), 'id', 'status', 'token_amount',
                             'currency', 'created_at'),
        'reports': rows(Report.objects.filter(user=user), 'report_type', 'content', 'created_at'),
        'memberships': rows(OrganizationMembership.objects.filter(user=user), 'organization__name', 'role'),
        'agent_profile': rows(Agent.objects.filter(user=user), 'name', 'phone', 'email', 'national_id_number',
                              'cnic_number', 'tax_id_number', 'ntn_number', 'license_number', 'bio', 'office_address'),
    }


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
                'cnic': user.cnic, 'ntn': user.ntn, 'role': user.role,
                'created_at': user.created_at, 'last_active': user.last_active,
            },
            'leads': list(Lead.objects.filter(user=user).values(
                'id', 'created_at', 'status', 'intent', 'organization__name', 'notes', 'city_interest', 'budget_min',
                'budget_max', 'budget_currency', 'last_known_lat', 'last_known_lon')),
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
        data |= _more(user)
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
    """The person's own request: the same erasure as any other (KI-103)."""
    from apps.compliance.erasure import ErasureIncomplete, RTBFOrchestrator
    from apps.compliance.models import DataDeletionRequest
    try:
        RTBFOrchestrator().execute(DataDeletionRequest.objects.get(id=request_id))
    except ErasureIncomplete:
        logger.error('execute_data_deletion incomplete request_id=%s', request_id)     # process_data_requests repeats it


@shared_task
def process_data_requests():
    """Hourly: run again what did not finish, and report what is close to its deadline (MVP acceptance 11)."""
    from apps.compliance.models import DataDeletionRequest, DataExportRequest
    now = timezone.now()
    late = now - timedelta(days=settings.DATA_REQUEST_SLA_DAYS) * 0.8
    again = 0
    for model, task, statuses in ((DataDeletionRequest, execute_data_deletion, ['pending', 'processing', 'failed']),
                                  (DataExportRequest, generate_data_export, ['pending', 'processing'])):
        for pk, asked in model.objects.filter(status__in=statuses, requested_at__lt=now - timedelta(hours=1)
                                              ).values_list('pk', 'requested_at'):
            if asked < late:
                logger.error('data_request.overdue kind=%s request=%s requested_at=%s', model.__name__, pk, asked)
            task.delay(str(pk))
            again += 1
    return again
