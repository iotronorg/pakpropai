"""
AI re-engagement worker for cold QUALIFIED leads.
ReEngagementScanner      — finds leads matching re-engagement criteria
LeadContextBuilder       — builds WA transcript + CRM timeline
ReEngagementPromptGenerator — generates personalised LLM message
run_reengagement_pass    — entry point called by Celery task
"""
import logging
import re as _re
from dataclasses import dataclass
from datetime import timedelta

from django.db import models
from django.utils import timezone

logger = logging.getLogger(__name__)

_REENGAGEMENT_WINDOW_HOURS = 72
_FOLLOWUP_RESEND_DAYS      = 3
_MAX_WA_MESSAGES           = 20
_MAX_ACTIVITIES            = 15
_MAX_MSG_LENGTH            = 300

_CANNED_MESSAGE = (
    "Hi! We noticed you were exploring property options with us. "
    "New listings matching your interests are available — just reply and our AI will assist you!"
)

_PREAMBLE_RE = _re.compile(
    r"^(sure[!,.]?|here[''']?s [a-z ]+:|here is [a-z ]+:|of course[!,.]?)\s*",
    _re.IGNORECASE,
)


@dataclass
class LeadContext:
    transcript:   str
    crm_timeline: str
    lead_summary: dict


class ReEngagementScanner:

    def find_cold_leads(self, org=None):
        from apps.config.services import SystemConfigService
        if SystemConfigService.get('feature_follow_up_automation', 'false') != 'true':
            from apps.leads.models import Lead
            return Lead.objects.none()

        from apps.leads.models import Lead
        cutoff        = timezone.now() - timedelta(hours=_REENGAGEMENT_WINDOW_HOURS)
        resend_cutoff = timezone.now() - timedelta(days=_FOLLOWUP_RESEND_DAYS)

        qs = Lead.objects.filter(
            status=Lead.Status.QUALIFIED,
            last_contacted_at__lt=cutoff,
        ).filter(
            models.Q(follow_up_sent_at__isnull=True) |
            models.Q(follow_up_sent_at__lt=resend_cutoff)
        ).select_related('organization', 'user')

        if org is not None:
            qs = qs.filter(organization=org)

        return qs


class LeadContextBuilder:

    def build_context(self, lead) -> LeadContext:
        return LeadContext(
            transcript=self._wa_transcript(lead),
            crm_timeline=self._crm_timeline(lead),
            lead_summary=self._lead_summary(lead),
        )

    def _wa_transcript(self, lead) -> str:
        if not lead.user or not lead.user.phone:
            return ''
        try:
            from apps.whatsapp.models import WhatsAppMessage
            phone = lead.user.phone.lstrip('+')
            msgs  = (
                WhatsAppMessage.objects
                .filter(session__phone=phone)
                .order_by('-created_at')[:_MAX_WA_MESSAGES]
            )
            if not msgs:
                return ''
            lines = [
                f"[{m.direction}] {m.body or '(media)'}"
                for m in reversed(list(msgs))
            ]
            return '\n'.join(lines)
        except Exception as exc:
            logger.debug("LeadContextBuilder._wa_transcript error: %s", exc)
            return ''

    def _crm_timeline(self, lead) -> str:
        try:
            from apps.leads.models import LeadActivity
            acts = (
                LeadActivity.objects
                .filter(lead=lead)
                .order_by('-created_at')[:_MAX_ACTIVITIES]
            )
            if not acts:
                return ''
            lines = [
                f"[{a.action}] {a.notes or ''} ({a.created_at.strftime('%Y-%m-%d')})"
                for a in reversed(list(acts))
            ]
            return '\n'.join(lines)
        except Exception as exc:
            logger.debug("LeadContextBuilder._crm_timeline error: %s", exc)
            return ''

    def _lead_summary(self, lead) -> dict:
        return {
            'intent':     getattr(lead, 'intent', None),
            'budget_min': getattr(lead, 'budget_min', None),
            'budget_max': getattr(lead, 'budget_max', None),
            'city':       getattr(lead, 'city_interest', None),
            'score':      getattr(lead, 'score', None),
        }


