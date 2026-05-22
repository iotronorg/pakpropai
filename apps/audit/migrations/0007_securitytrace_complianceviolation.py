import uuid
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('audit', '0006_alter_auditbenchmark_options_and_more'),
    ]

    operations = [
        migrations.CreateModel(
            name='SecurityTrace',
            fields=[
                ('id',              models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, serialize=False)),
                ('phone',           models.CharField(blank=True, db_index=True, max_length=30)),
                ('organization_id', models.CharField(blank=True, db_index=True, max_length=100)),
                ('violation_type',  models.CharField(db_index=True, max_length=30,
                                        help_text='injection | adversarial | jailbreak | profanity | off_topic')),
                ('severity',        models.SmallIntegerField(choices=[(1, 'Low'), (2, 'Medium'), (3, 'High'), (4, 'Critical')], default=3)),
                ('message_excerpt', models.TextField(blank=True, help_text='First 300 chars of the blocked message')),
                ('created_at',      models.DateTimeField(auto_now_add=True, db_index=True)),
            ],
            options={
                'db_table': 'security_traces',
                'ordering': ['-created_at'],
            },
        ),
        migrations.AddIndex(
            model_name='securitytrace',
            index=models.Index(fields=['violation_type', '-created_at'], name='sec_trace_type_ts_idx'),
        ),
        migrations.AddIndex(
            model_name='securitytrace',
            index=models.Index(fields=['phone', '-created_at'], name='sec_trace_phone_ts_idx'),
        ),
        migrations.CreateModel(
            name='ComplianceViolation',
            fields=[
                ('id',              models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, serialize=False)),
                ('phone',           models.CharField(blank=True, db_index=True, max_length=30)),
                ('organization_id', models.CharField(blank=True, db_index=True, max_length=100)),
                ('property_id',     models.CharField(blank=True, db_index=True, max_length=100)),
                ('violation_type',  models.CharField(db_index=True, max_length=30,
                                        help_text='price_variance | false_installment | location_mismatch')),
                ('details',         models.TextField(blank=True)),
                ('created_at',      models.DateTimeField(auto_now_add=True, db_index=True)),
            ],
            options={
                'db_table': 'compliance_violations',
                'ordering': ['-created_at'],
            },
        ),
        migrations.AddIndex(
            model_name='complianceviolation',
            index=models.Index(fields=['violation_type', '-created_at'], name='comp_viol_type_ts_idx'),
        ),
        migrations.AddIndex(
            model_name='complianceviolation',
            index=models.Index(fields=['organization_id', '-created_at'], name='comp_viol_org_ts_idx'),
        ),
    ]
