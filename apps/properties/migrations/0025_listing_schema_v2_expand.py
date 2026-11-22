"""
Listing schema v2 — expand phase (T-1070a, ADR-023).

Additive only: nullable columns, or NOT NULL with a constant DB default (a
metadata-only change on Postgres 11+, no table rewrite). Pre-v2 code that
inserts without these columns keeps working during a rolling deploy.
Existing rows become purpose='sale' (every scraped and seeded listing is a sale).
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('properties', '0024_backfill_market'),
    ]

    operations = [
        migrations.AddField(
            model_name='property',
            name='attributes',
            field=models.JSONField(blank=True, db_default={}, default=dict, help_text='Market-specific attributes (validated by the market schema, T-1070f)'),
        ),
        migrations.AddField(
            model_name='property',
            name='bathrooms',
            field=models.PositiveSmallIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='property',
            name='bedrooms',
            field=models.PositiveSmallIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='property',
            name='deposit',
            field=models.BigIntegerField(blank=True, help_text='Security deposit, in `currency`', null=True),
        ),
        migrations.AddField(
            model_name='property',
            name='floor',
            field=models.SmallIntegerField(blank=True, help_text='Floor of the unit; 0 = ground, negative = basement', null=True),
        ),
        migrations.AddField(
            model_name='property',
            name='parking_spaces',
            field=models.PositiveSmallIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='property',
            name='pets_allowed',
            field=models.BooleanField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='property',
            name='price_negotiable',
            field=models.BooleanField(db_default=False, default=False),
        ),
        migrations.AddField(
            model_name='property',
            name='price_on_request',
            field=models.BooleanField(db_default=False, default=False),
        ),
        migrations.AddField(
            model_name='property',
            name='price_type',
            field=models.CharField(choices=[('total', 'Total'), ('per_month', 'Per Month'), ('per_year', 'Per Year'), ('per_sqm', 'Per m²')], db_default='total', default='total', max_length=20),
        ),
        migrations.AddField(
            model_name='property',
            name='purpose',
            field=models.CharField(choices=[('sale', 'For Sale'), ('rent_long', 'For Rent (long term)'), ('rent_short', 'For Rent (short term)'), ('lease_commercial', 'Commercial Lease')], db_default='sale', default='sale', max_length=20),
        ),
        migrations.AddField(
            model_name='property',
            name='service_charge',
            field=models.BigIntegerField(blank=True, help_text='Service/maintenance charge per year, in `currency`', null=True),
        ),
        migrations.AddField(
            model_name='property',
            name='total_floors',
            field=models.PositiveSmallIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='property',
            name='year_built',
            field=models.PositiveSmallIntegerField(blank=True, null=True),
        ),
    ]
