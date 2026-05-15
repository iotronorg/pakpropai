from django.db import migrations

# Default benchmarks matching the hardcoded values in services.py.
# Rows: (city, location_key, ppm_min, ppm_max, yield_pct, appr_pct, liq_months, approved)
DEFAULT_BENCHMARKS = [
    # Lahore
    ('lahore', 'dha',        4_000_000, 12_000_000, 3.5, 12, 2, True),
    ('lahore', 'bahria',     2_500_000,  5_000_000, 4.5, 15, 2, True),
    ('lahore', 'gulberg',    5_000_000, 15_000_000, 3.0,  8, 3, True),
    ('lahore', 'model town', 3_000_000,  8_000_000, 3.5,  8, 4, True),
    ('lahore', 'johar town', 2_000_000,  4_000_000, 4.0, 10, 3, True),
    ('lahore', 'wapda town', 1_800_000,  3_500_000, 4.5,  9, 4, True),
    ('lahore', 'default',    1_500_000,  3_000_000, 4.0,  8, 5, None),
    # Islamabad
    ('islamabad', 'dha',     6_000_000, 20_000_000, 3.0, 10, 3, True),
    ('islamabad', 'bahria',  3_000_000,  7_000_000, 4.0, 12, 2, True),
    ('islamabad', 'f-7',    10_000_000, 30_000_000, 2.5,  6, 6, True),
    ('islamabad', 'e-7',     8_000_000, 25_000_000, 2.5,  6, 6, True),
    ('islamabad', 'g-11',    3_000_000,  6_000_000, 4.0,  8, 4, True),
    ('islamabad', 'default', 2_000_000,  5_000_000, 4.0,  8, 4, None),
    # Karachi
    ('karachi', 'dha',       5_000_000, 15_000_000, 4.0,  8, 3, True),
    ('karachi', 'bahria',    2_000_000,  5_000_000, 5.0, 10, 3, True),
    ('karachi', 'clifton',   8_000_000, 25_000_000, 3.5,  7, 5, True),
    ('karachi', 'gulshan',   2_000_000,  5_000_000, 4.5,  7, 4, True),
    ('karachi', 'defence',   5_000_000, 15_000_000, 4.0,  8, 3, True),
    ('karachi', 'default',   1_500_000,  4_000_000, 4.5,  7, 5, None),
    # Rawalpindi
    ('rawalpindi', 'bahria',    2_500_000,  6_000_000, 4.5, 12, 3, True),
    ('rawalpindi', 'dha',       4_000_000, 10_000_000, 3.5, 10, 3, True),
    ('rawalpindi', 'satellite', 1_500_000,  3_000_000, 4.5,  8, 5, True),
    ('rawalpindi', 'default',   1_000_000,  2_500_000, 4.5,  8, 6, None),
    # Global fallback
    ('default', 'default',   1_000_000,  3_000_000, 4.0,  8, 6, None),
]


def populate_benchmarks(apps, schema_editor):
    AuditBenchmark = apps.get_model('audit', 'AuditBenchmark')
    for city, loc_key, ppm_min, ppm_max, yield_pct, appr_pct, liq_months, approved in DEFAULT_BENCHMARKS:
        AuditBenchmark.objects.get_or_create(
            city=city,
            location_key=loc_key,
            defaults={
                'ppm_min': ppm_min,
                'ppm_max': ppm_max,
                'yield_pct': yield_pct,
                'appr_pct': appr_pct,
                'liq_months': liq_months,
                'approved': approved,
                'is_active': True,
            },
        )


def depopulate_benchmarks(apps, schema_editor):
    AuditBenchmark = apps.get_model('audit', 'AuditBenchmark')
    cities = {row[0] for row in DEFAULT_BENCHMARKS}
    AuditBenchmark.objects.filter(city__in=cities).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('audit', '0002_auditbenchmark'),
    ]

    operations = [
        migrations.RunPython(populate_benchmarks, depopulate_benchmarks),
    ]
