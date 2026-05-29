import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('organizations', '0021_org_provisioning_record'),
        ('properties', '0019_property_is_sandbox'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='ExternalPlatformConnection',
            fields=[
                ('id',                  models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('platform',            models.CharField(choices=[('zameen','Zameen'),('propertyfinder','PropertyFinder'),('bayut','Bayut'),('rightmove','Rightmove'),('zillow','Zillow'),('custom','Custom')], max_length=20)),
                ('api_key',             models.CharField(blank=True, max_length=500)),
                ('api_secret',          models.CharField(blank=True, max_length=500)),
                ('base_url',            models.URLField(blank=True)),
                ('is_active',           models.BooleanField(default=True)),
                ('sync_direction',      models.CharField(choices=[('inbound','Inbound Only'),('outbound','Outbound Only'),('bidirectional','Bidirectional')], default='inbound', max_length=15)),
                ('conflict_resolution', models.CharField(choices=[('internal_wins','Internal Wins'),('external_wins','External Wins'),('manual','Manual Review')], default='internal_wins', max_length=15)),
                ('last_synced_at',      models.DateTimeField(blank=True, null=True)),
                ('sync_status',         models.CharField(choices=[('idle','Idle'),('syncing','Syncing'),('error','Error'),('paused','Paused')], db_index=True, default='idle', max_length=10)),
                ('error_detail',        models.TextField(blank=True)),
                ('field_mappings',      models.JSONField(blank=True, default=dict)),
                ('created_at',          models.DateTimeField(auto_now_add=True)),
                ('org',                 models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='platform_connections', to='organizations.organization')),
            ],
            options={'db_table': 'inventory_platform_connections', 'ordering': ['platform', 'created_at']},
        ),
        migrations.CreateModel(
            name='SyncConflictAlert',
            fields=[
                ('id',             models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('external_delta', models.JSONField(default=dict)),
                ('internal_state', models.JSONField(default=dict)),
                ('resolution',     models.CharField(choices=[('pending','Pending'),('internal_wins','Internal Wins'),('external_wins','External Wins'),('manual','Manual')], db_index=True, default='pending', max_length=15)),
                ('created_at',     models.DateTimeField(auto_now_add=True, db_index=True)),
                ('resolved_at',    models.DateTimeField(blank=True, null=True)),
                ('connection',     models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='conflict_alerts', to='inventory.externalplatformconnection')),
                ('org',            models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='sync_conflict_alerts', to='organizations.organization')),
                ('property',       models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='sync_conflicts', to='properties.property')),
                ('resolved_by',    models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='resolved_sync_conflicts', to=settings.AUTH_USER_MODEL)),
            ],
            options={'db_table': 'inventory_sync_conflict_alerts', 'ordering': ['-created_at']},
        ),
        migrations.CreateModel(
            name='WebhookDeliveryRecord',
            fields=[
                ('id',            models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('event_type',    models.CharField(blank=True, max_length=50)),
                ('payload',       models.JSONField(default=dict)),
                ('status',        models.CharField(choices=[('pending','Pending'),('delivered','Delivered'),('failed','Failed')], db_index=True, default='pending', max_length=10)),
                ('attempt_count', models.PositiveIntegerField(default=0)),
                ('delivered_at',  models.DateTimeField(blank=True, null=True)),
                ('error_detail',  models.CharField(blank=True, max_length=500)),
                ('created_at',    models.DateTimeField(auto_now_add=True, db_index=True)),
                ('connection',    models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='webhook_records', to='inventory.externalplatformconnection')),
                ('org',           models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='webhook_deliveries', to='organizations.organization')),
            ],
            options={'db_table': 'inventory_webhook_delivery_records', 'ordering': ['-created_at']},
        ),
    ]
