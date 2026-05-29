from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('leads', '0017_referral_link'),
    ]

    operations = [
        migrations.AddField(
            model_name='lead',
            name='is_sandbox',
            field=models.BooleanField(db_index=True, default=True),
        ),
    ]
