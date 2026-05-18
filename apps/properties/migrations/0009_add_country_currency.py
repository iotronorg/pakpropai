from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('properties', '0008_data_populate_organization'),
    ]

    operations = [
        migrations.AddField(
            model_name='property',
            name='country',
            field=models.CharField(
                default='PK',
                help_text='ISO 3166-1 alpha-2 country code — used for tax/legal rules',
                max_length=2,
            ),
        ),
        migrations.AddField(
            model_name='property',
            name='currency',
            field=models.CharField(
                default='PKR',
                help_text='ISO 4217 currency code for price_pkr field, e.g. PKR, AED, USD',
                max_length=3,
            ),
        ),
    ]
