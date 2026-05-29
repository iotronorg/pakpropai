from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('organizations', '0019_rename_dev_api_keys_org_active_idx_developer_a_organiz_70be96_idx_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='organization',
            name='operational_mode',
            field=models.CharField(
                choices=[
                    ('sandbox', 'Sandbox'),
                    ('provisioning', 'Provisioning'),
                    ('production', 'Production'),
                    ('failed', 'Failed'),
                ],
                db_index=True,
                default='sandbox',
                max_length=15,
            ),
        ),
    ]
