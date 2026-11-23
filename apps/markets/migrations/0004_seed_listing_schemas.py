"""
Seed listing attribute schemas for PK, AE and GB (T-1070e). Only fills an
empty schema, so admin edits survive a re-run. US/CA stay {} (no rules).
Reverse empties only schemas still equal to the seed.
"""
from django.db import migrations

_BOOL = {'type': 'boolean'}

SCHEMAS = {
    'PK': {
        'fields': {
            'possession':       {'type': 'enum', 'values': ['available', 'not_available'],
                                 'label': 'Possession'},
            'corner':           dict(_BOOL, label='Corner plot'),
            'park_facing':      dict(_BOOL, label='Park facing'),
            'main_road':        dict(_BOOL, label='On main road / boulevard'),
            'plot_number':      {'type': 'string', 'max_length': 30, 'label': 'Plot / house number'},
            'block':            {'type': 'string', 'max_length': 50, 'label': 'Block / phase'},
            'society_approval': {'type': 'enum', 'values': ['approved', 'pending', 'unknown'],
                                 'label': 'Society approval (LDA/CDA/RDA)'},
        },
        'additional': 'reject',
    },
    'AE': {
        'fields': {
            'rera_permit_number': {'type': 'string', 'max_length': 40, 'pattern': '^[A-Za-z0-9-]+$',
                                   'label': 'RERA / Trakheesi permit number'},
            'broker_orn':         {'type': 'string', 'max_length': 20, 'pattern': '^[0-9]+$',
                                   'label': 'Broker ORN'},
            'freehold':           dict(_BOOL, label='Freehold'),
            'chiller_free':       dict(_BOOL, label='Chiller free'),
            'rent_cheques':       {'type': 'integer', 'min': 1, 'max': 12, 'label': 'Rent cheques'},
        },
        'additional': 'reject',
    },
    'GB': {
        'fields': {
            'epc_rating':            {'type': 'enum', 'values': list('ABCDEFG'), 'label': 'EPC rating'},
            'tenure':                {'type': 'enum',
                                      'values': ['freehold', 'leasehold', 'share_of_freehold', 'commonhold'],
                                      'label': 'Tenure'},
            'lease_years_remaining': {'type': 'integer', 'min': 0, 'max': 999,
                                      'label': 'Lease years remaining'},
            'council_tax_band':      {'type': 'enum', 'values': list('ABCDEFGHI'),
                                      'label': 'Council tax band'},
            'ground_rent':           {'type': 'number', 'min': 0, 'label': 'Ground rent per year'},
        },
        'additional': 'reject',
    },
}


def seed(apps, schema_editor):
    Market = apps.get_model('markets', 'Market')
    for code, schema in SCHEMAS.items():
        Market.objects.filter(code=code, listing_schema={}).update(listing_schema=schema)


def unseed(apps, schema_editor):
    Market = apps.get_model('markets', 'Market')
    for code, schema in SCHEMAS.items():
        Market.objects.filter(code=code, listing_schema=schema).update(listing_schema={})


class Migration(migrations.Migration):
    dependencies = [
        ('markets', '0003_market_listing_schema'),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
