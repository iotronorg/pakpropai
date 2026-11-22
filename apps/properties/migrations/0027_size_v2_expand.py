"""Size v2 columns (T-1070b, ADR-023): nullable, additive, rolling-deploy safe."""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('properties', '0026_property_purpose_index'),
    ]

    operations = [
        migrations.AddField(
            model_name='property',
            name='plot_size_m2',
            field=models.DecimalField(blank=True, decimal_places=4, help_text='Plot/land area in m² when it differs from the built size', max_digits=12, null=True),
        ),
        migrations.AddField(
            model_name='property',
            name='size_m2',
            field=models.DecimalField(blank=True, decimal_places=4, help_text='Canonical size in square metres (computed)', max_digits=12, null=True),
        ),
        migrations.AddField(
            model_name='property',
            name='size_unit',
            field=models.CharField(blank=True, choices=[('marla', 'Marla'), ('kanal', 'Kanal'), ('sqft', 'Square Feet'), ('sqm', 'Square Metre'), ('acre', 'Acre'), ('guntha', 'Guntha'), ('cent', 'Cent')], max_length=10, null=True),
        ),
        migrations.AddField(
            model_name='property',
            name='size_value',
            field=models.DecimalField(blank=True, decimal_places=2, help_text='Size in size_unit', max_digits=10, null=True),
        ),
    ]
