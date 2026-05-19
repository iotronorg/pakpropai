from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('properties', '0011_arch_global_first'),
    ]

    operations = [
        migrations.RenameField(
            model_name='property',
            old_name='price_pkr',
            new_name='price',
        ),
    ]
