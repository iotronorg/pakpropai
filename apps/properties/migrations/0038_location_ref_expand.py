"""
Listing → location hierarchy link (T-1060a, ADR-024). Additive and rolling-safe:
a nullable FK (no index here; 0039 builds it concurrently) and a precision
column with a DB default. The FK constraint check scans the table once, and
every value is NULL at that point.
"""
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('locations', '0002_seed_locations'),
        ('properties', '0037_search_tsv_index'),
    ]

    operations = [
        migrations.AddField(
            model_name='property',
            name='location_precision',
            field=models.CharField(choices=[('exact', 'Exact'), ('approximate', 'Approximate')],
                                   db_default='approximate', default='approximate',
                                   help_text='exact = coordinates are the property itself (FR-LST-003)',
                                   max_length=12),
        ),
        migrations.AddField(
            model_name='property',
            name='location_ref',
            field=models.ForeignKey(blank=True, db_index=False, null=True,
                                    on_delete=django.db.models.deletion.SET_NULL,
                                    related_name='listings', to='locations.location'),
        ),
    ]
