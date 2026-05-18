from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('payments', '0002_gateway_fields'),
    ]

    operations = [
        migrations.RenameField(
            model_name='payment',
            old_name='amount_pkr',
            new_name='amount',
        ),
        migrations.AddField(
            model_name='payment',
            name='currency',
            field=models.CharField(
                default='PKR',
                help_text='ISO 4217 currency code for amount, e.g. PKR, AED, USD',
                max_length=3,
            ),
        ),
    ]
