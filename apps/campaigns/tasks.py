import logging
from celery import shared_task
from django.utils import timezone

logger = logging.getLogger(__name__)

_INTENT_FILTERS = {'buy', 'sell', 'rent', 'invest'}
_STATUS_FILTERS = {'new', 'warm', 'qualified', 'cold'}


def _get_lead_phones(campaign) -> list[str]:
    """Return phone numbers for leads matching the campaign's audience_filter."""
    from apps.leads.models import Lead
    qs = Lead.objects.filter(
        organization=campaign.organization,
    ).select_related('user').exclude(user__phone='')

    af = campaign.audience_filter
    if af in _STATUS_FILTERS:
        qs = qs.filter(status=af)
    elif af in _INTENT_FILTERS:
        qs = qs.filter(intent=af)
    # 'all' — no additional filter

    phones = list(qs.values_list('user__phone', flat=True).distinct())
    return [p for p in phones if p]


@shared_task(bind=True, max_retries=2, default_retry_delay=60)
def send_campaign_messages(self, campaign_id: str):
    """Fan out WhatsApp messages to all leads matching the campaign audience filter."""
    from .models import Campaign
    from apps.whatsapp.client import WhatsAppClient

    try:
        campaign = Campaign.objects.select_related('organization').get(id=campaign_id)
    except Campaign.DoesNotExist:
        logger.error(f"send_campaign_messages: campaign {campaign_id} not found")
        return

    if campaign.status not in (Campaign.Status.SENDING,):
        logger.warning(f"Campaign {campaign_id} is not in SENDING state — skipping")
        return

    phones = _get_lead_phones(campaign)
    campaign.recipient_count = len(phones)
    campaign.save(update_fields=['recipient_count'])

    sent    = 0
    failed  = 0

    for phone in phones:
        try:
            WhatsAppClient.send_text(phone, campaign.message_template, skip_window_check=True)
            sent += 1
        except Exception as exc:
            failed += 1
            logger.warning(f"Campaign {campaign_id}: failed to send to {phone}: {exc}")

    final_status = Campaign.Status.SENT if sent > 0 or failed == 0 else Campaign.Status.FAILED
    campaign.sent_count   = sent
    campaign.failed_count = failed
    campaign.status       = final_status
    campaign.sent_at      = timezone.now()
    campaign.save(update_fields=['sent_count', 'failed_count', 'status', 'sent_at'])

    logger.info(
        f"Campaign {campaign_id} complete: {sent} sent, {failed} failed, "
        f"status={final_status}"
    )
    return {'sent': sent, 'failed': failed}


@shared_task
def dispatch_scheduled_campaigns():
    """
    Runs every 5 minutes. Finds campaigns that are SCHEDULED and past their
    scheduled_at time, then queues each for delivery.
    """
    from .models import Campaign

    now = timezone.now()
    due = Campaign.objects.filter(
        status=Campaign.Status.SCHEDULED,
        scheduled_at__lte=now,
    )

    count = 0
    for campaign in due:
        campaign.status = Campaign.Status.SENDING
        campaign.save(update_fields=['status'])
        send_campaign_messages.delay(str(campaign.id))
        count += 1

    if count:
        logger.info(f"dispatch_scheduled_campaigns: dispatched {count} campaign(s)")
    return count
