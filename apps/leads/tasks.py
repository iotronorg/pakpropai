import logging
from datetime import timedelta
from celery import shared_task
from django.utils import timezone

logger = logging.getLogger(__name__)

STALE_DAYS = 7   # leads with no contact for this many days are marked cold
INACTIVE_DAYS = 14  # leads with no contact for this many days get an agent reminder


@shared_task
def mark_stale_leads():
    """
    Daily task: mark leads COLD when no contact in 7+ days.
    Creates a LeadActivity record for audit trail and notifies the assigned agent.
    """
    from .models import Lead, LeadActivity

    cutoff = timezone.now() - timedelta(days=STALE_DAYS)

    stale = Lead.objects.filter(
        status__in=(Lead.Status.NEW, Lead.Status.WARM),
        last_contacted_at__lt=cutoff,
    ).select_related('assigned_agent__user')

    count = 0
    for lead in stale:
        LeadActivity.objects.create(
            lead=lead,
            actor=None,
            action=LeadActivity.ActionType.STATUS,
            notes=(
                f"Auto-marked COLD: no contact for {STALE_DAYS}+ days "
                f"(last contact: {lead.last_contacted_at.strftime('%Y-%m-%d') if lead.last_contacted_at else 'never'})."
            ),
            meta={'old_status': lead.status, 'new_status': Lead.Status.COLD},
        )
        lead.status = Lead.Status.COLD
        lead.save(update_fields=['status'])
        count += 1

    logger.info(f"mark_stale_leads: marked {count} leads COLD (cutoff: {cutoff.date()})")
    return count


@shared_task
def send_stale_lead_reminders():
    """
    Daily task: send WhatsApp reminders to agents for leads with no contact in 14+ days.
    Only fires for leads that are still assigned and haven't been followed up.
    """
    from .models import Lead

    cutoff = timezone.now() - timedelta(days=INACTIVE_DAYS)

    inactive_leads = (
        Lead.objects
        .filter(
            status__in=(Lead.Status.NEW, Lead.Status.WARM, Lead.Status.COLD),
            last_contacted_at__lt=cutoff,
            assigned_agent__isnull=False,
        )
        .select_related('assigned_agent__user')
    )

    sent = 0
    agent_lead_map: dict = {}
    for lead in inactive_leads:
        agent = lead.assigned_agent
        agent_lead_map.setdefault(agent, []).append(lead)

    for agent, leads in agent_lead_map.items():
        if not agent.user or not agent.user.phone:
            continue
        try:
            from apps.whatsapp.client import WhatsAppClient
            phone = agent.user.phone.lstrip('+')
            names = ', '.join(
                f"{l.user.phone}" for l in leads[:5]
            )
            more = f" (+{len(leads) - 5} more)" if len(leads) > 5 else ""
            msg = (
                f"⏰ *Follow-up Reminder*\n\n"
                f"You have {len(leads)} lead(s) with no contact in {INACTIVE_DAYS}+ days:\n"
                f"📱 {names}{more}\n\n"
                "Please reach out to keep your pipeline warm."
            )
            WhatsAppClient.send_text(phone, msg)
            sent += 1
        except Exception as exc:
            logger.warning(f"stale_lead_reminder failed for agent {agent.id}: {exc}")

    logger.info(f"send_stale_lead_reminders: notified {sent} agents")
    return sent
