import logging
from datetime import timedelta
from celery import shared_task
from django.db import models
from django.utils import timezone
from apps.whatsapp.client import get_wa_client

REMINDER_WINDOW_MINUTES  = 60
AUTO_ASSIGN_DELAY_MINUTES = 30  # min age before a lead qualifies for auto-assign

logger = logging.getLogger(__name__)

STALE_DAYS    = 7
INACTIVE_DAYS = 14
FOLLOWUP_RESEND_DAYS = 3  # don't re-remind an agent within this window


@shared_task
def auto_assign_unassigned_leads():
    """
    Every 15 min: assign unassigned leads (age >= 30 min) to the best available agent.
    Only runs for orgs that have feature_auto_assign enabled.
    """
    from .models import Lead
    from .services import suggest_agents_for_lead, assign_agent_to_lead
    from apps.organizations.models import OrganizationConfig
    from apps.config.services import SystemConfigService

    now = timezone.now()
    cutoff = now - timedelta(minutes=AUTO_ASSIGN_DELAY_MINUTES)

    # Collect org IDs with the feature on.
    # Org-level overrides take precedence; if the platform default is 'true', all orgs qualify.
    platform_on = SystemConfigService.get('feature_auto_assign', 'false') == 'true'
    if platform_on:
        # All orgs qualify unless explicitly opted out (no opt-out mechanism yet → all orgs)
        unassigned = Lead.objects.filter(
            assigned_agent__isnull=True,
            organization__isnull=False,
            created_at__lt=cutoff,
            status__in=(Lead.Status.NEW, Lead.Status.WARM),
        ).select_related('organization', 'user')
    else:
        # Only orgs that have explicitly set feature_auto_assign=true
        opted_in_org_ids = OrganizationConfig.objects.filter(
            key='feature_auto_assign', value='true'
        ).values_list('organization_id', flat=True)
        unassigned = Lead.objects.filter(
            assigned_agent__isnull=True,
            organization_id__in=opted_in_org_ids,
            created_at__lt=cutoff,
            status__in=(Lead.Status.NEW, Lead.Status.WARM),
        ).select_related('organization', 'user')

    assigned_count = 0
    for lead in unassigned:
        candidates = suggest_agents_for_lead(lead, limit=1)
        if not candidates:
            continue
        try:
            assign_agent_to_lead(lead, candidates[0], actor=None, auto=True)
            assigned_count += 1
        except Exception as exc:
            logger.warning(f"auto_assign failed for lead {lead.id}: {exc}")

    logger.info(f"auto_assign_unassigned_leads: assigned {assigned_count} leads")
    return assigned_count


@shared_task
def mark_stale_leads():
    """Daily: mark leads COLD when no contact in 7+ days, then re-engage client via WA."""
    from .models import Lead, LeadActivity

    cutoff = timezone.now() - timedelta(days=STALE_DAYS)
    stale = Lead.objects.filter(
        status__in=(Lead.Status.NEW, Lead.Status.WARM),
        last_contacted_at__lt=cutoff,
    ).select_related('organization', 'assigned_agent__user')

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
        _send_client_reengagement(lead)

    logger.info(f"mark_stale_leads: marked {count} leads COLD (cutoff: {cutoff.date()})")
    return count


def _send_client_reengagement(lead) -> None:
    """Send a re-engagement WhatsApp nudge to a client whose lead just went cold."""
    from apps.config.services import SystemConfigService
    if SystemConfigService.get('feature_follow_up_automation', 'false') != 'true':
        return

    try:
        user = lead.user
        if not user or not user.phone:
            return

        city   = lead.city_interest or 'your area of interest'
        intent = lead.get_intent_display() if lead.intent else 'a property'
        msg = (
            f"👋 *We miss you!*\n\n"
            f"We noticed you were exploring *{intent}* options in *{city}*.\n\n"
            f"New properties matching your criteria have been listed. "
            f"Just reply here and our AI assistant will pick up right where you left off! 🏠"
        )
        get_wa_client(org=lead.organization).send_text(user.phone, msg)
        lead.follow_up_sent_at = timezone.now()
        lead.save(update_fields=['follow_up_sent_at'])
    except Exception as exc:
        logger.warning(f"Client re-engagement WA failed for lead {lead.id}: {exc}")


