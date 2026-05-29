from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('properties', '0018_property_geo'),
    ]

    operations = [
        migrations.AddField(
            model_name='property',
            name='is_sandbox',
            field=models.BooleanField(db_index=True, default=True),
        ),
    ]
