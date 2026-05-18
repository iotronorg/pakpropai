"""
Data migration: populate property.organization from the owner's Organization.

For each Property whose owner has role='developer' and an owned_organization,
set property.organization to that Organization.
"""
from django.db import migrations


def populate_property_organization(apps, schema_editor):
    Property     = apps.get_model('properties', 'Property')
    Organization = apps.get_model('organizations', 'Organization')

    # Build lookup: user_id → Organization
    org_by_user = {
        org.admin_user_id: org
        for org in Organization.objects.all()
        if org.admin_user_id is not None
    }

    to_update = []
    for prop in Property.objects.filter(owner__isnull=False).select_related('owner'):
        org = org_by_user.get(prop.owner_id)
        if org:
            prop.organization = org
            to_update.append(prop)

    if to_update:
        Property.objects.bulk_update(to_update, ['organization'])


def reverse_migration(apps, schema_editor):
    Property = apps.get_model('properties', 'Property')
    Property.objects.update(organization=None)


class Migration(migrations.Migration):

    dependencies = [
        ('properties', '0007_add_organization_fk'),
        ('organizations', '0002_data_migrate_from_developers'),
    ]

    operations = [
        migrations.RunPython(populate_property_organization, reverse_migration),
    ]