@shared_task
def send_stale_lead_reminders():
    """
    Daily: remind agents about leads with no contact in 14+ days.
    Skips leads that already received a reminder within FOLLOWUP_RESEND_DAYS.
    """
    from .models import Lead

    now    = timezone.now()
    cutoff = now - timedelta(days=INACTIVE_DAYS)
    resend_cutoff = now - timedelta(days=FOLLOWUP_RESEND_DAYS)

    inactive_leads = (
        Lead.objects
        .filter(
            status__in=(Lead.Status.NEW, Lead.Status.WARM, Lead.Status.COLD),
            last_contacted_at__lt=cutoff,
            assigned_agent__isnull=False,
        )
        .filter(
            models.Q(follow_up_sent_at__isnull=True) |
            models.Q(follow_up_sent_at__lt=resend_cutoff)
        )
        .select_related('organization', 'assigned_agent__user', 'user')
    )

    sent = 0
    agent_lead_map: dict = {}
    for lead in inactive_leads:
        agent_lead_map.setdefault(lead.assigned_agent, []).append(lead)

    for agent, leads in agent_lead_map.items():
        if not agent.user or not agent.user.phone:
            continue
        try:
            org = agent.organization or leads[0].organization
            names = ', '.join(l.user.phone for l in leads[:5])
            more  = f" (+{len(leads) - 5} more)" if len(leads) > 5 else ""
            msg = (
                f"⏰ *Follow-up Reminder*\n\n"
                f"You have {len(leads)} lead(s) with no contact in {INACTIVE_DAYS}+ days:\n"
                f"📱 {names}{more}\n\n"
                "Please reach out to keep your pipeline warm."
            )
            get_wa_client(org=org).send_text(agent.user.phone, msg)

            Lead.objects.filter(
                id__in=[l.id for l in leads]
            ).update(follow_up_sent_at=now)
            sent += 1
        except Exception as exc:
            logger.warning(f"stale_lead_reminder failed for agent {agent.id}: {exc}")

    logger.info(f"send_stale_lead_reminders: notified {sent} agents")
    return sent


@shared_task
def send_appointment_reminders():
    """
    Every 15 min: send WA reminder to clients with an appointment starting within 1 hour.
    """
    from .models import Appointment

    now        = timezone.now()
    window_end = now + timedelta(minutes=REMINDER_WINDOW_MINUTES)

    due = Appointment.objects.filter(
        scheduled_at__gt=now,
        scheduled_at__lte=window_end,
        reminder_sent_at__isnull=True,
        status__in=(Appointment.Status.SCHEDULED, Appointment.Status.CONFIRMED),
    ).select_related('lead__user', 'lead__organization', 'property', 'agent__user')

    sent = 0
    for appt in due:
        try:
            client = appt.lead.user
            if not client or not client.phone:
                continue

            time_str   = appt.scheduled_at.strftime('%I:%M %p')
            date_str   = appt.scheduled_at.strftime('%A, %d %B %Y')
            prop_title = appt.property.title if appt.property else 'the property'
            agent_name = appt.agent.display_name if appt.agent else 'your agent'

            msg = (
                f"🏠 *Visit Reminder*\n\n"
                f"You have a property visit scheduled:\n\n"
                f"📍 *{prop_title}*\n"
                f"📅 {date_str}\n"
                f"🕐 {time_str}\n"
                f"👤 Agent: {agent_name}\n\n"
                "Reply *CONFIRM* to confirm or *CANCEL* to cancel your visit."
            )

            get_wa_client(org=appt.lead.organization).send_text(client.phone, msg)
            appt.reminder_sent_at = now
            appt.save(update_fields=['reminder_sent_at'])
            sent += 1
        except Exception as exc:
            logger.warning(f"Appointment reminder failed for appt {appt.pk}: {exc}")

    logger.info(f"send_appointment_reminders: sent {sent} reminder(s)")
    return sent
