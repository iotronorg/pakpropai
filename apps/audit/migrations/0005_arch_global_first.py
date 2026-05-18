from django.db import migrations, models


class Migration(migrations.Migration):
    """
    Global-first audit schema:
    - AuditBenchmark: rename ppm_min/max → price_per_unit_min/max;
                      add country, size_unit, currency;
                      expand unique_together to include country.
    - PropertyAudit:  rename area_marla → area_size,
                      estimated_value_pkr → estimated_value;
                      add area_unit, currency.
    """

    dependencies = [
        ('audit', '0004_remove_auditbenchmark_unique_city_location_benchmark_and_more'),
    ]

    operations = [
        # ── AuditBenchmark — field renames (DB column also renamed) ─────────────
        migrations.RenameField(
            model_name='auditbenchmark',
            old_name='ppm_min',
            new_name='price_per_unit_min',
        ),
        migrations.RenameField(
            model_name='auditbenchmark',
            old_name='ppm_max',
            new_name='price_per_unit_max',
        ),

        # ── AuditBenchmark — new fields ─────────────────────────────────────────
        migrations.AddField(
            model_name='auditbenchmark',
            name='country',
            field=models.CharField(
                default='PK',
                help_text='ISO 3166-1 alpha-2 country code, e.g. PK, AE, UK',
                max_length=2,
            ),
        ),
        migrations.AddField(
            model_name='auditbenchmark',
            name='size_unit',
            field=models.CharField(
                choices=[
                    ('marla', 'Marla'), ('kanal', 'Kanal'), ('sqft', 'Square Feet'),
                    ('sqm',   'Square Metre'), ('acre', 'Acre'),
                ],
                default='marla',
                help_text='Unit that price_per_unit_min/max is denominated in',
                max_length=10,
            ),
        ),
        migrations.AddField(
            model_name='auditbenchmark',
            name='currency',
            field=models.CharField(
                default='PKR',
                help_text='ISO 4217 currency code for all price fields in this row',
                max_length=3,
            ),
        ),

        # ── AuditBenchmark — expand unique_together to include country ───────────
        migrations.AlterUniqueTogether(
            name='auditbenchmark',
            unique_together={('country', 'city', 'location_key')},
        ),
        migrations.AddIndex(
            model_name='auditbenchmark',
            index=models.Index(fields=['country', 'city'], name='benchmark_country_city_idx'),
        ),

        # ── PropertyAudit — field renames ───────────────────────────────────────
        migrations.RenameField(
            model_name='propertyaudit',
            old_name='area_marla',
            new_name='area_size',
        ),
        migrations.RenameField(
            model_name='propertyaudit',
            old_name='estimated_value_pkr',
            new_name='estimated_value',
        ),

        # ── PropertyAudit — new fields ──────────────────────────────────────────
        migrations.AddField(
            model_name='propertyaudit',
            name='area_unit',
            field=models.CharField(
                choices=[
                    ('marla', 'Marla'), ('kanal', 'Kanal'), ('sqft', 'Sq Ft'),
                    ('sqm',   'Sq Metre'), ('acre', 'Acre'),
                ],
                default='marla',
                max_length=10,
            ),
        ),
        migrations.AddField(
            model_name='propertyaudit',
            name='currency',
            field=models.CharField(
                default='PKR',
                help_text='ISO 4217 currency code for estimated_value',
                max_length=3,
            ),
        ),
    ]
