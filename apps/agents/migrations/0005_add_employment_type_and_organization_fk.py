import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('agents', '0004_agent_availability'),
        ('organizations', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='agent',
            name='organization',
            field=models.ForeignKey(
                blank=True,
                help_text='Organization this agent belongs to (null = independent freelance agent)',
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='agents',
                to='organizations.organization',
            ),
        ),
        migrations.AddField(
            model_name='agent',
            name='employment_type',
            field=models.CharField(
                choices=[('internal', 'Internal (Employee)'), ('freelance', 'Freelance (Independent)')],
                default='freelance',
                help_text='Internal = employed by the organization; Freelance = independent contractor',
                max_length=20,
            ),
        ),
        migrations.AlterField(
            model_name='agent',
            name='parent_organization',
            field=models.ForeignKey(
                blank=True,
                help_text='[DEPRECATED] Use agent.organization instead.',
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='team_members',
                to='agents.agent',
            ),
        ),
    ]
