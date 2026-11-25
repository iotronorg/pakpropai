"""Notification.event: what caused a notification, to measure delivery (ADR-050). Additive."""
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('notifications', '0004_saved_searches_and_preferences'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        # db_default: no table rewrite, and older pods that do not send the column still insert.
        migrations.AddField(
            model_name='notification',
            name='event',
            field=models.CharField(blank=True, db_default='', default='', max_length=40),
        ),
    ]
