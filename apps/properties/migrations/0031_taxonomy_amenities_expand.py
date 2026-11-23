"""
Listing taxonomy + amenities, expand phase (T-1070d, ADR-023 addendum).
Additive and rolling-safe: category/subtype are nullable (pre-v2 inserts omit
them; save() and sync_listing_taxonomy derive category from property_type);
amenities/property_amenities are new tables. The (category, is_active) index
is built CONCURRENTLY in 0034.
"""
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('properties', '0030_backfill_price_history'),
    ]

    operations = [
        migrations.CreateModel(
            name='Amenity',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('key', models.SlugField(help_text='Stable API identifier, e.g. "gym"', unique=True)),
                ('group', models.CharField(choices=[('interior', 'Interior'), ('building', 'Building'), ('outdoor', 'Outdoor'), ('security', 'Security'), ('utilities', 'Utilities'), ('community', 'Community'), ('accessibility', 'Accessibility')], max_length=20)),
                ('labels', models.JSONField(default=dict, help_text='Label per language: {"en": "Gym", "ur": "…"}')),
                ('markets', models.JSONField(blank=True, default=list, help_text='Market codes where it applies; empty = every market')),
                ('sort_order', models.PositiveSmallIntegerField(default=0)),
                ('is_active', models.BooleanField(default=True)),
            ],
            options={
                'db_table': 'amenities',
                'ordering': ['group', 'sort_order', 'key'],
            },
        ),
        migrations.AddField(
            model_name='property',
            name='category',
            field=models.CharField(blank=True, choices=[('residential', 'Residential'), ('commercial', 'Commercial'), ('industrial', 'Industrial'), ('land', 'Land'), ('hospitality', 'Hospitality')], max_length=20, null=True),
        ),
        migrations.AddField(
            model_name='property',
            name='subtype',
            field=models.CharField(blank=True, choices=[('house', 'House'), ('apartment', 'Apartment'), ('upper_portion', 'Upper portion'), ('lower_portion', 'Lower portion'), ('villa', 'Villa'), ('townhouse', 'Townhouse'), ('penthouse', 'Penthouse'), ('farmhouse', 'Farmhouse'), ('studio', 'Studio'), ('room', 'Room'), ('office', 'Office'), ('shop', 'Shop'), ('showroom', 'Showroom'), ('building', 'Building'), ('mixed_use', 'Mixed use'), ('warehouse', 'Warehouse'), ('factory', 'Factory'), ('workshop', 'Workshop'), ('cold_storage', 'Cold storage'), ('residential_plot', 'Residential plot'), ('commercial_plot', 'Commercial plot'), ('industrial_plot', 'Industrial plot'), ('agricultural_land', 'Agricultural land'), ('plot_file', 'Plot file'), ('hotel', 'Hotel'), ('guest_house', 'Guest house'), ('serviced_apartment', 'Serviced apartment'), ('resort', 'Resort')], max_length=40, null=True),
        ),
        migrations.AlterField(
            model_name='property',
            name='property_type',
            field=models.CharField(choices=[('residential', 'Residential'), ('commercial', 'Commercial'), ('plot', 'Plot'), ('industrial', 'Industrial')], default='residential', help_text='Deprecated (T-1070d): derived from category until T-1070g', max_length=30),
        ),
        migrations.CreateModel(
            name='ListingAmenity',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('amenity', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='properties.amenity')),
                ('property', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='listing_amenities', to='properties.property')),
            ],
            options={
                'db_table': 'property_amenities',
            },
        ),
        migrations.AddField(
            model_name='property',
            name='amenities',
            field=models.ManyToManyField(blank=True, related_name='listings', through='properties.ListingAmenity', to='properties.amenity'),
        ),
        migrations.AddIndex(
            model_name='listingamenity',
            index=models.Index(fields=['amenity', 'property'], name='prop_amenity_lookup_idx'),
        ),
        migrations.AddConstraint(
            model_name='listingamenity',
            constraint=models.UniqueConstraint(fields=('property', 'amenity'), name='uniq_property_amenity'),
        ),
    ]
