import logging
from django.db.models.signals import post_save
from django.dispatch import receiver

logger = logging.getLogger(__name__)

_NOTIFY_STATUSES = {'passed', 'failed', 'disputed'}


@receiver(post_save, sender='verification.Verification')
def notify_verification_status_change(sender, instance, created, **kwargs):
    """Send WhatsApp notification to property owner when verification is reviewed."""
    if created or instance.status not in _NOTIFY_STATUSES:
        return

    try:
        owner = instance.property.owner
        if not owner or not owner.phone:
            return

        phone = owner.phone.lstrip('+')
        title = instance.property.title

        if instance.status == 'passed':
            msg = (
                f"✅ *Verification Approved*\n\n"
                f"Your property *{title}* has been verified and approved.\n\n"
                "It is now marked as *Verified* on RealTron AI. "
                "Buyers can see the verified badge on your listing."
            )
        elif instance.status == 'failed':
            msg = (
                f"❌ *Verification Rejected*\n\n"
                f"Your property *{title}* could not be verified.\n\n"
                f"Reason: {instance.notes or 'Documents were insufficient or could not be confirmed.'}\n\n"
                "Please upload clearer ownership documents and request re-verification."
            )
        else:  # disputed
            msg = (
                f"⚠️ *Verification Under Review*\n\n"
                f"Your property *{title}* has been flagged for further review.\n\n"
                "Our team will contact you shortly. "
                "Please ensure your documents are ready for verification."
            )

        # T-1022a: outbox. The key makes this once per (verification, status):
        # the signal fires on every save, which used to re-send the notice.
        from apps.notifications.delivery import enqueue_whatsapp_text
        enqueue_whatsapp_text(phone, msg, key=f'verification:{instance.pk}:{instance.status}',
                              organization=instance.property.organization)
        logger.info(f"Verification notify queued — status={instance.status} property={instance.property_id}")

    except Exception as exc:
        logger.warning(f"Verification status notify failed for verification {instance.pk}: {exc}")
