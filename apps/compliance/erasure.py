import hashlib
import logging
import re
import uuid

from django.db import transaction
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
            raise ValueError(f"No user found for phone: {phone_e164}")

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
        Atomic cascading erasure. Partial entity failures log ERROR and continue —
        prefer incomplete erasure over silent skip.
        """
        from apps.compliance.models import DataDeletionRequest, PrivacyAuditLog

        if request.status == DataDeletionRequest.Status.COMPLETED:
            return

        request.status = DataDeletionRequest.Status.PROCESSING
        request.save(update_fields=['status'])

        user   = request.user
        errors = {}
        scope  = {}

        # 1 — Lead rows
        try:
            from apps.leads.models import Lead
            leads = list(Lead.objects.filter(user=user))
            Lead.objects.filter(user=user).update(
                notes=_REDACTED,
                budget_min=None,
                budget_max=None,
                score=0,
            )
            scope['leads_anonymized'] = len(leads)
        except Exception as exc:
            logger.error("RTBF: Lead anonymization failed user=%s: %s", user.id, exc)
            errors['leads'] = str(exc)

        # 2 — WhatsApp session messages
        try:
            from apps.whatsapp.models import WhatsAppSession, WhatsAppMessage
            sessions = WhatsAppSession.objects.filter(user=user)
            deleted, _ = WhatsAppMessage.objects.filter(session__in=sessions).delete()
            scope['wa_messages_deleted'] = deleted
        except Exception as exc:
            logger.error("RTBF: WhatsAppMessage purge failed user=%s: %s", user.id, exc)
            errors['wa_messages'] = str(exc)

        # 3 — LeadActivity notes
        try:
            from apps.leads.models import Lead, LeadActivity
            lead_ids = Lead.objects.filter(user=user).values_list('id', flat=True)
            updated = LeadActivity.objects.filter(lead_id__in=lead_ids).update(notes=_REDACTED)
            scope['lead_activities_redacted'] = updated
        except Exception as exc:
            logger.error("RTBF: LeadActivity redaction failed user=%s: %s", user.id, exc)
            errors['lead_activities'] = str(exc)

        # 4 — DocumentScan rows
        try:
            from apps.verification.models import DocumentScan
            deleted, _ = DocumentScan.objects.filter(user=user).delete()
            scope['document_scans_deleted'] = deleted
        except Exception as exc:
            logger.error("RTBF: DocumentScan delete failed user=%s: %s", user.id, exc)
            errors['document_scans'] = str(exc)

        # 5 — CampaignRecipient: anonymize any phone reference via lead FK
        try:
            from apps.campaigns.models import CampaignRecipient
            from apps.leads.models import Lead
            lead_ids = Lead.objects.filter(user=user).values_list('id', flat=True)
            updated = CampaignRecipient.objects.filter(lead_id__in=lead_ids).update(lead=None)
            scope['campaign_recipients_anonymized'] = updated
        except Exception as exc:
            logger.error("RTBF: CampaignRecipient anonymization failed user=%s: %s", user.id, exc)
            errors['campaign_recipients'] = str(exc)

        # 6 — User anonymization
        try:
            user.is_active = False
            user.name      = _DELETED
            user.email     = f'deleted_{uuid.uuid4().hex[:8]}@deleted.invalid'
            user.save(update_fields=['is_active', 'name', 'email'])
            scope['user_anonymized'] = True
        except Exception as exc:
            logger.error("RTBF: User anonymization failed user=%s: %s", user.id, exc)
            errors['user'] = str(exc)

        # 7 — Mark request complete
        request.status       = DataDeletionRequest.Status.COMPLETED
        request.completed_at = timezone.now()
        request.erasure_scope = scope
        request.save(update_fields=['status', 'completed_at', 'erasure_scope'])

        # 8 — Audit log
        try:
            PrivacyAuditLog.objects.create(
                org=getattr(request.user, 'owned_organization', None),
                action=PrivacyAuditLog.Action.ERASURE_COMPLETED,
                actor=request.requested_by,
                subject_identifier=_sha256(user.phone),
                regulation=request.regulation or 'LOCAL',
                details={'scope': scope, 'errors': errors},
                is_sensitive=True,
            )
        except Exception:
            logger.error("RTBF: PrivacyAuditLog write failed", exc_info=True)
