from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('escrow', '0004_escrowdeal_seller_confirmation_token'),
    ]

    operations = [
        migrations.AddField(
            model_name='escrowdeal',
            name='currency',
            field=models.CharField(
                default='PKR',
                help_text='ISO 4217 currency code for token_amount, e.g. PKR, AED, USD',
                max_length=3,
            ),
        ),
    ]
