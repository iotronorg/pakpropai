"""
ADR-002 backfill: WhatsApp sessions on a production organization's number are
live data. Sessions without an organization are left unchanged. Reverse is a no-op.
"""
from django.db import migrations


def backfill(apps, schema_editor):
    WhatsAppSession = apps.get_model('whatsapp', 'WhatsAppSession')
    Organization = apps.get_model('organizations', 'Organization')
    production_org_ids = Organization.objects.filter(
        operational_mode='production',
    ).values_list('id', flat=True)
    WhatsAppSession.objects.filter(
        organization_id__in=list(production_org_ids), is_sandbox=True,
    ).update(is_sandbox=False)


class Migration(migrations.Migration):

    dependencies = [
        ('whatsapp', '0010_copilot_session'),
        ('organizations', '0022_organization_theme'),
    ]

    operations = [
        migrations.RunPython(backfill, migrations.RunPython.noop),
    ]
