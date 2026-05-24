from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('organizations', '0016_data_migrate_membership_admin_to_org_admin'),
    ]
    operations = [
        migrations.AddField(
            model_name='organization',
            name='brand_color',
            field=models.CharField(
                blank=True,
                default='#1B4F72',
                help_text='Hex color code for PDF report header, e.g. #1B4F72',
                max_length=7,
            ),
        ),
    ]
