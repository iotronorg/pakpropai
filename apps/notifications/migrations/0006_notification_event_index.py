"""Partial index for the delivery report (ADR-050). Built CONCURRENTLY (atomic=False)."""
from django.contrib.postgres.operations import AddIndexConcurrently
from django.db import migrations, models


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ('notifications', '0005_notification_event'),
    ]

    operations = [
        AddIndexConcurrently(
            model_name='notification',
            index=models.Index(condition=~models.Q(event=''), fields=['event', 'created_at'],
                               name='notification_event_idx'),
        ),
    ]
