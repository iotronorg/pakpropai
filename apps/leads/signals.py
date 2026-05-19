import logging
from django.db.models.signals import pre_save
from django.dispatch import receiver

logger = logging.getLogger(__name__)

HOT_THRESHOLD = 70  # score at which a lead is considered "hot"


@receiver(pre_save, sender='leads.Lead')
def record_score_change(sender, instance, **kwargs):
    """
    Before saving a Lead, compare the new score against the DB value.
    If it changed, create a LeadScoreHistory entry after the save completes.
    Uses pre_save to capture old value, then defers the write via post_save.
    """
    if not instance.pk:
        return  # new record — no history yet

    try:
        old = sender.objects.values_list('score', flat=True).get(pk=instance.pk)
    except sender.DoesNotExist:
        return

    if old != instance.score:
        # Stash on the instance so post_save can read it without a second query
        instance._score_changed_from = old


@receiver(pre_save, sender='leads.Lead')
def _noop_post_save_hook(sender, instance, **kwargs):
    pass


from django.db.models.signals import post_save

@receiver(post_save, sender='leads.Lead')
def write_score_history(sender, instance, created, **kwargs):
    if created:
        return
    old = getattr(instance, '_score_changed_from', None)
    if old is None:
        return
    from .models import LeadScoreHistory
    LeadScoreHistory.objects.create(
        lead=instance,
        old_score=old,
        new_score=instance.score,
        reason='AI rescored' if not getattr(instance, '_score_actor', None) else 'manual override',
        changed_by=getattr(instance, '_score_actor', None),
    )
    instance._score_changed_from = None

    # Hot lead: score just crossed HOT_THRESHOLD — alert assigned agent immediately
    if old < HOT_THRESHOLD <= instance.score and instance.assigned_agent_id:
        _fire_hot_lead_alert(instance)


def _fire_hot_lead_alert(lead) -> None:
    """Send an instant WhatsApp alert to the assigned agent when a lead goes hot."""
    try:
        agent = lead.assigned_agent
        if not agent or not agent.user or not agent.user.phone:
            return
        from apps.whatsapp.client import WhatsAppClient
        city   = lead.city_interest or 'Not specified'
        intent = lead.get_intent_display() if lead.intent else 'Not specified'
        msg = (
            f"🔥 *Hot Lead Alert!*\n\n"
            f"Lead *{lead.user.phone}* just scored *{lead.score}/100* — they're ready to convert.\n\n"
            f"📍 City: {city}\n"
            f"🎯 Intent: {intent}\n\n"
            "Follow up NOW before the moment passes! ⚡"
        )
        WhatsAppClient.send_text(agent.user.phone.lstrip('+'), msg)
        logger.info(f"Hot lead alert sent for lead={lead.id} score={lead.score} agent={agent.id}")
    except Exception as exc:
        logger.warning(f"Hot lead WA alert failed for lead {lead.id}: {exc}")
