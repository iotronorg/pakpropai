"""Keep duplicate clusters current after a listing or its photos change (T-4050, ADR-069)."""
from django.db import transaction
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from .models import Property, PropertyImage

# Fields of the fingerprint; other saves (views, counters, status-only) do not re-cluster.
WATCHED = {'country', 'city', 'location', 'location_ref', 'latitude', 'longitude', 'geo_lat', 'geo_lng',
           'purpose', 'category', 'bedrooms', 'currency', 'price_type', 'price', 'price_on_request',
           'size_value', 'size_unit', 'size_m2', 'status'}


def _queue(property_id):
    from .tasks import refresh_duplicate_cluster

    def run():
        try:
            refresh_duplicate_cluster.delay(str(property_id))
        except Exception:                                   # a broker outage must not fail the save
            import logging
            logging.getLogger(__name__).warning('duplicates: could not queue %s', property_id)
    transaction.on_commit(run)


@receiver(post_save, sender=Property, dispatch_uid='duplicates_listing_saved')
def listing_saved(sender, instance, created, update_fields=None, raw=False, **kwargs):
    if raw or (update_fields is not None and not WATCHED & set(update_fields)):
        return
    # The exact part of the fingerprint now (it is what candidates are found by); read back
    # from the database because a trigger fills the public coordinates.
    from .duplicates import key_for
    fresh = Property.objects.filter(pk=instance.pk).first()
    if fresh is not None and fresh.duplicate_key != (key := key_for(fresh)):
        Property.objects.filter(pk=instance.pk).update(duplicate_key=key)
        instance.duplicate_key = key
    _queue(instance.pk)


@receiver(post_save, sender=PropertyImage, dispatch_uid='duplicates_image_saved')
@receiver(post_delete, sender=PropertyImage, dispatch_uid='duplicates_image_deleted')
def image_changed(sender, instance, raw=False, **kwargs):
    if not raw:
        _queue(instance.property_id)
