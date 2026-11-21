"""
ADR-002 backfill: leads belonging to a production organization are live data.
Leads created after go-live were left with the model default (sandbox=True).
Unscoped (organization=NULL) leads are left unchanged. Reverse is a no-op.
"""
from django.db import migrations


def backfill(apps, schema_editor):
    Lead = apps.get_model('leads', 'Lead')
    Organization = apps.get_model('organizations', 'Organization')
    production_org_ids = Organization.objects.filter(
        operational_mode='production',
    ).values_list('id', flat=True)
    Lead.objects.filter(
        organization_id__in=list(production_org_ids), is_sandbox=True,
    ).update(is_sandbox=False)


class Migration(migrations.Migration):

    dependencies = [
        ('leads', '0018_lead_is_sandbox'),
        ('organizations', '0022_organization_theme'),
    ]

    operations = [
        migrations.RunPython(backfill, migrations.RunPython.noop),
    ]
