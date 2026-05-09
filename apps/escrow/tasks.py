import logging
from celery import shared_task
from django.utils import timezone

logger = logging.getLogger(__name__)


@shared_task
def expire_deal_locks():
    """Mark LOCKED deals past their expiry as EXPIRED. Run every 30 minutes."""
    from .models import EscrowDeal
    from apps.whatsapp.client import WhatsAppClient

    now     = timezone.now()
    expired = EscrowDeal.objects.filter(status=EscrowDeal.Status.LOCKED, lock_expires_at__lte=now)
    count   = expired.count()

    for deal in expired.select_related('buyer', 'seller', 'agent__user', 'property__owner'):
        deal.status = EscrowDeal.Status.EXPIRED
        deal.save(update_fields=['status', 'updated_at'])
        _notify_buyer_expired(deal)
        _notify_seller_expired(deal)

    if count:
        logger.info(f"Expired {count} deal lock(s).")
    return count


def _notify_buyer_expired(deal):
    try:
        from apps.whatsapp.client import WhatsAppClient
        phone = deal.buyer.phone.lstrip('+')
        msg = (
            f"⏰ *Deal Lock Expired*\n\n"
            f"Your 48-hour exclusivity on *{deal.property.title}* has ended.\n\n"
            "The property is now available to other buyers. "
            "Type *lock property* to start a new deal lock if you're still interested."
        )
        WhatsAppClient.send_text(phone, msg)
    except Exception as exc:
        logger.warning(f"Deal lock expiry buyer notify failed deal={deal.pk}: {exc}")


def _notify_seller_expired(deal):
    """Notify the seller (or property owner) and agent that the deal lock expired."""
    try:
        from apps.whatsapp.client import WhatsAppClient

        title = deal.property.title

        # Notify seller if set, otherwise notify the property owner
        seller = deal.seller or getattr(deal.property, 'owner', None)
        if seller and seller.phone:
            phone = seller.phone.lstrip('+')
            WhatsAppClient.send_text(
                phone,
                f"⏰ *Deal Lock Expired*\n\n"
                f"The 48-hour deal lock on *{title}* has expired without completing payment.\n\n"
                "The property is available again. Contact us if you have questions."
            )

        # Notify the agent who facilitated the deal
        agent = deal.agent
        if agent and agent.user and agent.user.phone:
            phone = agent.user.phone.lstrip('+')
            WhatsAppClient.send_text(
                phone,
                f"⏰ *Deal Lock Expired*\n\n"
                f"The deal lock on *{title}* has expired.\n\n"
                "Please follow up with the buyer and seller if needed."
            )
    except Exception as exc:
        logger.warning(f"Deal lock expiry seller notify failed deal={deal.pk}: {exc}")
