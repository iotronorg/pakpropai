from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('properties', '0013_rename_prop_owner_type_idx_properties_listing_f44c8e_idx_and_more'),
    ]

    operations = [
        migrations.AddIndex(
            model_name='property',
            index=models.Index(fields=['property_type'], name='properties_prop_type_idx'),
        ),
        migrations.AddIndex(
            model_name='property',
            index=models.Index(fields=['price'], name='properties_price_idx'),
        ),
        migrations.AddIndex(
            model_name='property',
            index=models.Index(fields=['is_active', 'city'], name='properties_active_city_idx'),
        ),
        migrations.AddIndex(
            model_name='property',
            index=models.Index(fields=['is_active', 'created_at'], name='properties_active_created_idx'),
        ),
    ]
