import uuid
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('agents', '0007_remove_parent_organization'),
        ('organizations', '0005_organization_wa_phone_number_id'),
    ]

    operations = [
        # DB-level constraint: internal agents must have an organization
        migrations.AddConstraint(
            model_name='agent',
            constraint=models.CheckConstraint(
                check=(
                    models.Q(employment_type='freelance') |
                    (models.Q(employment_type='internal') & models.Q(organization__isnull=False))
                ),
                name='internal_agent_requires_organization',
            ),
        ),
        # Junction table for freelance agents collaborating with multiple orgs
        migrations.CreateModel(
            name='AgentOrgMembership',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('role', models.CharField(
                    choices=[('primary', 'Primary (exclusive partner)'), ('collaborator', 'Collaborator (non-exclusive)')],
                    default='collaborator',
                    max_length=20,
                )),
                ('status', models.CharField(
                    choices=[('active', 'Active'), ('suspended', 'Suspended'), ('ended', 'Ended')],
                    default='active',
                    max_length=20,
                )),
                ('commission_pct', models.DecimalField(
                    blank=True, decimal_places=2, max_digits=5, null=True,
                    help_text='Commission percentage agreed with this org (overrides agent default)',
                )),
                ('can_access_inventory', models.BooleanField(default=True)),
                ('can_receive_leads',    models.BooleanField(default=True)),
                ('notes',      models.TextField(blank=True)),
                ('started_at', models.DateField(blank=True, null=True)),
                ('ended_at',   models.DateField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('agent', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='org_memberships',
                    to='agents.agent',
                )),
                ('organization', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='freelance_memberships',
                    to='organizations.organization',
                )),
            ],
            options={
                'db_table':  'agent_org_memberships',
                'ordering':  ['-created_at'],
            },
        ),
        migrations.AddIndex(
            model_name='agentorgmembership',
            index=models.Index(fields=['organization', 'status'], name='aom_org_status_idx'),
        ),
        migrations.AddIndex(
            model_name='agentorgmembership',
            index=models.Index(fields=['agent', 'status'], name='aom_agent_status_idx'),
        ),
        migrations.AlterUniqueTogether(
            name='agentorgmembership',
            unique_together={('agent', 'organization')},
        ),
        # NOTE: The CheckConstraint on AgentOrgMembership referencing
        # agent__employment_type is enforced at the application layer (Agent.clean)
        # rather than at the DB level, as PostgreSQL CHECK constraints cannot
        # span across tables.
    ]
