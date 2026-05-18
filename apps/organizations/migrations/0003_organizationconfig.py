import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('organizations', '0002_data_migrate_from_developers'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='OrganizationConfig',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('key', models.CharField(db_index=True, max_length=100)),
                ('value', models.CharField(max_length=20)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('organization', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='configs',
                    to='organizations.organization',
                )),
                ('updated_by', models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='org_config_changes',
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                'db_table': 'organization_configs',
                'ordering': ['organization', 'key'],
            },
        ),
        migrations.AlterUniqueTogether(
            name='organizationconfig',
            unique_together={('organization', 'key')},
        ),
    ]
