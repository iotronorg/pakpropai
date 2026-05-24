import django.db.models.deletion
import uuid
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('organizations', '0017_organization_brand_color'),
    ]

    operations = [
        migrations.AddField(
            model_name='organization',
            name='custom_domain',
            field=models.CharField(
                blank=True,
                db_index=True,
                help_text='White-label FQDN, e.g. portal.imarat.ai — resolved by TenantDomainMiddleware',
                max_length=255,
            ),
        ),
        migrations.CreateModel(
            name='DeveloperApiKey',
            fields=[
                ('id',           models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('name',         models.CharField(help_text='Label for identification, e.g. "HubSpot CRM"', max_length=100)),
                ('key_prefix',   models.CharField(max_length=12, unique=True, db_index=True)),
                ('key_hash',     models.CharField(max_length=128)),
                ('key_salt',     models.CharField(max_length=64)),
                ('scopes',       models.JSONField(default=list)),
                ('is_active',    models.BooleanField(db_index=True, default=True)),
                ('created_at',   models.DateTimeField(auto_now_add=True)),
                ('last_used_at', models.DateTimeField(blank=True, null=True)),
                ('expires_at',   models.DateTimeField(blank=True, null=True)),
                ('organization', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='api_keys',
                    to='organizations.organization',
                )),
            ],
            options={
                'db_table': 'developer_api_keys',
                'indexes': [
                    models.Index(fields=['organization', 'is_active'], name='dev_api_keys_org_active_idx'),
                ],
            },
        ),
    ]
