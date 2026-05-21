from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('organizations', '0006_orgpaymentsettings'),
    ]

    operations = [
        migrations.AddField(
            model_name='organization',
            name='language',
            field=models.CharField(
                choices=[
                    ('en', 'English'),
                    ('ar', 'Arabic'),
                    ('ur', 'Urdu'),
                    ('fr', 'French'),
                    ('zh', 'Chinese (Simplified)'),
                    ('es', 'Spanish'),
                ],
                default='en',
                help_text="Preferred AI response language for this org's clients",
                max_length=5,
            ),
        ),
    ]
