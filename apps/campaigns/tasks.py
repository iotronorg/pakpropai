import logging
from celery import shared_task
from django.utils import timezone

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=10, default_retry_delay=60)
def dispatch_campaign_to_recipients(self, campaign_id: str):
    """Fan out a campaign to all pending CampaignRecipient rows with rate limiting."""
    from .models import Campaign, CampaignRecipient
    from .campaign_manager import (
        CampaignOrchestrator, MetaTierRateLimiter,
        RateLimitExceeded, MetaRateLimitError,
    )
    from apps.whatsapp.client import get_wa_client

    try:
        campaign = Campaign.objects.select_related('organization').get(id=campaign_id)
    except Campaign.DoesNotExist:
        logger.error("dispatch_campaign_to_recipients: campaign %s not found", campaign_id)
        return

    if campaign.status != Campaign.Status.SENDING:
        logger.warning("Campaign %s not in SENDING state — skipping", campaign_id)
        return

    orchestrator = CampaignOrchestrator()
    limiter      = MetaTierRateLimiter()
    wa_client    = get_wa_client(org=campaign.organization)

    orchestrator.build_recipients(campaign)

    pending = CampaignRecipient.objects.filter(
        campaign=campaign,
        delivery_status=CampaignRecipient.DeliveryStatus.PENDING,
    )

    for recipient in pending.iterator():
        try:
            orchestrator.dispatch_one(recipient, wa_client, limiter)
        except RateLimitExceeded:
            backoff = limiter.get_backoff_seconds(self.request.retries)
            logger.info("Campaign %s: rate limit hit, retrying in %.1fs", campaign_id, backoff)
            raise self.retry(countdown=backoff)
        except MetaRateLimitError:
            backoff = limiter.get_backoff_seconds(self.request.retries)
            logger.warning("Campaign %s: Meta 429, retrying in %.1fs", campaign_id, backoff)
            raise self.retry(countdown=backoff)

    from django.db.models import Count, Case, When, IntegerField
    agg = CampaignRecipient.objects.filter(campaign=campaign).aggregate(
        sent   = Count(Case(When(delivery_status='sent',   then=1), output_field=IntegerField())),
        failed = Count(Case(When(delivery_status='failed', then=1), output_field=IntegerField())),
    )
    campaign.sent_count   = agg['sent']   or 0
    campaign.failed_count = agg['failed'] or 0
    campaign.status       = Campaign.Status.SENT if campaign.sent_count > 0 else Campaign.Status.FAILED
    campaign.sent_at      = timezone.now()
    campaign.save(update_fields=['sent_count', 'failed_count', 'status', 'sent_at'])
    logger.info("Campaign %s complete: %d sent, %d failed", campaign_id,
                campaign.sent_count, campaign.failed_count)


@shared_task
def dispatch_scheduled_campaigns():
    """Every 5 min: dispatch campaigns whose scheduled_at has passed."""
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
        dispatch_campaign_to_recipients.delay(str(campaign.id))
        count += 1

    if count:
        logger.info("dispatch_scheduled_campaigns: dispatched %d campaign(s)", count)
    return count


@shared_task(bind=True, max_retries=3, default_retry_delay=300)
def run_reengagement_worker(self, org_id: str = None):
    """Every 6 hours: send AI re-engagement messages to cold QUALIFIED leads."""
    try:
        from .re_engagement import run_reengagement_pass
        return run_reengagement_pass(org_id=org_id)
    except Exception as exc:
        logger.error("run_reengagement_worker error: %s", exc)
        raise self.retry(exc=exc)
