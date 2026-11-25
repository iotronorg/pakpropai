import hashlib
import logging
import re
import uuid

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

logger = logging.getLogger(__name__)

_E164_RE = re.compile(r'^\+\d{7,15}$')
_REDACTED = '[REDACTED]'
_DELETED  = '[DELETED]'


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class RTBFOrchestrator:
    """Right-to-Be-Forgotten cascade orchestrator."""

    def initiate(self, phone_e164: str, org, requested_by_user=None):
        """Validate E.164, create DataDeletionRequest, write audit log, return request."""
        from apps.compliance.models import DataDeletionRequest, PrivacyAuditLog
        from apps.compliance.privacy_guard import JurisdictionComplianceGate
        from apps.users.models import User

        if not _E164_RE.match(phone_e164):
            raise ValueError(f"Invalid E.164 phone number: {phone_e164}")

        user = User.objects.filter(phone=phone_e164).first()
        if user is None:
            raise ValueError('No such customer.')
        # An organization may erase its own customer, not someone who also uses the platform
        # or other organizations. The answer is the same as for an unknown number.
        if getattr(requested_by_user, 'role', None) == 'developer' and not only_customer_of(user, org):
            raise ValueError('No such customer.')
        if reason := blocked_reason(user):
            raise ValueError(reason)

        regulation = JurisdictionComplianceGate().get_active_regulations(org)
        regulation_str = regulation[0] if regulation else 'LOCAL'

        req = DataDeletionRequest.objects.create(
            user=user,
            status=DataDeletionRequest.Status.PENDING,
            regulation=regulation_str,
            requested_by=requested_by_user,
            request_source=DataDeletionRequest.RequestSource.API,
        )

        PrivacyAuditLog.objects.create(
            org=org,
            action=PrivacyAuditLog.Action.ERASURE_INITIATED,
            actor=requested_by_user,
            subject_identifier=_sha256(phone_e164),
            jurisdiction=regulation_str,
            regulation=regulation_str,
            details={'request_id': str(req.id)},
            is_sensitive=True,
        )

        return req

    def execute(self, request):
        """
        Run every step, each in its own transaction. Safe to repeat. The account is blocked
        first; its identity is removed, and the request completed, only when every other
        step worked: a failed step needs the phone number to find the data again.
        Raises ErasureIncomplete otherwise (the task and the hourly job try again).
        """
        from apps.compliance.models import DataDeletionRequest, PrivacyAuditLog

        if request.status == DataDeletionRequest.Status.COMPLETED:
            return
        request.status = DataDeletionRequest.Status.PROCESSING
        request.save(update_fields=['status'])

        user = request.user
        subject, org = _sha256(user.phone), _primary_org(user)
        type(user).objects.filter(pk=user.pk).update(is_active=False)
        scope, errors = {}, {}
        for name, step in (*_STEPS, ('user_anonymized', _account)):
            if step is _account and errors:
                break
            try:
                with transaction.atomic():
                    scope[name] = step(user)
            except Exception as exc:
                logger.exception('RTBF: step %s failed user=%s', name, user.pk)
                errors[name] = type(exc).__name__       # messages can quote the data itself
                if name in _STOP_ON_FAILURE:            # later steps delete what the retry needs
                    break
        if errors:
            request.status, request.erasure_scope = DataDeletionRequest.Status.FAILED, {**scope, 'errors': errors}
            request.save(update_fields=['status', 'erasure_scope'])
            raise ErasureIncomplete(', '.join(errors))

        request.status        = DataDeletionRequest.Status.COMPLETED
        request.completed_at  = timezone.now()
        request.erasure_scope = scope
        request.save(update_fields=['status', 'completed_at', 'erasure_scope'])
        try:
            PrivacyAuditLog.objects.create(
                org=org, action=PrivacyAuditLog.Action.ERASURE_COMPLETED, actor=request.requested_by,
                subject_identifier=subject, regulation=request.regulation or 'LOCAL',
                details={'scope': scope, 'errors': {}}, is_sensitive=True)
        except Exception:
            logger.error("RTBF: PrivacyAuditLog write failed", exc_info=True)


class ErasureIncomplete(Exception):
    pass


def blocked_reason(user) -> str:
    """Why this account cannot be erased now ('' = it can)."""
    from apps.escrow.models import EscrowDeal
    from apps.organizations.models import OrganizationMembership
    if user.is_staff or user.role == 'admin':
        return 'Staff accounts are removed by an administrator.'
    if OrganizationMembership.objects.filter(user=user, role='owner', is_active=True,
                                             organization__is_active=True).exists():
        return 'Transfer the ownership of your organization first.'
    D = EscrowDeal.Status
    if EscrowDeal.objects.filter(Q(buyer=user) | Q(seller=user), status__in=[D.INITIATED, D.LOCKED]).exists():
        return 'A reservation is in progress.'
    return ''


