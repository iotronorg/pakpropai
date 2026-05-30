import uuid
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('escrow', '0007_expand_gateway_choices'),
        ('leads', '0018_lead_is_sandbox'),
        ('organizations', '0022_organization_theme'),
        ('properties', '0019_property_is_sandbox'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='SyndicationListing',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('status', models.CharField(choices=[('draft', 'Draft'), ('syndicated', 'Syndicated'), ('withdrawn', 'Withdrawn')], default='draft', max_length=20)),
                ('commission_type', models.CharField(choices=[('fixed', 'Fixed'), ('percentage', 'Percentage')], max_length=20)),
                ('commission_value', models.DecimalField(decimal_places=4, max_digits=12)),
                ('commission_currency', models.CharField(default='USD', max_length=3)),
                ('syndication_scope', models.CharField(choices=[('platform_wide', 'Platform Wide'), ('selected_partners', 'Selected Partners')], default='platform_wide', max_length=30)),
                ('description', models.TextField(blank=True)),
                ('expires_at', models.DateTimeField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('developer_org', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='syndication_listings', to='organizations.organization')),
                ('property', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='syndication_listings', to='properties.property')),
            ],
            options={
                'db_table': 'syndication_listings',
                'unique_together': {('property', 'developer_org')},
            },
        ),
        migrations.CreateModel(
            name='BrokerNetworkPartnership',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('status', models.CharField(choices=[('invited', 'Invited'), ('active', 'Active'), ('suspended', 'Suspended'), ('revoked', 'Revoked')], default='invited', max_length=20)),
                ('commission_override_type', models.CharField(blank=True, choices=[('fixed', 'Fixed'), ('percentage', 'Percentage')], max_length=20, null=True)),
                ('commission_override_value', models.DecimalField(blank=True, decimal_places=4, max_digits=12, null=True)),
                ('invited_at', models.DateTimeField(auto_now_add=True)),
                ('activated_at', models.DateTimeField(blank=True, null=True)),
                ('notes', models.TextField(blank=True)),
                ('developer_org', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='broker_partnerships_as_developer', to='organizations.organization')),
                ('broker_org', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='broker_partnerships_as_broker', to='organizations.organization')),
                ('broker_agent', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='broker_partnerships', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'db_table': 'broker_network_partnerships',
            },
        ),
        migrations.AddConstraint(
            model_name='brokernetworkpartnership',
            constraint=models.CheckConstraint(
                check=models.Q(broker_org__isnull=False) | models.Q(broker_agent__isnull=False),
                name='partnership_has_broker_party',
            ),
        ),
        migrations.CreateModel(
            name='SyndicationLeadSubmission',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('status', models.CharField(choices=[('pending', 'Pending'), ('accepted', 'Accepted'), ('rejected', 'Rejected'), ('converted', 'Converted')], default='pending', max_length=20)),
                ('commission_calculated', models.DecimalField(blank=True, decimal_places=4, max_digits=12, null=True)),
                ('commission_currency', models.CharField(default='USD', max_length=3)),
                ('submitted_at', models.DateTimeField(auto_now_add=True)),
                ('reviewed_at', models.DateTimeField(blank=True, null=True)),
                ('listing', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='lead_submissions', to='marketplace.syndicationlisting')),
                ('lead', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='syndication_submissions', to='leads.lead')),
                ('submitted_by_org', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='submitted_syndication_leads', to='organizations.organization')),
                ('submitted_by_agent', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='submitted_syndication_leads', to=settings.AUTH_USER_MODEL)),
                ('deal_lock', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='syndication_submissions', to='escrow.escrowdeal')),
                ('reviewed_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='reviewed_syndication_submissions', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'db_table': 'syndication_lead_submissions',
            },
        ),
        migrations.CreateModel(
            name='CommissionLedgerEntry',
            fields=[
                ('entry_id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('commission_amount', models.DecimalField(decimal_places=4, max_digits=12)),
                ('commission_currency', models.CharField(default='USD', max_length=3)),
                ('commission_type', models.CharField(choices=[('fixed', 'Fixed'), ('percentage', 'Percentage')], max_length=20)),
                ('source_chain_hash', models.CharField(max_length=64)),
                ('prev_entry_hash', models.CharField(blank=True, max_length=64)),
                ('status', models.CharField(choices=[('pending', 'Pending'), ('confirmed', 'Confirmed'), ('paid', 'Paid'), ('disputed', 'Disputed')], default='pending', max_length=20)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('listing', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='ledger_entries', to='marketplace.syndicationlisting')),
                ('submission', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='ledger_entries', to='marketplace.syndicationleadsubmission')),
                ('deal_lock', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='commission_ledger_entries', to='escrow.escrowdeal')),
                ('developer_org', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='commission_entries_as_developer', to='organizations.organization')),
                ('broker_org', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='commission_entries_as_broker', to='organizations.organization')),
                ('broker_agent', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='commission_entries_as_broker_agent', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'db_table': 'commission_ledger_entries',
            },
        ),
    ]