class ReEngagementPromptGenerator:

    def generate(self, lead, context: LeadContext, org) -> str:
        try:
            message = self._call_llm(lead, context, org)
            if not message:
                return _CANNED_MESSAGE
            message = _PREAMBLE_RE.sub('', message).strip()
            return message[:_MAX_MSG_LENGTH] or _CANNED_MESSAGE
        except Exception as exc:
            logger.warning("ReEngagementPromptGenerator LLM error for lead %s: %s", lead.id, exc)
            return _CANNED_MESSAGE

    def _call_llm(self, lead, context: LeadContext, org) -> str:
        from apps.ai.backends import get_backend
        backend = get_backend()

        lang = getattr(org, 'language', 'en') or 'en'
        summary = context.lead_summary

        system_prompt = (
            f"You are a real estate sales assistant for {org.name}. "
            f"Generate a single WhatsApp re-engagement message (max {_MAX_MSG_LENGTH} characters) "
            f"for a lead who went quiet 72+ hours ago. "
            f"Tone: warm, helpful, not pushy. Language: {lang}. "
            f"Do NOT use more than one emoji. Never mention internal CRM terms."
        )

        budget_str = ''
        if summary.get('budget_min') and summary.get('budget_max'):
            budget_str = f"Budget: {summary['budget_min']:,}–{summary['budget_max']:,}"

        user_prompt = (
            f"Lead profile:\n"
            f"- Intent: {summary.get('intent', 'unknown')}\n"
            f"- City: {summary.get('city', 'unknown')}\n"
            f"- {budget_str}\n\n"
            f"Recent conversation:\n{context.transcript or '(no messages)'}\n\n"
            f"CRM activity:\n{context.crm_timeline or '(no activity)'}\n\n"
            f"Write one short WhatsApp message (max {_MAX_MSG_LENGTH} chars) that picks up "
            f"naturally from this conversation."
        )

        return backend.chat(user_prompt, [], [], system_prompt)


def run_reengagement_pass(org_id: str = None) -> int:
    """
    Scan for cold leads and send AI-personalised re-engagement messages.
    Returns count of leads processed.
    """
    from apps.whatsapp.client import get_wa_client
    from apps.leads.models import LeadActivity
    from apps.campaigns.campaign_manager import MetaTierRateLimiter, RateLimitExceeded

    scanner   = ReEngagementScanner()
    builder   = LeadContextBuilder()
    generator = ReEngagementPromptGenerator()
    limiter   = MetaTierRateLimiter()

    org = None
    if org_id:
        from apps.organizations.models import Organization
        try:
            org = Organization.objects.get(id=org_id)
        except Organization.DoesNotExist:
            logger.error("run_reengagement_pass: org %s not found", org_id)
            return 0

    leads     = scanner.find_cold_leads(org=org)
    processed = 0

    for lead in leads:
        try:
            if not lead.user or not lead.user.phone:
                continue

            context = builder.build_context(lead)
            message = generator.generate(lead, context, lead.organization)

            if not limiter.acquire(str(lead.organization_id), tier=1):
                raise RateLimitExceeded("bucket empty")

            wa_client = get_wa_client(org=lead.organization)
            wa_client.send_text(lead.user.phone, message)

            lead.follow_up_sent_at = timezone.now()
            lead.save(update_fields=['follow_up_sent_at'])

            LeadActivity.objects.create(
                lead=lead,
                actor=None,
                action=LeadActivity.ActionType.NOTE,
                notes=f"AI re-engagement sent: {message[:80]}{'...' if len(message) > 80 else ''}",
            )
            processed += 1

        except RateLimitExceeded:
            logger.info("run_reengagement_pass: rate limit hit, stopping pass early")
            break
        except Exception as exc:
            logger.warning("run_reengagement_pass: failed for lead %s: %s", lead.id, exc)

    logger.info("run_reengagement_pass: processed %d leads (org=%s)", processed, org_id)
    return processed