def only_customer_of(user, org) -> bool:
    """The person is known to this organization only; the platform holds nothing else about them."""
    from apps.conversations.models import Conversation
    from apps.leads.models import Lead
    from apps.preferences.models import Favourite, PreferenceItem, SavedSearch
    from apps.properties.models import Property
    from apps.whatsapp.models import WhatsAppSession
    mine = Q(organization=org)
    if user.role != 'client' or not (Lead.objects.filter(mine, user=user).exists()
                                     or WhatsAppSession.objects.filter(mine, user=user).exists()):
        return False
    elsewhere = [m.objects.filter(user=user).exclude(mine) for m in (Lead, WhatsAppSession, Conversation)]
    elsewhere += [m.objects.filter(user=user) for m in (Favourite, SavedSearch, PreferenceItem)]
    return not any(qs.exists() for qs in (*elsewhere, Property.objects.filter(owner=user)))


# ── Steps ─────────────────────────────────────────────────────────────────────

def _cloud_files(user):
    """
    Report and audit PDFs and WhatsApp media at Cloudinary (KI-105). First, while the
    messages and the audit links still exist; a failed delete fails the step.
    """
    from apps.audit.models import PropertyAudit
    from apps.reports.models import Report
    from apps.whatsapp.models import WhatsAppMessage, WhatsAppSession

    from .cloud_files import delete_all
    reports = Report.objects.filter(user=user).exclude(file_url='')
    audits = PropertyAudit.objects.filter(Q(user=user) | Q(phone__in=_phones(user)))
    sessions = WhatsAppSession.objects.filter(Q(user=user) | Q(user__isnull=True, phone__in=_phones(user)))
    media = WhatsAppMessage.objects.filter(session__in=sessions).exclude(media_url='')
    n = delete_all([*reports.values_list('file_url', flat=True),
                    *audits.exclude(cloudinary_url='').values_list('cloudinary_url', flat=True),
                    *media.values_list('media_url', flat=True)])
    for audit in audits.exclude(pdf_file='').exclude(pdf_file__isnull=True):
        f = audit.pdf_file
        transaction.on_commit(lambda f=f: f.storage.delete(f.name), robust=True)
    audits.update(cloudinary_url='', pdf_file=None)
    reports.update(file_url='')
    media.update(media_url='')
    return n


def _leads(user):
    from apps.leads.models import Lead
    return Lead.objects.filter(user=user).update(
        notes=_REDACTED, budget_min=None, budget_max=None, score=0, intent_signals={}, score_factors={},
        city_interest='', last_known_lat=None, last_known_lon=None)


def _lead_activities(user):
    from apps.leads.models import LeadActivity
    return LeadActivity.objects.filter(lead__user=user).update(notes=_REDACTED, meta={})


def _lead_messages(user):
    """Messages of enquiry threads are handled with the enquiries."""
    from apps.leads.models import ConversationMessage
    ConversationMessage.objects.filter(sender=user, enquiry__isnull=True).update(body=_REDACTED)
    return ConversationMessage.objects.filter(lead__user=user, enquiry__isnull=True).delete()[0]


def _whatsapp(user):
    from apps.whatsapp.models import WhatsAppMessage, WhatsAppSession
    sessions = WhatsAppSession.objects.filter(Q(user=user) | Q(user__isnull=True, phone__in=_phones(user)))
    n = WhatsAppMessage.objects.filter(session__in=sessions).delete()[0]
    sessions.update(context={}, phone='', user=None)
    return n


def _documents(user):
    from apps.verification.models import DocumentScan
    return DocumentScan.objects.filter(Q(user=user) | Q(user__isnull=True, phone__in=_phones(user))).delete()[0]


def _campaigns(user):
    from apps.campaigns.models import CampaignRecipient
    from apps.leads.models import CRMContact
    CRMContact.objects.filter(phone__in=_phones(user)).delete()
    return CampaignRecipient.objects.filter(Q(lead__user=user) | Q(phone__in=_phones(user))).delete()[0]


def _voice(user):
    from apps.voice.models import VoiceCallSession
    calls = VoiceCallSession.objects.filter(
        Q(lead__user=user) | Q(from_phone__in=_phones(user)) | Q(to_phone__in=_phones(user)))
    calls.filter(from_phone__in=_phones(user)).update(from_phone='')
    return VoiceCallSession.objects.filter(pk__in=list(calls.values_list('pk', flat=True))).update(
        transcript='', ai_context_snapshot={}, recording_url='', to_phone='', from_phone='')


def _ai_records(user):
    from apps.ai.models import AITrace, PendingAction
    AITrace.objects.filter(user=user).update(user=None, conversation_ref='')
    return PendingAction.objects.filter(user=user).delete()[0]


def _ai_conversations(user):
    from apps.ai.models import AIInteraction
    from apps.conversations.store import erase_user
    AIInteraction.objects.filter(user=user).update(input_data={}, output_data={}, user=None)
    return erase_user(user)


