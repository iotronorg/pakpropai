import uuid
import apps.organizations.models
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='Organization',
            fields=[
                ('id',         models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, serialize=False)),
                ('name',       models.CharField(max_length=200)),
                ('slug',       models.SlugField(blank=True, max_length=120, unique=True, help_text='Auto-generated from name — used in URLs and internal references')),
                ('org_type',   models.CharField(choices=[('developer', 'Real Estate Developer'), ('agency', 'Real Estate Agency'), ('brokerage', 'Brokerage Firm'), ('housing_society', 'Housing Society'), ('enterprise', 'Enterprise / Corporate')], default='agency', max_length=20)),
                ('phone',      models.CharField(blank=True, max_length=20)),
                ('email',      models.EmailField(blank=True, max_length=254)),
                ('website',    models.URLField(blank=True)),
                ('logo',       models.ImageField(blank=True, null=True, upload_to=apps.organizations.models._logo_upload_path)),
                ('country',    models.CharField(default='PK', help_text='ISO 3166-1 alpha-2 country code, e.g. PK, AE, UK', max_length=2)),
                ('city',       models.CharField(blank=True, max_length=100)),
                ('address',    models.TextField(blank=True)),
                ('plan',       models.CharField(choices=[('trial', 'Trial'), ('basic', 'Basic'), ('professional', 'Professional'), ('enterprise', 'Enterprise')], default='trial', max_length=20)),
                ('is_active',  models.BooleanField(default=True)),
                ('is_verified', models.BooleanField(default=False, help_text='Platform admin has verified this organization')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('admin_user', models.OneToOneField(
                    blank=True, null=True,
                    help_text='The platform user who administers this organization',
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='owned_organization',
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                'db_table': 'organizations',
                'ordering': ['name'],
            },
        ),
        migrations.AddIndex(
            model_name='organization',
            index=models.Index(fields=['org_type'], name='org_type_idx'),
        ),
        migrations.AddIndex(
            model_name='organization',
            index=models.Index(fields=['country'], name='org_country_idx'),
        ),
        migrations.AddIndex(
            model_name='organization',
            index=models.Index(fields=['is_active'], name='org_active_idx'),
        ),
    ]
