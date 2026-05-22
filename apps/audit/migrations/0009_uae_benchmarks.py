"""Seed UAE (AED/sqft) market benchmarks into AuditBenchmark."""
from django.db import migrations

# (city, location_key, price_per_unit_min, price_per_unit_max,
#  yield_pct, appr_pct, liq_months, approved)
# Prices in AED per sqft — sourced from DLD/Bayut/PropertyFinder 2024 medians.
UAE_BENCHMARKS = [
    # Dubai
    ('dubai', 'palm jumeirah',  2_500, 4_500, 5.0, 10, 3, True),
    ('dubai', 'downtown dubai', 2_200, 3_500, 4.5,  8, 3, True),
    ('dubai', 'dubai marina',   1_800, 2_800, 6.0,  7, 2, True),
    ('dubai', 'business bay',   1_500, 2_200, 6.5,  9, 2, True),
    ('dubai', 'jumeirah lake towers', 1_200, 1_800, 7.0, 8, 2, True),
    ('dubai', 'jumeirah',       1_800, 2_800, 4.5,  7, 3, True),
    ('dubai', 'dubai hills',    1_600, 2_400, 5.5,  9, 2, True),
    ('dubai', 'arabian ranches', 1_400, 2_000, 4.5, 8, 3, True),
    ('dubai', 'default',        1_000, 2_000, 5.5,  7, 3, None),
    # Abu Dhabi
    ('abu dhabi', 'saadiyat island', 1_500, 2_500, 4.5, 8, 3, True),
    ('abu dhabi', 'yas island',      1_200, 2_000, 5.5, 7, 3, True),
    ('abu dhabi', 'al reem island',  1_200, 2_000, 6.0, 7, 2, True),
    ('abu dhabi', 'al khalidiyah',   1_000, 1_600, 5.5, 6, 4, True),
    ('abu dhabi', 'default',           900, 1_800, 5.5, 6, 4, None),
    # Sharjah
    ('sharjah', 'al majaz',   500,  900, 7.0, 5, 4, True),
    ('sharjah', 'al nahda',   400,  700, 7.5, 5, 4, True),
    ('sharjah', 'muwaileh',   350,  600, 8.0, 5, 5, True),
    ('sharjah', 'default',    350,  750, 7.5, 5, 5, None),
    # Ajman
    ('ajman', 'default', 250, 500, 8.0, 4, 6, None),
]


def seed_uae_benchmarks(apps, schema_editor):
    AuditBenchmark = apps.get_model('audit', 'AuditBenchmark')
    for city, loc_key, ppu_min, ppu_max, yield_pct, appr_pct, liq_months, approved in UAE_BENCHMARKS:
        AuditBenchmark.objects.get_or_create(
            country='AE',
            city=city,
            location_key=loc_key,
            defaults={
                'price_per_unit_min': ppu_min,
                'price_per_unit_max': ppu_max,
                'size_unit': 'sqft',
                'currency': 'AED',
                'yield_pct': yield_pct,
                'appr_pct': appr_pct,
                'liq_months': liq_months,
                'approved': approved,
                'is_active': True,
            },
        )


def remove_uae_benchmarks(apps, schema_editor):
    AuditBenchmark = apps.get_model('audit', 'AuditBenchmark')
    AuditBenchmark.objects.filter(country='AE').delete()


class Migration(migrations.Migration):
    dependencies = [
        ('audit', '0008_rename_comp_viol_type_ts_idx_compliance__violati_65f253_idx_and_more'),
    ]

    operations = [
        migrations.RunPython(seed_uae_benchmarks, remove_uae_benchmarks),
    ]
