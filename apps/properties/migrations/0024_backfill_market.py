"""
T-1050a / ADR-019: set Property.market from country (expand step).

Rows whose country has no Market row keep market = NULL; list them with
`python manage.py market_backfill_report`. Idempotent (only NULL markets are
touched); the reverse is a no-op because the column itself is dropped by the
previous migration's reverse.
"""
from django.db import migrations
from django.db.models.functions import Upper


def backfill(apps, schema_editor):
    Market = apps.get_model('markets', 'Market')
    Model = apps.get_model('properties', 'Property')
    for code in Market.objects.values_list('code', flat=True):
        (Model.objects
            .annotate(cc=Upper('country'))
            .filter(market__isnull=True, cc=code)
            .update(market_id=code))


class Migration(migrations.Migration):

    dependencies = [
        ('properties', '0023_property_market_alter_property_area_unit_and_more'),
        ('markets', '0002_seed_markets'),
    ]

    operations = [
        migrations.RunPython(backfill, migrations.RunPython.noop),
    ]
