"""
Make the cnic field country-agnostic:
- Remove hard Pakistani RegexValidator (validation moved to User.clean())
- Widen max_length from 15 → 30 to accommodate international ID formats
- Update help_text
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0006_backfill_role_client'),
    ]

    operations = [
        migrations.AlterField(
            model_name='user',
            name='cnic',
            field=models.CharField(
                blank=True,
                help_text='National ID number (format varies by country)',
                max_length=30,
                null=True,
            ),
        ),
    ]
