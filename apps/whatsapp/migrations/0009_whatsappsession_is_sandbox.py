from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('whatsapp', '0008_conversation_mode_blocked'),
    ]

    operations = [
        migrations.AddField(
            model_name='whatsappsession',
            name='is_sandbox',
            field=models.BooleanField(db_index=True, default=True),
        ),
    ]
