"""Listings already waiting for review get a queue item (T-2014)."""
from datetime import timedelta

from django.db import migrations
from django.utils import timezone


def forwards(apps, schema_editor):
    Item = apps.get_model('moderation', 'ModerationItem')
    Property = apps.get_model('properties', 'Property')
    due = timezone.now() + timedelta(hours=24)
    have = Item.objects.filter(status='open').values('listing_id')
    ids = list(Property.objects.filter(status='pending_review').exclude(pk__in=have).values_list('pk', flat=True))
    Item.objects.bulk_create([Item(listing_id=pk, reason='review', due_at=due) for pk in ids], batch_size=500)


class Migration(migrations.Migration):
    dependencies = [('moderation', '0001_moderation')]
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
