"""
T-1070g-1 (ADR-023 contract, release 1 of 2).

State: the model forgets area_marla/area_unit/area_sqm/property_type, their
index and the legacy prop_fts_gin expression index. Database: nothing is
dropped; a trigger mirrors v2 → v1 (or v1 → v2 for a write from an older pod
that changed only v1), so pods of the previous release keep reading correct
values during and after the rollout. The DROP is the staged g-2 set
(apps/properties/contract_migrations/), deployed one release later.

The trigger is created inside the migration's transaction; CREATE TRIGGER
takes a SHARE ROW EXCLUSIVE lock for an instant (no table rewrite).
"""
import apps.markets.services
from django.db import migrations, models

from apps.properties.listing_contract import CREATE_TRIGGER_SQL, DROP_TRIGGER_SQL

_STATE = [
    migrations.RemoveIndex(model_name='property', name='properties_propert_f38ecc_idx'),
    migrations.RemoveIndex(model_name='property', name='prop_fts_gin'),
    migrations.RemoveField(model_name='property', name='area_marla'),
    migrations.RemoveField(model_name='property', name='area_sqm'),
    migrations.RemoveField(model_name='property', name='area_unit'),
    migrations.RemoveField(model_name='property', name='property_type'),
    # Python-side defaults only (no DDL): new rows always carry v2 values.
    migrations.AlterField(
        model_name='property', name='category',
        field=models.CharField(blank=True, choices=[
            ('residential', 'Residential'), ('commercial', 'Commercial'), ('industrial', 'Industrial'),
            ('land', 'Land'), ('hospitality', 'Hospitality')],
            default='residential', max_length=20, null=True),
    ),
    migrations.AlterField(
        model_name='property', name='size_unit',
        field=models.CharField(blank=True, choices=[
            ('marla', 'Marla'), ('kanal', 'Kanal'), ('sqft', 'Square Feet'), ('sqm', 'Square Metre'),
            ('acre', 'Acre'), ('guntha', 'Guntha'), ('cent', 'Cent')],
            default=apps.markets.services.default_area_unit, max_length=10, null=True),
    ),
]


class Migration(migrations.Migration):

    dependencies = [
        ('properties', '0039_location_ref_index'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=_STATE,
            database_operations=[migrations.RunSQL(CREATE_TRIGGER_SQL, DROP_TRIGGER_SQL)],
        ),
    ]
