from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('leads', '0013_crm_contact_client_profile'),
    ]

    operations = [
        migrations.AddField(
            model_name='lead',
            name='score_factors',
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
