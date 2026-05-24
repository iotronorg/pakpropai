import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('whatsapp', '0003_orgwhatsappconfig'),
        ('organizations', '0016_data_migrate_membership_admin_to_org_admin'),
    ]

    operations = [
        migrations.AddField(
            model_name='whatsappsession',
            name='organization',
            field=models.ForeignKey(
                blank=True,
                db_index=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='org_wa_sessions',
                to='organizations.organization',
                help_text="Organization that owns this session's WhatsApp number",
            ),
        ),
    ]
