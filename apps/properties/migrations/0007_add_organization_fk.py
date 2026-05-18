import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('properties', '0006_property_ref_no'),
        ('organizations', '0002_data_migrate_from_developers'),
    ]

    operations = [
        migrations.AddField(
            model_name='property',
            name='organization',
            field=models.ForeignKey(
                blank=True,
                help_text='Organization that owns this listing (null = individual/freelance listing)',
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='properties',
                to='organizations.organization',
            ),
        ),
        migrations.AddIndex(
            model_name='property',
            index=models.Index(fields=['organization'], name='prop_org_idx'),
        ),
    ]
