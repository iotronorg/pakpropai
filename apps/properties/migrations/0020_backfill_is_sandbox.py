"""
ADR-002 backfill: recompute Property.is_sandbox from ownership.

- platform listings                          -> sandbox
- organization listings, org in production   -> live
- organization listings, org not production  -> sandbox
- client / freelance_agent listings          -> live

Only the derived flag changes; no rows are created or deleted. Reverse is a
no-op (the previous values carried no consistent meaning).
"""
from django.db import migrations


def backfill(apps, schema_editor):
    Property = apps.get_model('properties', 'Property')
    Organization = apps.get_model('organizations', 'Organization')

    production_org_ids = list(
        Organization.objects.filter(operational_mode='production').values_list('id', flat=True)
    )

    Property.objects.filter(listing_owner_type='platform').update(is_sandbox=True)
    Property.objects.filter(
        listing_owner_type__in=['client', 'freelance_agent'],
    ).update(is_sandbox=False)
    Property.objects.filter(
        listing_owner_type='organization', organization_id__in=production_org_ids,
    ).update(is_sandbox=False)
    Property.objects.filter(
        listing_owner_type='organization',
    ).exclude(organization_id__in=production_org_ids).update(is_sandbox=True)


class Migration(migrations.Migration):

    dependencies = [
        ('properties', '0019_property_is_sandbox'),
        ('organizations', '0022_organization_theme'),
    ]

    operations = [
        migrations.RunPython(backfill, migrations.RunPython.noop),
    ]