def _security_logs(user):
    from apps.audit.models import ComplianceViolation, PropertyAudit, SecurityTrace
    phones = _phones(user)
    PropertyAudit.objects.filter(Q(user=user) | Q(phone__in=phones)).update(phone='', user=None)
    ComplianceViolation.objects.filter(phone__in=phones).update(phone='', details='')
    return SecurityTrace.objects.filter(phone__in=phones).update(phone='', message_excerpt='')


def _viewings(user):
    from apps.viewings.services import erase_user
    return erase_user(user)


def _enquiries(user):
    from apps.enquiries.services import erase_user
    return erase_user(user)


def _preferences(user):
    from apps.preferences.services import erase_user
    return erase_user(user)


def _moderation(user):
    from apps.moderation.services import erase_user
    return erase_user(user)


def _reports(user):
    from apps.reports.models import Report
    return Report.objects.filter(user=user).delete()[0]


def _export_files(user):
    from apps.compliance.models import DataExportFile
    return DataExportFile.objects.filter(request__user=user).delete()[0]


def _private_listings(user):
    """A private person's own listings are taken down and emptied (an organization keeps its listings)."""
    from apps.properties.models import Property, PropertyImage
    mine = Property.objects.filter(owner=user, organization__isnull=True)
    for image in PropertyImage.objects.filter(property__in=mine):
        for f in (image.image, image.thumbnail):
            if f:
                transaction.on_commit(lambda f=f: f.storage.delete(f.name), robust=True)
        image.delete()
    return mine.update(status=Property.Status.ARCHIVED, is_active=False, title=_DELETED, description='',
                       location='', raw_docs={}, ai_analysis={}, latitude=None, longitude=None, geo_lat=None,
                       geo_lng=None)


def _notifications(user):
    from apps.notifications.models import Notification
    return Notification.objects.filter(user=user).delete()[0]


def _agent_profile(user):
    from apps.agents.models import Agent
    n = 0
    for agent in Agent.objects.filter(user=user):
        if agent.profile_photo:
            f = agent.profile_photo
            transaction.on_commit(lambda f=f: f.storage.delete(f.name), robust=True)
        n += Agent.objects.filter(pk=agent.pk).update(
            name=_DELETED, phone=_placeholder(), whatsapp_number='', email='', national_id_number='', cnic_number='',
            tax_id_number='', ntn_number='', license_number='', registration_number='', bio='', website='',
            office_address='', profile_photo='', is_active=False)
    return n


def _account(user):
    from django.contrib.admin.models import LogEntry
    from rest_framework_simplejwt.token_blacklist.models import OutstandingToken

    from apps.organizations.models import OrganizationMembership
    from apps.users.models import MFADevice, MFARecoveryCode, OTPCode
    OTPCode.objects.filter(phone__in=_phones(user)).delete()
    for model in (MFADevice, MFARecoveryCode, OrganizationMembership, LogEntry):
        model.objects.filter(user=user).delete()
    OutstandingToken.objects.filter(user=user).update(user=None)
    user.is_active, user.mfa_enabled, user.name, user.email = False, False, _DELETED, \
        f'deleted_{uuid.uuid4().hex[:8]}@deleted.invalid'
    user.phone, user.cnic, user.ntn = _placeholder(), None, None
    user.set_unusable_password()
    user.save()
    return True


def _placeholder() -> str:
    """Unique, and not a phone number: the person can register again with theirs."""
    return f'deleted-{uuid.uuid4().hex[:12]}'


def _phones(user) -> list[str]:
    """WhatsApp stores numbers without the plus sign."""
    return [user.phone, user.phone.lstrip('+')]


# External files go first and stop the run when they fail: the rows that hold their URLs
# (reports, WhatsApp messages, audit links) are removed by the later steps.
_STOP_ON_FAILURE = frozenset({'cloud_files_deleted'})

# Viewings before enquiries, so the agent is told about cancelled visits.
_STEPS = (
    ('cloud_files_deleted', _cloud_files),
    ('leads_anonymized', _leads), ('lead_activities_redacted', _lead_activities),
    ('lead_messages_deleted', _lead_messages), ('wa_messages_deleted', _whatsapp),
    ('document_scans_deleted', _documents), ('campaign_recipients_deleted', _campaigns), ('voice_calls_emptied', _voice),
    ('ai_pending_actions_deleted', _ai_records), ('ai_conversations_deleted', _ai_conversations),
    ('security_logs_redacted', _security_logs), ('viewings_redacted', _viewings), ('enquiries_deleted', _enquiries),
    ('preferences_deleted', _preferences), ('reports_redacted', _moderation), ('generated_reports_deleted', _reports),
    ('export_files_deleted', _export_files), ('private_listings_removed', _private_listings),
    ('notifications_deleted', _notifications), ('agent_profiles_redacted', _agent_profile),
)


def _primary_org(user):
    from apps.core.permissions import primary_org
    return primary_org(user)
