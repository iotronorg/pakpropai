"""
Data migration: populate lead.organization from the assigned agent's organization.

For each Lead whose assigned_agent has an organization, set lead.organization to match.
"""
from django.db import migrations


def populate_lead_organization(apps, schema_editor):
    Lead = apps.get_model('leads', 'Lead')

    to_update = []
    for lead in Lead.objects.filter(assigned_agent__isnull=False).select_related('assigned_agent'):
        org_id = lead.assigned_agent.organization_id
        if org_id:
            lead.organization_id = org_id
            to_update.append(lead)

    if to_update:
        Lead.objects.bulk_update(to_update, ['organization'])


def reverse_migration(apps, schema_editor):
    Lead = apps.get_model('leads', 'Lead')
    Lead.objects.update(organization=None)


class Migration(migrations.Migration):

    dependencies = [
        ('leads', '0007_add_organization_fk'),
        ('agents', '0006_data_populate_organization_from_parent'),
    ]

    operations = [
        migrations.RunPython(populate_lead_organization, reverse_migration),
    ]
