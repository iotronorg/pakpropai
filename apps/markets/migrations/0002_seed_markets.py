"""
T-1050a / ADR-019: seed the Market table from the code registry.

Values are frozen here (not imported from apps/markets/registry.py) so a later
registry edit cannot change what this migration did. Existing rows are left
untouched: an operator may have edited them. A test keeps these values
consistent with the registry.

PK is the launched market; AE/GB/US have market packs but are not launched;
CA has only a registry entry.
"""
from django.db import migrations

SEED = [
    dict(code='PK', name='Pakistan', status='active',
         default_currency='PKR', currencies=['PKR'], currency_symbol='₨',
         default_area_unit='marla', area_units=['marla', 'kanal', 'sqft', 'sqm', 'acre'],
         default_language='ur', languages=['ur', 'en'], timezone='Asia/Karachi',
         price_format='crore / lakh  (1 crore = PKR 10,000,000 ; 1 lakh = PKR 100,000)',
         tax_system='Pakistan FBR (Section 7E CVT, CGT, WHT, Stamp Duty)'),
    dict(code='AE', name='United Arab Emirates', status='beta',
         default_currency='AED', currencies=['AED'], currency_symbol='د.إ',
         default_area_unit='sqft', area_units=['sqft', 'sqm', 'acre'],
         default_language='en', languages=['en'], timezone='Asia/Dubai',
         price_format='million / thousand AED',
         tax_system='UAE — no income tax; 4% DLD Transfer Fee on purchase'),
    dict(code='GB', name='United Kingdom', status='beta',
         default_currency='GBP', currencies=['GBP'], currency_symbol='£',
         default_area_unit='sqft', area_units=['sqft', 'sqm', 'acre'],
         default_language='en', languages=['en'], timezone='Europe/London',
         price_format='thousands / millions GBP',
         tax_system='UK — SDLT on purchase, CGT 18%/24% on gains, annual Council Tax'),
    dict(code='US', name='United States', status='beta',
         default_currency='USD', currencies=['USD'], currency_symbol='$',
         default_area_unit='sqft', area_units=['sqft', 'sqm', 'acre'],
         default_language='en', languages=['en'], timezone='America/New_York',
         price_format='thousands / millions USD',
         tax_system='US — annual county-level Property Tax (~1-2% of value), federal CGT on sale'),
    dict(code='CA', name='Canada', status='inactive',
         default_currency='CAD', currencies=['CAD'], currency_symbol='$',
         default_area_unit='sqft', area_units=['sqft', 'sqm', 'acre'],
         default_language='en', languages=['en'], timezone='America/Toronto',
         price_format='thousands / millions CAD',
         tax_system='Canada — Land Transfer Tax on purchase, combined Federal/Provincial CGT'),
]


def seed(apps, schema_editor):
    Market = apps.get_model('markets', 'Market')
    for row in SEED:
        Market.objects.get_or_create(code=row['code'], defaults=row)


class Migration(migrations.Migration):

    dependencies = [
        ('markets', '0001_market'),
    ]

    operations = [
        migrations.RunPython(seed, migrations.RunPython.noop),
    ]
