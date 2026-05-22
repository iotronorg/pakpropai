"""
Data migration: compute area_sqm for all existing Property rows from area_marla + area_unit.
"""
from django.db import migrations

_SQM_FACTORS = {
    'marla':  25.2929,
    'kanal':  505.857,
    'sqft':   0.092903,
    'sqm':    1.0,
    'acre':   4046.856,
    'guntha': 101.171,
    'cent':   40.4686,
}


def populate_area_sqm(apps, schema_editor):
    Property = apps.get_model('properties', 'Property')
    to_update = []
    for prop in Property.objects.filter(area_marla__isnull=False, area_sqm__isnull=True):
        factor = _SQM_FACTORS.get(prop.area_unit or 'marla', 1.0)
        prop.area_sqm = round(float(prop.area_marla) * factor, 4)
        to_update.append(prop)
    if to_update:
        Property.objects.bulk_update(to_update, ['area_sqm'], batch_size=500)


def reverse_populate(apps, schema_editor):
    Property = apps.get_model('properties', 'Property')
    Property.objects.update(area_sqm=None)


class Migration(migrations.Migration):

    dependencies = [
        ('properties', '0016_property_area_sqm'),
    ]

    operations = [
        migrations.RunPython(populate_area_sqm, reverse_populate),
    ]
