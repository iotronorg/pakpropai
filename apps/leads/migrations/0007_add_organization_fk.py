import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('leads', '0006_add_notification_prefs_and_score_history'),
        ('organizations', '0002_data_migrate_from_developers'),
    ]

    operations = [
        migrations.AddField(
            model_name='lead',
            name='organization',
            field=models.ForeignKey(
                blank=True,
                help_text='Organization this lead is scoped to (null = platform-wide lead)',
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='leads',
                to='organizations.organization',
            ),
        ),
        migrations.AddIndex(
            model_name='lead',
            index=models.Index(fields=['organization'], name='lead_org_idx'),
        ),
    ]
