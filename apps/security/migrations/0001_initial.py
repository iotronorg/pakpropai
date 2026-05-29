import uuid
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True
    dependencies = []

    operations = [
        migrations.CreateModel(
            name='ApiSecurityEvent',
            fields=[
                ('id',              models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('event_type',      models.CharField(db_index=True, max_length=30)),
                ('severity',        models.SmallIntegerField(default=3)),
                ('ip_address',      models.GenericIPAddressField(blank=True, db_index=True, null=True)),
                ('user_id',         models.CharField(blank=True, db_index=True, max_length=100)),
                ('organization_id', models.CharField(blank=True, db_index=True, max_length=100)),
                ('endpoint',        models.CharField(blank=True, max_length=255)),
                ('http_method',     models.CharField(blank=True, max_length=10)),
                ('threat_detail',   models.TextField(blank=True)),
                ('request_id',      models.CharField(blank=True, db_index=True, max_length=64)),
                ('prev_hash',       models.CharField(blank=True, max_length=64)),
                ('record_hash',     models.CharField(blank=True, db_index=True, max_length=64)),
                ('created_at',      models.DateTimeField(auto_now_add=True, db_index=True)),
            ],
            options={
                'db_table': 'api_security_events',
                'ordering': ['-created_at'],
                'indexes': [
                    models.Index(fields=['event_type', '-created_at'], name='sec_evt_type_created_idx'),
                    models.Index(fields=['ip_address',  '-created_at'], name='sec_ip_created_idx'),
                    models.Index(fields=['organization_id', '-created_at'], name='sec_org_created_idx'),
                ],
            },
        ),
    ]
