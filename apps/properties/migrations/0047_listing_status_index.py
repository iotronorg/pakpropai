"""T-1080: lifecycle indexes (owner dashboards, the expiry job). CONCURRENTLY, atomic=False."""
from django.contrib.postgres.operations import AddIndexConcurrently
from django.db import migrations, models


class Migration(migrations.Migration):
    atomic = False

    dependencies = [('properties', '0046_backfill_listing_status')]

    operations = [
        AddIndexConcurrently('property', models.Index(fields=['organization', 'status'],
                                                      name='prop_org_status_idx')),
        AddIndexConcurrently('property', models.Index(fields=['expires_at'],
                                                      condition=models.Q(status='published'),
                                                      name='prop_expiry_idx')),
    ]
