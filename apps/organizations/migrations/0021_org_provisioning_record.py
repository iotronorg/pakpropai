import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('organizations', '0020_operational_mode'),
    ]

    operations = [
        migrations.CreateModel(
            name='OrgProvisioningRecord',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('operational_mode', models.CharField(
                    choices=[
                        ('sandbox', 'Sandbox'),
                        ('provisioning', 'Provisioning'),
                        ('production', 'Production'),
                        ('failed', 'Failed'),
                    ],
                    default='sandbox',
                    max_length=15,
                )),
                ('waba_verified_at', models.DateTimeField(blank=True, null=True)),
                ('webhook_verified_at', models.DateTimeField(blank=True, null=True)),
                ('templates_approved_at', models.DateTimeField(blank=True, null=True)),
                ('data_migrated_at', models.DateTimeField(blank=True, null=True)),
                ('sandbox_leads_migrated', models.IntegerField(default=0)),
                ('sandbox_sessions_migrated', models.IntegerField(default=0)),
                ('sandbox_properties_migrated', models.IntegerField(default=0)),
                ('error_detail', models.TextField(blank=True)),
                ('last_step', models.CharField(
                    blank=True,
                    choices=[
                        ('waba', 'WABA Verification'),
                        ('webhook', 'Webhook Registration'),
                        ('templates', 'Template Sync'),
                        ('data', 'Data Migration'),
                        ('complete', 'Complete'),
                    ],
                    max_length=15,
                )),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('organization', models.OneToOneField(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='provisioning_record',
                    to='organizations.organization',
                )),
            ],
            options={'db_table': 'org_provisioning_records'},
        ),
    ]
