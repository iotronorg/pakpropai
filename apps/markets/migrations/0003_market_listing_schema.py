"""
Market.listing_schema (T-1070e, FR-LST-004). Additive: NOT NULL with a
constant db_default, so pre-v2 code can still insert markets.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('markets', '0002_seed_markets'),
    ]

    operations = [
        migrations.AddField(
            model_name='market',
            name='listing_schema',
            field=models.JSONField(blank=True, db_default={}, default=dict,
                                   help_text='Validates Property.attributes for listings in this market '
                                             '(format: apps/markets/listing_schema.py); {} = no rules'),
        ),
    ]
