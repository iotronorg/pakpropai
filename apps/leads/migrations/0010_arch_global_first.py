from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('leads', '0009_remove_lead_lead_org_idx'),
    ]

    operations = [
        migrations.AddField(
            model_name='lead',
            name='routing_state',
            field=models.CharField(
                choices=[
                    ('ai_queue',       'AI Routing Queue (unscoped)'),
                    ('org_queue',      'Organization Queue (org-scoped, unassigned)'),
                    ('agent_assigned', 'Agent Assigned'),
                    ('closed',         'Closed / Converted'),
                ],
                db_index=True,
                default='ai_queue',
                help_text=(
                    'AI_QUEUE → unscoped; ORG_QUEUE → org-scoped but no agent yet; '
                    'AGENT_ASSIGNED → assigned; CLOSED → converted/disqualified'
                ),
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name='lead',
            name='priority',
            field=models.SmallIntegerField(
                default=0,
                help_text='Higher = more urgent. AI sets this; agents/admins can override.',
            ),
        ),
        migrations.AddField(
            model_name='lead',
            name='budget_currency',
            field=models.CharField(
                default='PKR',
                help_text='ISO 4217 currency code for budget_min/max, e.g. PKR, AED, USD',
                max_length=3,
            ),
        ),
        migrations.AddIndex(
            model_name='lead',
            index=models.Index(fields=['routing_state'], name='lead_routing_state_idx'),
        ),
        migrations.AddIndex(
            model_name='lead',
            index=models.Index(fields=['organization', 'routing_state'], name='lead_org_routing_idx'),
        ),
        migrations.AddIndex(
            model_name='lead',
            index=models.Index(fields=['priority'], name='lead_priority_idx'),
        ),
    ]
