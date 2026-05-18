from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('agents', '0006_data_populate_organization_from_parent'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='agent',
            name='parent_organization',
        ),
    ]
